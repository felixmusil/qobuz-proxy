"""Tests for minting Qobuz Connect WebSocket tokens (qws/refreshToken, qws/createToken).

The app's token expires and the app only sends a new one when the user picks the
device again, so the proxy mints its own from the user's auth token.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from qobuz_proxy.auth.api_client import QobuzAPIClient


def _response(status: int, body: dict | None = None):  # type: ignore[no-untyped-def]
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value="denied")
    resp.json = AsyncMock(return_value=body or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _session(*responses):  # type: ignore[no-untyped-def]
    session = AsyncMock()
    session.post = MagicMock(side_effect=list(responses))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _authed_client() -> QobuzAPIClient:
    client = QobuzAPIClient("app123", "secret456")
    client.user_auth_token = "user_token"
    client.user_id = "999"
    client.x_session_id = "sess_abc"
    client.x_session_expires_at = 9_999_999_999_999  # valid; start_session is a no-op
    return client


def _jwt_qws(exp: int) -> dict:
    return {
        "jwt_qws": {
            "jwt": "fresh_jwt",
            "exp": exp,
            "endpoint": "wss%3A%2F%2Fqws-eu-prod.qobuz.com%2Fws",
        }
    }


class TestCreateWsToken:
    async def test_refresh_token_with_user_auth_headers(self) -> None:
        client = _authed_client()
        exp = int(time.time()) + 3600
        session = _session(_response(200, _jwt_qws(exp)))

        with patch("qobuz_proxy.auth.api_client.aiohttp.ClientSession", return_value=session):
            token = await client.create_ws_token()

        assert token is not None
        assert token.jwt == "fresh_jwt"
        assert token.exp_s == exp
        assert token.endpoint == "wss://qws-eu-prod.qobuz.com/ws"  # URL-decoded
        args, kwargs = session.post.call_args
        assert args[0].endswith("/qws/refreshToken")
        assert kwargs["data"] == "jwt=jwt_qws"
        assert kwargs["headers"]["X-User-Auth-Token"] == "user_token"
        assert kwargs["headers"]["X-App-Id"] == "app123"
        assert kwargs["headers"]["X-Session-Id"] == "sess_abc"

    async def test_falls_back_to_create_token(self) -> None:
        client = _authed_client()
        session = _session(_response(401), _response(200, _jwt_qws(int(time.time()) + 3600)))

        with patch("qobuz_proxy.auth.api_client.aiohttp.ClientSession", return_value=session):
            token = await client.create_ws_token()

        assert token is not None and token.jwt == "fresh_jwt"
        urls = [call.args[0] for call in session.post.call_args_list]
        assert urls[0].endswith("/qws/refreshToken")
        assert urls[1].endswith("/qws/createToken")

    async def test_none_when_both_endpoints_fail(self) -> None:
        client = _authed_client()
        session = _session(_response(401), _response(200, {"jwt_qws": {"jwt": "x"}}))

        with patch("qobuz_proxy.auth.api_client.aiohttp.ClientSession", return_value=session):
            assert await client.create_ws_token() is None

    async def test_none_without_user_auth_token(self) -> None:
        client = QobuzAPIClient("app123", "secret456")
        session = _session()

        with patch("qobuz_proxy.auth.api_client.aiohttp.ClientSession", return_value=session):
            assert await client.create_ws_token() is None
        session.post.assert_not_called()
