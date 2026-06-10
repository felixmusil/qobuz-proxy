"""Tests for pluggable standby detection (Hegel + Denon AVR) and selection."""

from typing import Optional

import pytest

from qobuz_proxy.backends.dlna import standby as standby_mod
from qobuz_proxy.backends.dlna.backend import DLNABackend
from qobuz_proxy.backends.dlna.client import DLNADeviceInfo
from qobuz_proxy.backends.dlna.standby import (
    DenonAVRStandbyDetector,
    HegelStandbyDetector,
    StandbyDetector,
    _xml_field,
    select_standby_detector,
)


# --------------------------------------------------------------------------- #
# Detector selection
# --------------------------------------------------------------------------- #
class TestSelectStandbyDetector:
    def test_hegel(self) -> None:
        info = DLNADeviceInfo(manufacturer="Hegel", model_name="H120")
        assert isinstance(select_standby_detector(info, "1.2.3.4"), HegelStandbyDetector)

    def test_denon_avr(self) -> None:
        info = DLNADeviceInfo(manufacturer="Denon", model_name="AVR-X2700H")
        assert isinstance(select_standby_detector(info, "1.2.3.4"), DenonAVRStandbyDetector)

    def test_marantz_avr(self) -> None:
        info = DLNADeviceInfo(manufacturer="Marantz", model_name="SR6015")
        assert isinstance(select_standby_detector(info, "1.2.3.4"), DenonAVRStandbyDetector)

    def test_heos_speaker_uses_heuristic(self) -> None:
        # Always-on HEOS/Home speaker → no detector.
        info = DLNADeviceInfo(manufacturer="Denon", model_name="HEOS 1")
        assert select_standby_detector(info, "1.2.3.4") is None

    def test_sonos_uses_heuristic(self) -> None:
        info = DLNADeviceInfo(manufacturer="Sonos, Inc.", model_name="Play:1")
        assert select_standby_detector(info, "1.2.3.4") is None

    def test_unknown_uses_heuristic(self) -> None:
        info = DLNADeviceInfo(manufacturer="Acme", model_name="Widget")
        assert select_standby_detector(info, "1.2.3.4") is None


# --------------------------------------------------------------------------- #
# Hegel detector
# --------------------------------------------------------------------------- #
class _FakeWriter:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeReader:
    def __init__(self, reply: bytes) -> None:
        self._reply = reply

    async def read(self, n: int) -> bytes:
        return self._reply


class TestHegelStandbyDetector:
    async def _probe(self, monkeypatch: pytest.MonkeyPatch, reply: bytes) -> Optional[bool]:
        writer = _FakeWriter()

        async def fake_open(ip, port):  # type: ignore[no-untyped-def]
            return _FakeReader(reply), writer

        monkeypatch.setattr(standby_mod.asyncio, "open_connection", fake_open)
        result = await HegelStandbyDetector("1.2.3.4").is_in_standby()
        # The control connection must always be closed.
        assert writer.closed is True
        # And the power query must have been sent.
        assert writer.data == b"-p.?\r"
        return result

    async def test_standby(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert await self._probe(monkeypatch, b"-p.0\r") is True

    async def test_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert await self._probe(monkeypatch, b"-p.1\r") is False

    async def test_garbage_reply_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert await self._probe(monkeypatch, b"?!?") is None

    async def test_connection_error_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_open(ip, port):  # type: ignore[no-untyped-def]
            raise OSError("connection refused")

        monkeypatch.setattr(standby_mod.asyncio, "open_connection", fake_open)
        assert await HegelStandbyDetector("1.2.3.4").is_in_standby() is None


# --------------------------------------------------------------------------- #
# Denon AVR detector
# --------------------------------------------------------------------------- #
def _denon_xml(power: str, zone_power: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<item><Power><value>{power}</value></Power>"
        f"<ZonePower><value>{zone_power}</value></ZonePower></item>"
    )


class _FakeResp:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *exc) -> bool:  # type: ignore[no-untyped-def]
        return False

    async def text(self) -> str:
        return self._text


class _FakeSession:
    def __init__(self, resp: object) -> None:
        self._resp = resp

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get(self, url: str):  # type: ignore[no-untyped-def]
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


class TestDenonAVRStandbyDetector:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, resp: object) -> None:
        monkeypatch.setattr(standby_mod.aiohttp, "ClientSession", lambda **kw: _FakeSession(resp))

    async def test_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, _FakeResp(200, _denon_xml("ON", "ON")))
        assert await DenonAVRStandbyDetector("1.2.3.4").is_in_standby() is False

    async def test_standby(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, _FakeResp(200, _denon_xml("STANDBY", "OFF")))
        assert await DenonAVRStandbyDetector("1.2.3.4").is_in_standby() is True

    async def test_zone_off_is_standby(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, _FakeResp(200, _denon_xml("ON", "OFF")))
        assert await DenonAVRStandbyDetector("1.2.3.4").is_in_standby() is True

    async def test_http_error_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, _FakeResp(500, ""))
        assert await DenonAVRStandbyDetector("1.2.3.4").is_in_standby() is None

    async def test_network_error_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, OSError("unreachable"))
        assert await DenonAVRStandbyDetector("1.2.3.4").is_in_standby() is None


class TestXmlField:
    def test_value_child_form(self) -> None:
        assert _xml_field("<item><Power><value>ON</value></Power></item>", "Power") == "ON"

    def test_direct_text_form(self) -> None:
        assert _xml_field("<item><Power>STANDBY</Power></item>", "Power") == "STANDBY"

    def test_missing_field(self) -> None:
        assert _xml_field("<item><Other>x</Other></item>", "Power") is None

    def test_bad_xml(self) -> None:
        assert _xml_field("not xml", "Power") is None


# --------------------------------------------------------------------------- #
# Backend decision: detector verdict overrides the heuristic
# --------------------------------------------------------------------------- #
class _FakeDetector(StandbyDetector):
    name = "fake"

    def __init__(self, verdict: Optional[bool]) -> None:
        self._verdict = verdict

    async def is_in_standby(self) -> Optional[bool]:
        return self._verdict


def _backend(
    position_ms: int, duration_ms: int, detector: Optional[StandbyDetector]
) -> DLNABackend:
    b = DLNABackend.__new__(DLNABackend)
    b._position_ms = position_ms
    b._duration_ms = duration_ms
    b._standby_detector = detector
    return b


class TestClassifyStop:
    async def test_no_detector_near_end_advances(self) -> None:
        # No detector → heuristic; near end → not external (advance).
        b = _backend(235_000, 240_000, None)
        assert await b._classify_stop_is_external() is False

    async def test_no_detector_midtrack_is_external(self) -> None:
        b = _backend(60_000, 240_000, None)
        assert await b._classify_stop_is_external() is True

    async def test_confirmed_standby_overrides_near_end(self) -> None:
        # Detector says standby → external stop even though position is near the end.
        b = _backend(239_000, 240_000, _FakeDetector(True))
        assert await b._classify_stop_is_external() is True

    async def test_detector_unknown_falls_back_to_heuristic(self) -> None:
        b = _backend(235_000, 240_000, _FakeDetector(None))
        assert await b._classify_stop_is_external() is False
