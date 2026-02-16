# LevelPlay MCP Server

MCP server wrapping the [LevelPlay](https://developers.is.com/) (ironSource/Unity) Monetization Reporting API. Built with [FastMCP](https://gofastmcp.com/).

## API Documentation

- [LevelPlay Reporting API](https://docs.unity.com/en-us/grow/levelplay/platform/api/reporting)

## Tools

### `levelplay-report`

Query the monetization reporting API.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `start_date` | Yes | — | Start date (YYYY-MM-DD) |
| `end_date` | Yes | — | End date (YYYY-MM-DD) |
| `metrics` | No | `revenue,impressions,eCPM,activeUsers` | Comma-separated metrics |
| `breakdowns` | No | `date` | Comma-separated breakdowns |
| `filters` | No | — | Dict of filters (appKey, country, adFormat, adNetwork, platform, isBidder, isLevelPlayMediation, abTest, mediationGroup, mediationAdUnitId) |
| `page` | No | — | Page number |
| `results_per_page` | No | — | Results per page |

<details>
<summary>Available metrics (27)</summary>

revenue, impressions, eCPM, clicks, clickThroughRate, sessions, engagedSessions, impressionPerEngagedSessions, impressionsPerSession, activeUsers, revenuePerActiveUser, sessionsPerActiveUser, impressionsPerActiveUser, engagedUsers, revenuePerEngagedUser, impressionsPerEngagedUser, engagedUsersRate, appFills, appFillRate, useRate, appRequests, completions, completionRateImpBased, revenuePerCompletion, adSourceChecks, adSourceResponses, adSourceAvailabilityRate
</details>

<details>
<summary>Available breakdowns (21)</summary>

date, week, month, app, platform, adNetwork, isBidder, adFormat, instance, country, mediationGroup, mediationAdUnit, segment, placement, osVersion, sdkVersion, appVersion, att, idfa, gaid, abTest, isLevelPlayMediation, bannerSize

**Note:** instance, segment, placement, bannerSize, idfa, gaid, att, appVersion, sdkVersion, osVersion cannot be combined with each other.
</details>

### `levelplay-apps`

List all apps on the account. No parameters. Useful for discovering app keys.

## Setup

### Environment variables

| Variable | Description |
|----------|-------------|
| `LEVELPLAY_SECRET_KEY` | Account secret key |
| `LEVELPLAY_REFRESH_TOKEN` | Account refresh token |
| `FASTMCP_SERVER_HOST` | Bind host (default `0.0.0.0`) |
| `FASTMCP_SERVER_PORT` | Bind port (default `8000`) |

### Docker Compose

```yaml
mcp-levelplay:
  build: .
  environment:
    - LEVELPLAY_SECRET_KEY=${LEVELPLAY_SECRET_KEY}
    - LEVELPLAY_REFRESH_TOKEN=${LEVELPLAY_REFRESH_TOKEN}
    - FASTMCP_SERVER_HOST=0.0.0.0
    - FASTMCP_SERVER_PORT=8000
  networks:
    - your-network
```

### MCP client config

```json
{
  "levelplay": {
    "type": "streamable-http",
    "url": "http://mcp-levelplay:8000/mcp/"
  }
}
```

> Trailing slash on `/mcp/` is required — FastMCP returns a 307 redirect without it.

## Authentication

LevelPlay uses 2-step bearer auth. The server handles this automatically:

1. Calls the auth endpoint with your secret key and refresh token to get a JWT (valid 24h)
2. Caches the JWT and auto-refreshes when expired
3. Retries once on 401 (token invalidation mid-flight)

## Rate limits

8,000 requests/hour. 429 errors are surfaced directly in tool responses.
