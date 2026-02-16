# Session 2 — Context/Lifespan Bug Fix

**Date**: 2026-02-16

## Problem

Tools crashed when called via MCP client (Celeste) due to incorrect access to the shared httpx client. Three successive errors:

1. `'FastMCP' object has no attribute 'get_context'` — original code called `mcp.get_context()` which doesn't exist
2. `'Context' object has no attribute 'lifespan_context'` — fixed to use Context injection (`ctx: Context = None`), but `ctx.lifespan_context` doesn't exist in FastMCP 2.14.5 despite being shown in gofastmcp.com docs
3. Found working path `ctx.request_context.lifespan_context["client"]` by reading FastMCP source — but then discovered FastMCP's lifespan runs per-session not per-server, defeating the purpose of connection pooling

## Solution

Dropped FastMCP's lifespan/Context entirely. Replaced with a lazy-initialized module-level httpx client:

```python
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client
```

Tools call `_get_client()` directly — no Context injection, no lifespan, no fragile attribute chains.

## What Changed

| File | Change |
|------|--------|
| `server.py` | Removed lifespan, Context import, `ctx` parameter from tools. Added `_get_client()`. Removed `client` parameter from `_authenticate`, `_get_token`, `_api_get`. |
| `test_server.py` | Removed httpx fixture (tests use `_get_client()` via server internals). Added `server._client = None` to reset fixture. Removed `client` argument from all integration test calls. |

## FastMCP Lifespan Investigation

- **FastMCP 2.14.5** is the latest stable release (confirmed via PyPI)
- **gofastmcp.com docs show `ctx.lifespan_context`** — this attribute does not exist on the `Context` class in 2.14.5. Verified by inspecting the installed source.
- **The actual path is `ctx.request_context.lifespan_context`** — found by tracing through `Context` → `RequestContext` (from `mcp.shared.context`) → `lifespan_context` field
- **Lifespan runs per-session, not per-server** — confirmed by GitHub issues [#775](https://github.com/jlowin/fastmcp/issues/775) and [#1115](https://github.com/jlowin/fastmcp/issues/1115). The maintainer acknowledged this is by design.
- **Community consensus**: use module-level state for server-lifetime resources

## Implementation Notes & Mistakes

1. **Three attempts to access httpx client** — `mcp.get_context()` → `ctx.lifespan_context` → `ctx.request_context.lifespan_context`. First was a guess without checking docs. Second was from docs that don't match the code. Third was from reading source. Should have read source from the start.
2. **Event loop closed in tests** — module-level `httpx.AsyncClient()` gets bound to the import-time event loop, which pytest closes between tests. Fixed with lazy init via `_get_client()` and resetting `_client = None` in test fixture.

## Verification
- Docker image builds cleanly
- All 8 tests pass (3 unit + 5 integration)
- Pushed to GitHub
