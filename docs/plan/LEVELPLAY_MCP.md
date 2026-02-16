# LevelPlay MCP Server — Architecture

## Overview
Lightweight MCP server wrapping the LevelPlay (ironSource/Unity) Monetization Reporting API. Runs as a Docker container on an internal network, consumed via streamable-http transport.

## Stack
- Python 3.11, FastMCP (v2.x), httpx
- Transport: streamable-http
- Docker: python:3.11-slim

## Authentication
LevelPlay uses 2-step bearer auth:
1. `GET /partners/publisher/auth` with `secretkey` + `refreshToken` headers → raw JWT (24h validity)
2. All subsequent requests use `Authorization: Bearer <jwt>`

JWT is cached in module-level vars. Expiry is parsed from the JWT `exp` claim (base64-decoded payload). Auto-refreshes on expiry or 401.

## Tools

### `levelplay-report`
Queries `GET /levelPlay/reporting/v1`. Parameters:
- `start_date`, `end_date` (required)
- `metrics` (default: revenue,impressions,eCPM,activeUsers)
- `breakdowns` (default: date)
- `filters` (dict, optional — keys: appKey, country, adFormat, adNetwork, platform, isBidder, mediationGroup, mediationAdUnitId)
- `page`, `results_per_page` (optional)

### `levelplay-apps`
Lists all apps. `GET /partners/publisher/applications/v6`. No parameters.

## Design Decisions
1. **Single file** — Two tools + auth ≈ 140 lines. No package needed.
2. **httpx via lifespan** — Shared async client with connection pooling, cleaned up on shutdown.
3. **JWT exp parsing** — Base64-decode the JWT payload to read `exp`. More robust than hardcoded TTL.
4. **401 retry** — On 401, clear cache, re-auth, retry once. Handles token invalidation mid-flight.
5. **Env vars** — `LEVELPLAY_SECRET_KEY`, `LEVELPLAY_REFRESH_TOKEN` for auth. `FASTMCP_SERVER_HOST`/`FASTMCP_SERVER_PORT` for bind config.

## Rate Limits
8,000 requests/hour. No client-side throttling — 429 errors surfaced directly.

## Docker Usage
```yaml
mcp-levelplay:
  build: .
  environment:
    - LEVELPLAY_SECRET_KEY=${LEVELPLAY_SECRET_KEY}
    - LEVELPLAY_REFRESH_TOKEN=${LEVELPLAY_REFRESH_TOKEN}
    - FASTMCP_SERVER_HOST=0.0.0.0
    - FASTMCP_SERVER_PORT=8000
  networks:
    - celeste
```

MCP client config:
```json
{
  "levelplay": {
    "type": "streamable-http",
    "url": "http://mcp-levelplay:8000/mcp/"
  }
}
```
