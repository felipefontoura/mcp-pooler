"""mcp-pooler — a caching connection pooler for an aggregating MCP gateway.

Problem
-------
Aggregating MCP gateways (e.g. MetaMCP) are convenient control planes — a catalog of
MCP servers grouped into a namespace, managed from a UI. But their data plane is slow
for *ephemeral* clients: they re-aggregate ``tools/list`` across every backend on each
call, and often cold-spawn a backend set per client session. Short-lived MCP clients
(for example a per-task agent that waits a fraction of a second for tool discovery)
never see the tools in time and silently degrade.

What this does
--------------
Decouples the connection lifecycle from the client lifecycle:

* holds **one persistent upstream session** to the gateway namespace — backends stay
  warm, no per-client cold-spawn, no process leak,
* **caches ``tools/list``** so downstream discovery is instant,
* **proxies ``tools/call``** to the warm session,
* refreshes the cache in the background, so catalog changes upstream show up
  automatically.

The upstream gateway stays the source of truth for the catalog. This pooler is a thin,
invisible accelerator in front of it.

Configuration (environment)
---------------------------
  UPSTREAM_URL   upstream MCP endpoint (streamable-HTTP), e.g. https://host/.../mcp
  UPSTREAM_KEY   bearer token for the upstream (optional; omit for unauthenticated)
  REFRESH_SEC    tools/list cache refresh interval in seconds (default 45)
  POOLER_HOST    bind host (default 0.0.0.0)
  POOLER_PORT    bind port (default 9100)

Endpoints
---------
  /mcp     streamable-HTTP MCP endpoint for downstream clients
  /health  200 once the upstream session is connected and tools are cached, else 503
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mcp-pooler")

UPSTREAM_URL = os.environ["UPSTREAM_URL"]
UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "")
HEADERS = {"Authorization": f"Bearer {UPSTREAM_KEY}"} if UPSTREAM_KEY else {}
REFRESH_SEC = int(os.environ.get("REFRESH_SEC", "45"))
HOST = os.environ.get("POOLER_HOST", "0.0.0.0")
PORT = int(os.environ.get("POOLER_PORT", "9100"))


class Upstream:
    """Owns the single persistent session to the upstream gateway and the tool cache."""

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.tools: list = []
        self.ready = asyncio.Event()

    async def run(self) -> None:
        # One session, kept alive; the tool cache is refreshed periodically. On any
        # failure the session is torn down and reconnected.
        while True:
            try:
                client = streamablehttp_client(UPSTREAM_URL, headers=HEADERS)
                async with client as (read, write, *_):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.session = session
                        self.tools = (await session.list_tools()).tools
                        self.ready.set()
                        log.info("upstream connected — %d tools cached", len(self.tools))
                        while True:
                            await asyncio.sleep(REFRESH_SEC)
                            try:
                                self.tools = (await session.list_tools()).tools
                            except Exception as exc:
                                log.warning("tools refresh failed (%s) — reconnecting", exc)
                                break
            except Exception as exc:
                self.ready.clear()
                self.session = None
                log.warning("upstream down (%s) — retrying in 2s", exc)
                await asyncio.sleep(2)


upstream = Upstream()
server = Server("mcp-pooler")


@server.list_tools()
async def list_tools():
    return upstream.tools  # served from cache — instant


@server.call_tool()
async def call_tool(name, arguments):
    # If the upstream session is mid-reconnect (e.g. the gateway restarted), wait
    # briefly for it to come back instead of failing the call outright.
    if upstream.session is None:
        try:
            await asyncio.wait_for(upstream.ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            raise RuntimeError("upstream session not ready after 30s")
    result = await upstream.session.call_tool(name, arguments)
    # Preserve structured output so tools that declare an outputSchema validate downstream.
    if getattr(result, "structuredContent", None) is not None:
        return result.content, result.structuredContent
    return result.content


session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=False)


async def handle_mcp(scope, receive, send):
    await session_manager.handle_request(scope, receive, send)


async def health(_request):
    if upstream.ready.is_set():
        return JSONResponse({"status": "ok", "tools": len(upstream.tools)})
    return JSONResponse({"status": "starting"}, status_code=503)


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with session_manager.run():
        task = asyncio.create_task(upstream.run())
        # Bounded wait for the first cache fill so early clients don't see an empty list.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(upstream.ready.wait(), timeout=20)
        try:
            yield
        finally:
            task.cancel()


app = Starlette(
    routes=[Route("/health", health), Mount("/mcp", app=handle_mcp)],
    lifespan=lifespan,
)


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
