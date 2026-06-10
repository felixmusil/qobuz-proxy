"""Tests for distinguishing a natural track end from an external stop.

When a DLNA renderer reports PLAYING->STOPPED, qobuz-proxy must only treat it as
a finished track (and auto-advance) if it stopped near the track's end. A stop
well before the end means the renderer was stopped/powered off, and advancing
would re-issue Play and wake the device.
"""

from qobuz_proxy.backends.dlna.backend import DLNABackend, TRACK_END_POSITION_THRESHOLD_MS


def _backend(position_ms: int, duration_ms: int) -> DLNABackend:
    backend = DLNABackend.__new__(DLNABackend)
    backend._position_ms = position_ms
    backend._duration_ms = duration_ms
    return backend


class TestIsNaturalTrackEnd:
    def test_near_end_is_natural(self) -> None:
        # Stopped ~5s before a 240s track ends → finished track.
        assert _backend(235_000, 240_000)._is_natural_track_end() is True

    def test_exactly_at_threshold_is_natural(self) -> None:
        assert _backend(240_000 - TRACK_END_POSITION_THRESHOLD_MS, 240_000)._is_natural_track_end()

    def test_midtrack_is_external_stop(self) -> None:
        # Stopped at 1:00 of a 4:00 track → renderer was stopped / powered off.
        assert _backend(60_000, 240_000)._is_natural_track_end() is False

    def test_unknown_duration_preserves_advance(self) -> None:
        # Duration unknown → keep prior behaviour (treat as natural end / advance).
        assert _backend(0, 0)._is_natural_track_end() is True
