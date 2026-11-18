"""Tests for LevelPlay MCP Server."""

import os
from datetime import date, timedelta

import pytest

import server

# ---------------------------------------------------------------------------
# Test JWT tokens (not cryptographically valid, but structurally correct)
# ---------------------------------------------------------------------------

# LevelPlay format: expirationTime claim
TOKEN_EXPIRATION_TIME = (
    "eyJ0eXAiOiAiSldUIiwgImFsZyI6ICJIUzI1NiJ9"
    ".eyJzZWNyZXRLZXkiOiAiYWJjIiwgInJlZnJlc2hUb2tlbiI6ICJkZWYiLCAiZXhwaXJhdGlvblRpbWUiOiAxNzAwMDAwMDAwfQ"
    ".ZmFrZXNpZw"
)

# Standard JWT format: exp claim
TOKEN_EXP = (
    "eyJ0eXAiOiAiSldUIiwgImFsZyI6ICJIUzI1NiJ9"
    ".eyJzdWIiOiAidGVzdCIsICJleHAiOiAxODAwMDAwMDAwfQ"
    ".ZmFrZXNpZw"
)


# ---------------------------------------------------------------------------
# Unit tests — JWT parsing
# ---------------------------------------------------------------------------


class TestParseJwtExp:
    def test_expirationTime_claim(self):
        result = server._parse_jwt_exp(TOKEN_EXPIRATION_TIME)
        assert result == 1700000000.0

    def test_exp_claim(self):
        result = server._parse_jwt_exp(TOKEN_EXP)
        assert result == 1800000000.0

    def test_returns_float(self):
        result = server._parse_jwt_exp(TOKEN_EXPIRATION_TIME)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Unit tests — collection as facts, network errors as data
# ---------------------------------------------------------------------------


ROW = {"date": "2026-11-16", "appName": "Fluffy Fall: Fly Fast to Dodge (Android)", "appKey": "1b62c3a9d",
       "platform": "Android", "revenue": 389.48, "activeUsers": 50473, "revenuePerActiveUser": 0.77,
       "impressions": 137406, "eCPM": 2.83}


class TestGameSlug:
    def test_one_name_across_platforms(self):
        assert server.game_slug("Fluffy Fall: Fly Fast to Dodge (Android)") == "fluffy-fall"
        assert server.game_slug("Fluffy Fall (iOS)") == "fluffy-fall"
        assert server.game_slug("Idle marble run (Android)") == "idle-marble-run"

    def test_empty_name(self):
        assert server.game_slug("") == "app"


class TestRowsToFacts:
    def test_one_fact_per_game_platform_day(self):
        [fact] = server.rows_to_facts([ROW])
        assert fact["key"] == "levelplay/fluffy-fall/android" and fact["date"] == "2026-11-16"
        assert fact["data"]["revenue"] == 389.48 and fact["data"]["dau"] == 50473
        assert fact["data"]["arpdau"] == 0.00772 and fact["data"]["ecpm"] == 2.83

    def test_arpdau_without_users(self):
        [fact] = server.rows_to_facts([{**ROW, "activeUsers": 0}])
        assert fact["data"]["arpdau"] is None

    def test_two_apps_with_one_name_stay_apart_whatever_the_order(self):
        other = {**ROW, "appName": "Fluffy Fall (Android)", "appKey": "0ABC"}
        for rows in ([ROW, other], [other, ROW]):
            owners = {f["key"]: f["data"]["app_key"] for f in server.rows_to_facts(rows)}
            assert owners == {"levelplay/fluffy-fall/android": "0ABC",
                              "levelplay/fluffy-fall/android-1b62c3a9d": "1b62c3a9d"}

    def test_uppercase_app_key_makes_a_valid_fact_key(self):
        other = {**ROW, "appName": "Fluffy Fall (Android)", "appKey": "ZZZ"}
        keys = {f["key"] for f in server.rows_to_facts([ROW, other])}
        assert "levelplay/fluffy-fall/android-zzz" in keys


class TestCollect:
    @pytest.mark.asyncio
    async def test_reads_every_page(self, monkeypatch):
        pages = []

        async def fake_get(url, params=None):
            pages.append(params["page"])
            rows = [{**ROW, "date": f"2026-11-1{params['page']}"}]
            return {"page": params["page"], "pageSize": 1, "totalResults": 2, "data": rows}
        monkeypatch.setattr(server, "_api_get", fake_get)
        result = await server.levelplay_collect(days=2)
        assert pages == [1, 2] and len(result["facts"]) == 2

    @pytest.mark.asyncio
    async def test_error_passed_through(self, monkeypatch):
        async def fake_get(url, params=None):
            return {"error": "API returned 500"}
        monkeypatch.setattr(server, "_api_get", fake_get)
        assert await server.levelplay_collect() == {"error": "API returned 500"}

    @pytest.mark.asyncio
    async def test_network_failure_is_a_readable_error(self, monkeypatch):
        async def timeout(*args, **kwargs):
            raise server.httpx.ReadTimeout("")
        monkeypatch.setattr(server, "_get_token", timeout)
        result = await server._api_get(server.REPORT_URL)
        assert result == {"error": "LevelPlay unreachable: ReadTimeout (no message)"}


# ---------------------------------------------------------------------------
# Integration tests — require real credentials
# ---------------------------------------------------------------------------

HAVE_CREDS = bool(
    os.environ.get("LEVELPLAY_SECRET_KEY")
    and os.environ.get("LEVELPLAY_REFRESH_TOKEN")
)
skip_no_creds = pytest.mark.skipif(
    not HAVE_CREDS, reason="LEVELPLAY_SECRET_KEY and LEVELPLAY_REFRESH_TOKEN not set"
)


@pytest.fixture(autouse=True)
def reset_state():
    """Clear cached JWT and HTTP client between tests."""
    server._jwt_token = None
    server._jwt_expiry = 0.0
    server._client = None


@skip_no_creds
class TestIntegrationAuth:
    @pytest.mark.asyncio
    async def test_authenticate_returns_jwt(self):
        token = await server._authenticate()
        assert token
        assert token.count(".") == 2  # valid JWT structure

    @pytest.mark.asyncio
    async def test_token_is_cached(self):
        token1 = await server._get_token()
        token2 = await server._get_token()
        assert token1 == token2
        assert server._jwt_token is not None


@skip_no_creds
class TestIntegrationApps:
    @pytest.mark.asyncio
    async def test_list_apps(self):
        await server._authenticate()
        result = await server._api_get(server.APPS_URL)
        assert isinstance(result, (list, dict))


@skip_no_creds
class TestIntegrationReport:
    @pytest.mark.asyncio
    async def test_basic_report(self):
        await server._authenticate()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        params = {
            "startDate": yesterday,
            "endDate": yesterday,
            "metrics": "revenue,impressions",
            "breakdowns": "date",
        }
        result = await server._api_get(server.REPORT_URL, params)
        assert "data" in result
        assert "page" in result

    @pytest.mark.asyncio
    async def test_report_with_filter(self):
        await server._authenticate()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        params = {
            "startDate": yesterday,
            "endDate": yesterday,
            "metrics": "revenue",
            "breakdowns": "date",
            "platform": "android",
        }
        result = await server._api_get(server.REPORT_URL, params)
        assert "data" in result
