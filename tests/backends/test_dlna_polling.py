"""Tests that the DLNA poll loop is the only reader of renderer state.

Every SOAP call is a fresh TCP connection. The player checks state and position
every 0.5s; when that went to the device too, a Hegel H120 saw ~5 connections a
second. get_state()/get_position() must serve the poll loop's cached values.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from qobuz_proxy.backends.dlna import backend as backend_module
from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.types import PlaybackState


def _backend() -> DLNABackend:
    backend = DLNABackend(ip="1.2.3.4")
    client = AsyncMock()
    client.get_transport_info = AsyncMock(return_value="PLAYING")
    client.get_position_info = AsyncMock(return_value=42_000)
    backend._client = client
    return backend


async def test_state_and_position_reads_do_not_touch_the_device() -> None:
    backend = _backend()
    backend._state = PlaybackState.PLAYING
    backend._position_ms = 1_000

    for _ in range(20):
        assert await backend.get_state() == PlaybackState.PLAYING
        assert await backend.get_position() == 1_000

    backend._client.get_transport_info.assert_not_awaited()  # type: ignore[union-attr]
    backend._client.get_position_info.assert_not_awaited()  # type: ignore[union-attr]


async def test_poll_loop_reads_the_device_and_fills_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "STATE_POLL_INTERVAL_SECONDS", 0)
    backend = _backend()
    backend._is_connected = True

    task = asyncio.create_task(backend._poll_state_loop())
    await asyncio.sleep(0.01)
    backend._is_connected = False
    await asyncio.wait_for(task, timeout=1.0)

    assert await backend.get_state() == PlaybackState.PLAYING
    assert await backend.get_position() == 42_000
    client = backend._client
    assert client is not None
    # One state and one position query per cycle, nothing else.
    assert client.get_transport_info.await_count == client.get_position_info.await_count  # type: ignore[attr-defined]
