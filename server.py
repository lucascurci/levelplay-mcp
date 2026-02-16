"""LevelPlay MCP Server — wraps the ironSource/Unity Monetization Reporting API."""

import base64
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastmcp import Context, FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LEVELPLAY_SECRET_KEY = os.environ.get("LEVELPLAY_SECRET_KEY", "")
LEVELPLAY_REFRESH_TOKEN = os.environ.get("LEVELPLAY_REFRESH_TOKEN", "")

BASE_URL = "https://platform.ironsrc.com"
AUTH_URL = f"{BASE_URL}/partners/publisher/auth"
REPORT_URL = f"{BASE_URL}/levelPlay/reporting/v1"
APPS_URL = f"{BASE_URL}/partners/publisher/applications/v6"

DEFAULT_METRICS = "revenue,impressions,eCPM,activeUsers"
DEFAULT_BREAKDOWNS = "date"

# ---------------------------------------------------------------------------
# Auth — JWT cache
# ---------------------------------------------------------------------------

_jwt_token: str | None = None
_jwt_expiry: float = 0.0


def _parse_jwt_exp(token: str) -> float:
    """Extract exp claim from a JWT without a crypto library."""
    payload_b64 = token.split(".")[1]
    # Add padding
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return float(payload.get("exp") or payload["expirationTime"])


async def _authenticate(client: httpx.AsyncClient) -> str:
    """Call the auth endpoint and cache the JWT."""
    global _jwt_token, _jwt_expiry

    resp = await client.get(
        AUTH_URL,
        headers={
            "secretkey": LEVELPLAY_SECRET_KEY,
            "refreshToken": LEVELPLAY_REFRESH_TOKEN,
        },
    )
    resp.raise_for_status()

    token = resp.text.strip().strip('"')
    _jwt_token = token
    _jwt_expiry = _parse_jwt_exp(token) - 60  # 1-min safety margin
    return token


async def _get_token(client: httpx.AsyncClient) -> str:
    """Return a valid JWT, refreshing if expired."""
    if _jwt_token and time.time() < _jwt_expiry:
        return _jwt_token
    return await _authenticate(client)


async def _api_get(
    client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None
) -> Any:
    """GET with bearer auth. Retries once on 401."""
    token = await _get_token(client)

    resp = await client.get(
        url, params=params, headers={"Authorization": f"Bearer {token}"}
    )

    if resp.status_code == 401:
        token = await _authenticate(client)
        resp = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}
        )

    if resp.status_code == 429:
        return {"error": "Rate limited (429). LevelPlay allows 8000 requests/hour."}

    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(server):
    async with httpx.AsyncClient(timeout=30) as client:
        yield {"client": client}


mcp = FastMCP(
    "LevelPlay",
    instructions=(
        "LevelPlay ad monetization reporting server. "
        "Use levelplay-apps to discover app keys, "
        "then levelplay-report to query revenue, impressions, eCPM, and other metrics "
        "by date, app, country, ad format, ad network, and more. "
        "Dates are YYYY-MM-DD. Metrics and breakdowns are comma-separated strings."
    ),
    lifespan=lifespan,
)


@mcp.tool(name="levelplay-report")
async def levelplay_report(
    start_date: str,
    end_date: str,
    metrics: str = DEFAULT_METRICS,
    breakdowns: str = DEFAULT_BREAKDOWNS,
    filters: dict[str, str] | None = None,
    page: int | None = None,
    results_per_page: int | None = None,
    ctx: Context = None,
) -> dict:
    """Query the LevelPlay monetization reporting API.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        metrics: Comma-separated metrics (default: revenue,impressions,eCPM,activeUsers).
        breakdowns: Comma-separated breakdowns (default: date).
        filters: Optional dict of filters (appKey, country, adFormat, adNetwork, platform, isBidder, isLevelPlayMediation, abTest, mediationGroup, mediationAdUnitId).
        page: Page number for pagination.
        results_per_page: Results per page.
    """
    client: httpx.AsyncClient = ctx.lifespan_context["client"]

    params: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
        "breakdowns": breakdowns,
    }

    if filters:
        for key, value in filters.items():
            params[key] = value

    if page is not None:
        params["page"] = page
    if results_per_page is not None:
        params["resultsPerPage"] = results_per_page

    return await _api_get(client, REPORT_URL, params)


@mcp.tool(name="levelplay-apps")
async def levelplay_apps(ctx: Context = None) -> dict:
    """List all apps on the LevelPlay account. Useful for discovering app keys."""
    client: httpx.AsyncClient = ctx.lifespan_context["client"]
    return await _api_get(client, APPS_URL)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("FASTMCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("FASTMCP_SERVER_PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port)
