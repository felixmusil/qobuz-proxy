"""Tests that a session snapshot replayed on WebSocket rejoin doesn't pause playback.

Every (re)join makes the Qobuz server replay its last-known state, typically PAUSED
at the position from just before the drop. A token-renewal reconnect takes well under
a second, so that replay is too close to the real position for the gap rule alone;
obeying it seeked the renderer and paused it an hour into every session.
"""

import time
from unittest.mock import AsyncMock, MagicMock

from qobuz_proxy.backends import PlaybackState
from qobuz_proxy.playback.player import QobuzPlayer

TRACK_ID = "85131518"
PAUSED = 3


def _playing_player() -> tuple[QobuzPlayer, MagicMock]:
    backend = MagicMock()
    backend.pause = AsyncMock()
    backend.seek = AsyncMock()
    player = QobuzPlayer(queue=MagicMock(), metadata_service=MagicMock(), backend=backend)
    track = MagicMock()
    track.track_id = TRACK_ID
    player._current_track = track
    player._state = PlaybackState.PLAYING
    player._position_value_ms = 172_000
    player._position_timestamp_ms = int(time.time() * 1000)
    player._state_update_callback = AsyncMock()
    return player, backend


async def _replay_paused(player: QobuzPlayer) -> None:
    await player.apply_remote_state(
        track_id=TRACK_ID, queue_item_id=8, position_ms=170_427, playing_state=PAUSED
    )


async def test_paused_replay_right_after_rejoin_is_ignored() -> None:
    player, backend = _playing_player()
    player.note_session_joined()

    await _replay_paused(player)

    assert player._state == PlaybackState.PLAYING
    backend.pause.assert_not_awaited()
    backend.seek.assert_not_awaited()
    # The server now thinks we're paused: report PLAYING right away
    player._state_update_callback.assert_awaited_once()  # type: ignore[union-attr]


async def test_pause_without_rejoin_is_obeyed() -> None:
    player, backend = _playing_player()

    await _replay_paused(player)

    assert player._state == PlaybackState.PAUSED
    backend.pause.assert_awaited_once()


async def test_only_the_first_pause_after_rejoin_is_ignored() -> None:
    player, backend = _playing_player()
    player.note_session_joined()

    await _replay_paused(player)
    await _replay_paused(player)  # the user pausing a moment later

    assert player._state == PlaybackState.PAUSED
    backend.pause.assert_awaited_once()


async def test_pause_long_after_rejoin_is_obeyed() -> None:
    player, backend = _playing_player()
    player.note_session_joined()
    player._session_joined_at = time.monotonic() - 60

    await _replay_paused(player)

    assert player._state == PlaybackState.PAUSED
