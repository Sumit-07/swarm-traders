"""Tests for Kite Connect authentication flow."""

import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.kite_auth import (
    TOKEN_FILE,
    _KiteCallbackHandler,
    _load_todays_token,
    _save_token,
    force_reauthenticate,
)


@pytest.fixture
def tmp_token_file(tmp_path):
    """Use a temporary token file for tests."""
    token_file = tmp_path / "kite_token.json"
    with patch("tools.kite_auth.TOKEN_FILE", token_file):
        yield token_file


class TestLoadTodaysToken:
    def test_returns_token_if_exists_today(self, tmp_token_file):
        """Token from today should be returned."""
        token_data = {
            "access_token": "test_token_123",
            "generated_at": datetime.now().isoformat(),
            "generated_date": datetime.now().strftime("%Y-%m-%d"),
        }
        tmp_token_file.write_text(json.dumps(token_data))

        result = _load_todays_token()
        assert result == "test_token_123"

    def test_returns_none_for_stale_token(self, tmp_token_file):
        """Token from yesterday should return None."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        token_data = {
            "access_token": "old_token",
            "generated_at": datetime.now().isoformat(),
            "generated_date": yesterday,
        }
        tmp_token_file.write_text(json.dumps(token_data))

        result = _load_todays_token()
        assert result is None

    def test_returns_none_if_file_missing(self, tmp_token_file):
        """Missing token file should return None."""
        assert not tmp_token_file.exists()
        result = _load_todays_token()
        assert result is None

    def test_handles_corrupt_json(self, tmp_token_file):
        """Corrupt JSON should be deleted and return None."""
        tmp_token_file.write_text("not valid json {{{")

        result = _load_todays_token()
        assert result is None
        assert not tmp_token_file.exists()


class TestCallbackHandler:
    def test_captures_request_token(self):
        """GET with request_token param should set request_token and return 200."""
        _KiteCallbackHandler.request_token = None
        _KiteCallbackHandler.received = False

        handler = _KiteCallbackHandler.__new__(_KiteCallbackHandler)
        handler.path = "/callback?request_token=ABC123&status=success"
        handler.wfile = io.BytesIO()
        handler.requestline = "GET /callback HTTP/1.1"
        handler.request_version = "HTTP/1.1"

        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_GET()

        assert _KiteCallbackHandler.request_token == "ABC123"
        assert _KiteCallbackHandler.received is True
        handler.send_response.assert_called_with(200)

    def test_returns_404_for_unknown_path(self):
        """GET without request_token should return 404."""
        _KiteCallbackHandler.request_token = None
        _KiteCallbackHandler.received = False

        handler = _KiteCallbackHandler.__new__(_KiteCallbackHandler)
        handler.path = "/some/other/path"
        handler.wfile = io.BytesIO()
        handler.requestline = "GET /some/other/path HTTP/1.1"
        handler.request_version = "HTTP/1.1"

        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler.do_GET()

        assert _KiteCallbackHandler.request_token is None
        assert _KiteCallbackHandler.received is False
        handler.send_response.assert_called_with(404)


class TestTotpCode:
    def test_totp_code_matches_expected_format(self):
        """pyotp should generate a 6-digit code."""
        import pyotp

        totp = pyotp.TOTP("JBSWY3DPEHPK3PXP")
        code = totp.now()
        assert len(code) == 6
        assert code.isdigit()


class TestSaveToken:
    def test_creates_file_with_correct_date(self, tmp_token_file):
        """_save_token should create a file with today's date."""
        _save_token("my_access_token")
        assert tmp_token_file.exists()

        data = json.loads(tmp_token_file.read_text())
        assert data["access_token"] == "my_access_token"
        assert data["generated_date"] == datetime.now().strftime("%Y-%m-%d")


class TestForceReauthenticate:
    def test_deletes_existing_token(self, tmp_token_file):
        """force_reauthenticate should delete the existing token file."""
        tmp_token_file.write_text(json.dumps({
            "access_token": "old",
            "generated_date": datetime.now().strftime("%Y-%m-%d"),
        }))

        with patch("tools.kite_auth.load_or_refresh_token") as mock_load:
            mock_kite = MagicMock()
            mock_load.return_value = mock_kite
            force_reauthenticate(telegram=MagicMock())

        assert not tmp_token_file.exists()
        mock_load.assert_called_once()
