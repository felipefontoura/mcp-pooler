# Contributing

Thanks for your interest! `mcp-pooler` is intentionally tiny — one file, a few deps.

## Dev loop

```bash
# run against a real upstream
UPSTREAM_URL=https://your-gateway/.../mcp UPSTREAM_KEY=... \
  uv run --with mcp --with uvicorn --with starlette python pooler.py

# lint
uvx ruff check .

# build the image
docker build -t mcp-pooler:dev .
```

## Guidelines

- Keep it small and dependency-light (`mcp`, `uvicorn`, `starlette`). New runtime deps
  need a good reason.
- The two invariants that make this work: **one persistent upstream session** and a
  **cached `tools/list`**. Don't regress either (a per-request upstream session defeats
  the whole point).
- Match the existing style; `ruff` settings live in `pyproject.toml`.

## Reporting issues

Include your upstream gateway (e.g. MetaMCP version), the client, and what
`GET /health` returns. Latency numbers (`tools/list` / `tools/call`) help a lot.
