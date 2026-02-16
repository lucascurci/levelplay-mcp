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
