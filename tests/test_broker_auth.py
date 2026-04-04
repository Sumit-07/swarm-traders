"""Tests for Fyers OAuth2 authentication flow."""

import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.broker import (
    TOKEN_FILE,
    _FyersCallbackHandler,
    load_or_refresh_token,
)


@pytest.fixture
def tmp_token_file(tmp_path):
    """Use a temporary token file for tests."""
    token_file = tmp_path / "fyers_token.json"
    with patch("tools.broker.TOKEN_FILE", token_file):
        yield token_file


class TestLoadOrRefreshToken:
    def test_reuses_todays_token(self, tmp_token_file):
        """Token from today should be reused without auth flow."""
        token_data = {
            "access_token": "test_token_123",
            "generated_at": datetime.now().isoformat(),
            "generated_date": datetime.now().strftime("%Y-%m-%d"),
        }
        tmp_token_file.write_text(json.dumps(token_data))

        telegram = MagicMock()

        with patch("tools.broker._run_auth_flow") as mock_auth:
            result = load_or_refresh_token(telegram)

        mock_auth.assert_not_called()
        assert result == "test_token_123"
        telegram.send_message.assert_called_once()
        assert "Reusing" in telegram.send_message.call_args[0][0]

    def test_triggers_auth_for_stale_token(self, tmp_token_file):
        """Token from yesterday should trigger full auth flow."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        token_data = {
            "access_token": "old_token",
            "generated_at": datetime.now().isoformat(),
            "generated_date": yesterday,
        }
        tmp_token_file.write_text(json.dumps(token_data))

        telegram = MagicMock()

        with patch("tools.broker._run_auth_flow", return_value="new_token") as mock_auth:
            result = load_or_refresh_token(telegram)

        mock_auth.assert_called_once_with(telegram)
        assert result == "new_token"

    def test_triggers_auth_for_missing_file(self, tmp_token_file):
        """Missing token file should trigger full auth flow."""
        assert not tmp_token_file.exists()

        telegram = MagicMock()

        with patch("tools.broker._run_auth_flow", return_value="fresh_token") as mock_auth:
            result = load_or_refresh_token(telegram)

        mock_auth.assert_called_once_with(telegram)
        assert result == "fresh_token"

    def test_handles_corrupt_file(self, tmp_token_file):
        """Corrupt JSON should be deleted and auth flow triggered."""
        tmp_token_file.write_text("not valid json {{{")

        telegram = MagicMock()

        with patch("tools.broker._run_auth_flow", return_value="recovered_token") as mock_auth:
            result = load_or_refresh_token(telegram)

        mock_auth.assert_called_once_with(telegram)
        assert result == "recovered_token"
        # Corrupt file should have been deleted
        assert not tmp_token_file.exists()


class TestCallbackHandler:
    def test_captures_auth_code(self):
        """GET with auth_code param should set auth_code and return 200."""
        # Reset state
        _FyersCallbackHandler.auth_code = None
        _FyersCallbackHandler.received = False

        handler = _FyersCallbackHandler.__new__(_FyersCallbackHandler)
        handler.path = "/callback?auth_code=ABC123&state=test"
        handler.wfile = io.BytesIO()
        handler.requestline = "GET /callback HTTP/1.1"
        handler.request_version = "HTTP/1.1"

        # Mock response methods
        headers_sent = []
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_GET()

        assert _FyersCallbackHandler.auth_code == "ABC123"
        assert _FyersCallbackHandler.received is True
        handler.send_response.assert_called_with(200)

    def test_ignores_unknown_requests(self):
        """GET without auth_code should return 404 and not set auth_code."""
        _FyersCallbackHandler.auth_code = None
        _FyersCallbackHandler.received = False

        handler = _FyersCallbackHandler.__new__(_FyersCallbackHandler)
        handler.path = "/some/other/path"
        handler.wfile = io.BytesIO()
        handler.requestline = "GET /some/other/path HTTP/1.1"
        handler.request_version = "HTTP/1.1"

        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_GET()

        assert _FyersCallbackHandler.auth_code is None
        assert _FyersCallbackHandler.received is False
        handler.send_response.assert_called_with(404)
