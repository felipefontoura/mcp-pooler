# mcp-pooler

[![publish image](https://github.com/felipefontoura/mcp-pooler/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/felipefontoura/mcp-pooler/actions/workflows/docker-publish.yml)
[![lint](https://github.com/felipefontoura/mcp-pooler/actions/workflows/lint.yml/badge.svg)](https://github.com/felipefontoura/mcp-pooler/actions/workflows/lint.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A tiny **caching connection pooler** that sits in front of an aggregating MCP gateway
(e.g. [MetaMCP](https://github.com/metatool-ai/metamcp)) so **ephemeral MCP clients get
instant tool discovery and warm tool calls**.

## The problem

Aggregating MCP gateways are great as a **control plane** — a catalog of MCP servers,
grouped into a namespace, managed from a UI. But their **data plane** is slow for
short-lived clients:

- they **re-aggregate `tools/list`** across every backend on each call, and
- they often **cold-spawn a backend set per client session** (and leak the processes).

An ephemeral MCP client — say a per-task agent that waits a fraction of a second for
tool discovery before its first turn — never sees the tools in time. It silently
degrades (falls back to shell, reports "no tools", etc.).

> Measured against a real MetaMCP namespace (7 backends): a warm single backend answers
> `tools/list` in **~10 ms**, but through the gateway it took **~1.8 s warm / ~27 s cold**
> — the cost is the gateway's per-call aggregation and per-session spawn, not the
> backends.

## What it does

`mcp-pooler` decouples the **connection** lifecycle from the **client** lifecycle:

1. holds **one persistent upstream session** to the gateway namespace → backends stay
   warm, **no per-client cold-spawn, no leak** (the gateway only ever sees one session);
2. **caches `tools/list`** → downstream discovery is **instant** (~50 ms);
3. **proxies `tools/call`** to the warm session (~130 ms);
4. **refreshes the cache** in the background, so MCPs you add in the gateway's admin show
   up automatically.

The gateway stays the **source of truth** for the catalog. The pooler is a thin,
invisible accelerator — whoever manages the catalog never touches it.

```
   ephemeral MCP clients                 mcp-pooler                 aggregating gateway
  (e.g. per-task agents)         ┌───────────────────────┐         (e.g. MetaMCP)
        │  tools/list  ──────────▶  served from cache  ◀──┼── 1 warm session ──▶ backends
        │  tools/call  ──────────▶  proxied to session ◀──┘                       (warm)
        ▼  ~50ms discovery, ~130ms calls
```

## Quick start

```bash
docker run --rm -p 9100:9100 \
  -e UPSTREAM_URL="https://your-gateway/metamcp/<namespace>/mcp" \
  -e UPSTREAM_KEY="<bearer-token>" \
  ghcr.io/felipefontoura/mcp-pooler:latest
```

Point your MCP client at `http://localhost:9100/mcp/`. Health: `GET /health`.

Or with Compose (see [`compose.example.yml`](compose.example.yml)):

```bash
cp .env.example .env   # fill UPSTREAM_URL / UPSTREAM_KEY
docker compose -f compose.example.yml up -d
```

## Configuration

| env | default | meaning |
|---|---|---|
| `UPSTREAM_URL` | — (required) | upstream MCP endpoint, streamable-HTTP (`…/mcp`) |
| `UPSTREAM_KEY` | _(none)_ | bearer token for the upstream (omit if unauthenticated) |
| `REFRESH_SEC` | `45` | how often the tool cache is refreshed from upstream |
| `POOLER_HOST` | `0.0.0.0` | bind host |
| `POOLER_PORT` | `9100` | bind port |

## Endpoints

| path | method | purpose |
|---|---|---|
| `/mcp` | POST/GET | streamable-HTTP MCP endpoint for downstream clients |
| `/health` | GET | `200 {"status":"ok","tools":N}` once warm, else `503` |

## Why not just a transport bridge?

A plain MCP proxy / transport bridge (e.g. opening a new upstream session per request)
**does not** solve this — it re-pays the gateway's cold-spawn and aggregation on every
call (observed spikes of tens of seconds). The two things that matter are: **one
persistent upstream session** (warm backends) and a **`tools/list` cache** (instant
discovery). That's all this is.

## Development

```bash
uv run --with mcp --with uvicorn --with starlette python pooler.py
# or:  pip install -e .  &&  python pooler.py
```

Requires Python ≥ 3.11. The runtime deps are `mcp`, `uvicorn`, `starlette`.

## Notes & hardening

- One upstream session is shared by all downstream clients; the MCP `ClientSession`
  multiplexes by request id. Add a concurrency cap if a single backend gets hammered.
- The downstream `/mcp` endpoint has no auth — intended to run on a private network.
  Put it behind a reverse proxy / network policy if exposed.
- Run 2+ replicas behind a load balancer to remove the single point of failure (each
  replica holds its own warm upstream session).

## License

MIT © Felipe Fontoura
