"""Tests for operator.gateway_client — RevkaGatewayClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.gateway_client import RevkaGatewayClient


class TestGatewayClientInit:
    def test_disabled_without_url(self):
        with patch.dict("os.environ", {}, clear=True):
            gw = RevkaGatewayClient()
            assert not gw._available

    def test_disabled_without_httpx(self):
        with patch.dict("os.environ", {"REVKA_GATEWAY_URL": "http://localhost:8080"}), \
             patch("operator_mcp.gateway_client._HAS_HTTPX", False):
            gw = RevkaGatewayClient()
            assert not gw._available

    def test_enabled(self):
        with patch.dict("os.environ", {"REVKA_GATEWAY_URL": "http://localhost:8080"}), \
             patch("operator_mcp.gateway_client._HAS_HTTPX", True):
            gw = RevkaGatewayClient()
            assert gw._available
            assert gw.gateway_url == "http://localhost:8080"

    def test_strips_trailing_slash(self):
        with patch.dict("os.environ", {"REVKA_GATEWAY_URL": "http://localhost:8080/"}), \
             patch("operator_mcp.gateway_client._HAS_HTTPX", True):
            gw = RevkaGatewayClient()
            assert gw.gateway_url == "http://localhost:8080"

    def test_headers_with_service_token(self):
        with patch.dict("os.environ", {
            "REVKA_GATEWAY_URL": "http://localhost:8080",
            "REVKA_SERVICE_TOKEN": "svc-token",
        }, clear=True), patch("operator_mcp.gateway_client._HAS_HTTPX", True):
            gw = RevkaGatewayClient()
            headers = gw._headers()
            assert headers["X-Revka-Service-Token"] == "svc-token"
            assert headers["Accept"] == "application/json"
            assert "Authorization" not in headers

    def test_headers_without_token(self):
        with patch.dict("os.environ", {
            "REVKA_GATEWAY_URL": "http://localhost:8080",
        }, clear=True), \
             patch("operator_mcp.gateway_client._HAS_HTTPX", True), \
             patch("operator_mcp.gateway_client._read_service_token", return_value=""):
            gw = RevkaGatewayClient()
            headers = gw._headers()
            assert "X-Revka-Service-Token" not in headers
            assert "Authorization" not in headers


class TestGatewayClientMethods:
    @pytest.mark.asyncio
    async def test_get_cost_summary_unavailable(self):
        with patch.dict("os.environ", {}, clear=True):
            gw = RevkaGatewayClient()
            result = await gw.get_cost_summary()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_status_unavailable(self):
        with patch.dict("os.environ", {}, clear=True):
            gw = RevkaGatewayClient()
            result = await gw.get_status()
            assert result is None

    @pytest.mark.asyncio
    async def test_push_channel_event_unavailable(self):
        with patch.dict("os.environ", {}, clear=True):
            gw = RevkaGatewayClient()
            result = await gw.push_channel_event({"type": "test"})
            assert result is False
