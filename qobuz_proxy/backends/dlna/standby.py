"""Per-device standby/power detection for DLNA renderers.

When a renderer reports PLAYING->STOPPED, qobuz-proxy must decide whether the
track finished (auto-advance) or the device was stopped/powered off (don't
advance — that would re-issue Play and wake the device). Some devices expose a
real power API; querying it is authoritative. Devices we don't recognise (or
that never standby, like Sonos) fall back to the position heuristic in the
backend.

A detector returns:
  True  -> confirmed in standby / powered off
  False -> confirmed on
  None  -> unknown / unreachable (caller uses the heuristic)
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiohttp import ClientTimeout

if TYPE_CHECKING:
    from .client import DLNADeviceInfo

logger = logging.getLogger(__name__)

# Hegel IP control protocol (TCP). `-p.?` queries power; reply is `-p.1` (on) or
# `-p.0` (standby). The amp answers on this port while in network standby.
HEGEL_CONTROL_PORT = 50001
PROBE_TIMEOUT_SECONDS = 2.0


class StandbyDetector(ABC):
    """Queries a renderer's real power/standby state."""

    name: str = "standby-detector"

    @abstractmethod
    async def is_in_standby(self) -> Optional[bool]:
        """True if confirmed standby/off, False if confirmed on, None if unknown."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any held resources (no-op by default)."""
        return None


class HegelStandbyDetector(StandbyDetector):
    """Standby detection for Hegel amplifiers via the IP control protocol."""

    def __init__(self, ip: str, port: int = HEGEL_CONTROL_PORT):
        self._ip = ip
        self._port = port
        self.name = f"Hegel({ip})"

    async def is_in_standby(self) -> Optional[bool]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._ip, self._port),
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.debug(f"Hegel power probe failed for {self._ip}: {type(e).__name__}: {e}")
            return None

        try:
            writer.write(b"-p.?\r")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(64), timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.debug(f"Hegel power probe failed for {self._ip}: {type(e).__name__}: {e}")
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        reply = data.decode(errors="ignore")
        if "-p.0" in reply:
            return True  # standby
        if "-p.1" in reply:
            return False  # on
        logger.debug(f"Hegel power probe: unrecognised reply {reply!r}")
        return None


class DenonAVRStandbyDetector(StandbyDetector):
    """Standby detection for Denon/Marantz AVRs via the HTTP status endpoint."""

    STATUS_PATH = "/goform/formMainZone_MainZoneXmlStatusLite.xml"

    def __init__(self, ip: str):
        self._ip = ip
        self._url = f"http://{ip}{self.STATUS_PATH}"
        self.name = f"DenonAVR({ip})"

    async def is_in_standby(self) -> Optional[bool]:
        try:
            timeout = ClientTimeout(total=PROBE_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._url) as resp:
                    if resp.status != 200:
                        return None
                    body = await resp.text()
        except Exception as e:
            logger.debug(f"Denon power probe failed for {self._ip}: {type(e).__name__}: {e}")
            return None

        power = _xml_field(body, "Power")
        zone_power = _xml_field(body, "ZonePower")
        if power is None and zone_power is None:
            logger.debug("Denon power probe: no Power/ZonePower in response")
            return None
        # Available to play in the main zone only when both are ON.
        on = (power is None or power.upper() == "ON") and (
            zone_power is None or zone_power.upper() == "ON"
        )
        return not on


def _xml_field(xml_text: str, tag: str) -> Optional[str]:
    """Read a Denon status field, tolerating both <Tag>v</Tag> and <Tag><value>v</value></Tag>."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    el = root.find(tag)
    if el is None:
        return None
    value = el.find("value")
    text = value.text if value is not None else el.text
    return text.strip() if text else None


def select_standby_detector(device_info: "DLNADeviceInfo", ip: str) -> Optional[StandbyDetector]:
    """Pick a standby detector by device type, or None to use the heuristic.

    | Manufacturer / model        | Detector              | Why                         |
    | --------------------------- | --------------------- | --------------------------- |
    | Hegel                       | HegelStandbyDetector  | TCP 50001 power query        |
    | Denon / Marantz AVR         | DenonAVRStandbyDetector | HTTP status Power/ZonePower |
    | Sonos, HEOS/Home speakers   | None (heuristic)      | always-on, never standby     |
    | anything else               | None (heuristic)      | unknown                      |
    """
    manufacturer = (device_info.manufacturer or "").lower()
    model = (device_info.model_name or "").lower()

    if "hegel" in manufacturer:
        return HegelStandbyDetector(ip)

    if "denon" in manufacturer or "marantz" in manufacturer:
        # HEOS / Denon Home wireless speakers are always-on; only AVRs standby
        # and wake on play. Exclude the obvious speaker lines.
        is_speaker = "heos" in model or "home" in model
        if not is_speaker:
            return DenonAVRStandbyDetector(ip)

    return None
