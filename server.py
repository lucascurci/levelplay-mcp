"""LevelPlay MCP Server — wraps the ironSource/Unity Monetization Reporting API."""

import base64
import json
import os
import re
import time
from datetime import date, timedelta
from typing import Any

import httpx
from fastmcp import FastMCP

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
# HTTP client & Auth — JWT cache
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client

_jwt_token: str | None = None
_jwt_expiry: float = 0.0


def _parse_jwt_exp(token: str) -> float:
    """Extract exp claim from a JWT without a crypto library."""
    payload_b64 = token.split(".")[1]
    # Add padding
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return float(payload.get("exp") or payload["expirationTime"])


async def _authenticate() -> str:
    """Call the auth endpoint and cache the JWT."""
    global _jwt_token, _jwt_expiry

    resp = await _get_client().get(
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


async def _get_token() -> str:
    """Return a valid JWT, refreshing if expired."""
    if _jwt_token and time.time() < _jwt_expiry:
        return _jwt_token
    return await _authenticate()


async def _api_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET with bearer auth. Retries once on 401.

    Network failures (timeouts, refused or dropped connections) come back as an error
    dict naming the exception: an httpx timeout has an empty message, so raised it
    reached the caller as a bare "Error calling tool".
    """
    try:
        token = await _get_token()
        client = _get_client()

        resp = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}
        )

        if resp.status_code == 401:
            token = await _authenticate()
            resp = await client.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as e:
        detail = str(e) or "no message"
        return {"error": f"LevelPlay unreachable: {type(e).__name__} ({detail})"}

    if resp.status_code == 429:
        return {"error": "Rate limited (429). LevelPlay allows 8000 requests/hour."}

    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"error": f"API returned {resp.status_code}", "details": body}

    return resp.json()


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "LevelPlay",
    instructions=(
        "LevelPlay ad monetization reporting server. "
        "Use levelplay-apps to discover app keys, "
        "then levelplay-report to query revenue, impressions, eCPM, and other metrics "
        "by date, app, country, ad format, ad network, and more. "
        "Dates are YYYY-MM-DD. Metrics and breakdowns are comma-separated strings. "
        "levelplay-collect is called by Celeste's collector, not in conversation: its daily "
        "numbers are already in her fact store under levelplay/."
    ),
)


# ---------------------------------------------------------------------------
# Collection — the standard fact shape for Celeste's collectors
# ---------------------------------------------------------------------------

COLLECT_METRICS = "revenue,activeUsers,revenuePerActiveUser,impressions,eCPM"
COLLECT_PAGE_SIZE = 500


def game_slug(app_name: str) -> str:
    """One name per game across platforms: "Fluffy Fall: Fly Fast to Dodge (Android)" and
    "Fluffy Fall (iOS)" are both "fluffy-fall"."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", app_name or "")
    name = name.split(":", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def rows_to_facts(rows: list) -> list:
    """Report rows (date, app, platform) as facts: one per game, platform and day."""
    found, owners = {}, {}
    # Rows in app-key order: when two apps reduce to one name, the same one keeps the plain key
    # on every run, whatever order the API answers in
    for row in sorted(rows, key=lambda r: (str(r.get("appKey") or ""), str(r.get("date") or ""))):
        platform = re.sub(r"[^a-z0-9]+", "-", str(row.get("platform") or "unknown").lower()).strip("-")
        key = f"levelplay/{game_slug(row.get('appName', ''))}/{platform}"
        app_key = row.get("appKey") or ""
        if owners.setdefault(key, app_key) != app_key:
            # two apps reduce to one name: keep them apart, in the lowercase a fact key allows
            key = f"{key}-{re.sub(r'[^a-z0-9]+', '-', app_key.lower()).strip('-')}"
        revenue, dau = row.get("revenue"), row.get("activeUsers")
        found[(key, row["date"])] = {
            "key": key, "date": row["date"],
            # Computed: the API's revenuePerActiveUser is 100 times revenue / activeUsers
            "data": {"revenue": revenue, "dau": dau,
                     "arpdau": round(revenue / dau, 5) if revenue is not None and dau else None,
                     "impressions": row.get("impressions"), "ecpm": row.get("eCPM"),
                     "app_name": row.get("appName"), "app_key": app_key}}
    return list(found.values())


@mcp.tool(name="levelplay-collect")
async def levelplay_collect(days: int = 3) -> dict:
    """Daily numbers per game and platform for the last `days` full days (yesterday back),
    as facts {key: "levelplay/{game}/{platform}", date, data: revenue, dau, arpdau,
    impressions, ecpm}. Re-reading recent days catches LevelPlay's late revisions.

    days: 1 to 31.
    """
    days = max(1, min(int(days), 31))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    rows, page = [], 1
    while True:
        result = await _api_get(REPORT_URL, {
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "metrics": COLLECT_METRICS, "breakdowns": "date,app,platform",
            "page": page, "resultsPerPage": COLLECT_PAGE_SIZE})
        if not isinstance(result, dict) or "error" in result:
            return result if isinstance(result, dict) else {"error": "unexpected answer", "details": str(result)[:300]}
        rows.extend(result.get("data") or [])
        if page * int(result.get("pageSize") or COLLECT_PAGE_SIZE) >= int(result.get("totalResults") or 0):
            break
        page += 1
    return {"facts": rows_to_facts(rows)}


@mcp.tool(name="levelplay-report")
async def levelplay_report(
    start_date: str,
    end_date: str,
    metrics: str = DEFAULT_METRICS,
    breakdowns: str = DEFAULT_BREAKDOWNS,
    filters: dict[str, str] | None = None,
    page: int | None = None,
    results_per_page: int | None = None,
) -> dict:
    """Query the LevelPlay monetization reporting API.

    Args:
        start_date: Start date (YYYY-MM-DD). Must not be in the future.
        end_date: End date (YYYY-MM-DD). Must not be in the future.
        metrics: Comma-separated metrics. Available: revenue, impressions, eCPM, clicks,
            clickThroughRate, sessions, engagedSessions, impressionPerEngagedSessions,
            impressionsPerSession, activeUsers, revenuePerActiveUser, sessionsPerActiveUser,
            impressionsPerActiveUser, engagedUsers, revenuePerEngagedUser,
            impressionsPerEngagedUser, engagedUsersRate, appFills, appFillRate, useRate,
            appRequests, completions, completionRateImpBased, revenuePerCompletion,
            adSourceChecks, adSourceResponses, adSourceAvailabilityRate.
        breakdowns: Comma-separated breakdowns. Available: date, week, month, app, platform,
            adNetwork, isBidder, adFormat, instance, country, mediationGroup, mediationAdUnit,
            segment, placement, osVersion, sdkVersion, appVersion, att, idfa, gaid, abTest,
            isLevelPlayMediation, bannerSize.
            NOTE: instance, segment, placement, bannerSize, idfa, gaid, att, appVersion,
            sdkVersion, osVersion cannot be combined with each other.
        filters: Optional dict to filter results. Keys: appKey, country, adFormat, adNetwork,
            platform, isBidder, isLevelPlayMediation, abTest, mediationGroup,
            mediationAdUnitId. Example: {"appKey": "1a2b3c", "platform": "iOS"}.
        page: Page number for pagination.
        results_per_page: Results per page.
    """
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

    return await _api_get(REPORT_URL, params)


@mcp.tool(name="levelplay-apps")
async def levelplay_apps() -> dict:
    """List all apps on the LevelPlay account.

    Returns a dict with an "apps" key containing a list of app objects.
    Each app has: appKey, appName, appStatus, platform, bundleId, adUnits, networkList.
    Use the appKey values as filters in levelplay-report.
    """
    result = await _api_get(APPS_URL)
    if isinstance(result, list):
        return {"apps": result}
    return result


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("FASTMCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("FASTMCP_SERVER_PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port)
