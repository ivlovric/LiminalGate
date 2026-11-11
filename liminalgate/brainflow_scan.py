"""BrainFlow board list and a BLE scan for the Setup panel.

BrainFlow does not expose a Python scan. Nearby headsets are read with bleak,
then simplepyble (the same SimpleBLE stack BrainFlow links). Docker's bridge
network cannot see the host adapter; that case returns a hint to use
``compose.host.yaml``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

logger = logging.getLogger("liminalgate.brainflow")

BLUETOOTH_HINT = "Bluetooth isn't reachable from this container. Run with compose.host.yaml."

# BrainFlow 5.22.2 BoardIds, used when the package is not installed.
# NO_BOARD, playback, and the streaming board are not headset choices.
_FALLBACK_IDS: tuple[tuple[str, int], ...] = (
    ("SYNTHETIC_BOARD", -1),
    ("CYTON_BOARD", 0),
    ("GANGLION_BOARD", 1),
    ("CYTON_DAISY_BOARD", 2),
    ("GALEA_BOARD", 3),
    ("GANGLION_WIFI_BOARD", 4),
    ("CYTON_WIFI_BOARD", 5),
    ("CYTON_DAISY_WIFI_BOARD", 6),
    ("BRAINBIT_BOARD", 7),
    ("UNICORN_BOARD", 8),
    ("CALLIBRI_EEG_BOARD", 9),
    ("CALLIBRI_EMG_BOARD", 10),
    ("CALLIBRI_ECG_BOARD", 11),
    ("NOTION_1_BOARD", 13),
    ("NOTION_2_BOARD", 14),
    ("GFORCE_PRO_BOARD", 16),
    ("FREEEEG32_BOARD", 17),
    ("BRAINBIT_BLED_BOARD", 18),
    ("GFORCE_DUAL_BOARD", 19),
    ("MUSE_S_BLED_BOARD", 21),
    ("MUSE_2_BLED_BOARD", 22),
    ("CROWN_BOARD", 23),
    ("ANT_NEURO_EE_410_BOARD", 24),
    ("ANT_NEURO_EE_411_BOARD", 25),
    ("ANT_NEURO_EE_430_BOARD", 26),
    ("ANT_NEURO_EE_211_BOARD", 27),
    ("ANT_NEURO_EE_212_BOARD", 28),
    ("ANT_NEURO_EE_213_BOARD", 29),
    ("ANT_NEURO_EE_214_BOARD", 30),
    ("ANT_NEURO_EE_215_BOARD", 31),
    ("ANT_NEURO_EE_221_BOARD", 32),
    ("ANT_NEURO_EE_222_BOARD", 33),
    ("ANT_NEURO_EE_223_BOARD", 34),
    ("ANT_NEURO_EE_224_BOARD", 35),
    ("ANT_NEURO_EE_225_BOARD", 36),
    ("ENOPHONE_BOARD", 37),
    ("MUSE_2_BOARD", 38),
    ("MUSE_S_BOARD", 39),
    ("BRAINALIVE_BOARD", 40),
    ("MUSE_2016_BOARD", 41),
    ("MUSE_2016_BLED_BOARD", 42),
    ("EXPLORE_4_CHAN_BOARD", 44),
    ("EXPLORE_8_CHAN_BOARD", 45),
    ("GANGLION_NATIVE_BOARD", 46),
    ("EMOTIBIT_BOARD", 47),
    ("NTL_WIFI_BOARD", 50),
    ("ANT_NEURO_EE_511_BOARD", 51),
    ("FREEEEG128_BOARD", 52),
    ("AAVAA_V3_BOARD", 53),
    ("EXPLORE_PLUS_8_CHAN_BOARD", 54),
    ("EXPLORE_PLUS_32_CHAN_BOARD", 55),
    ("PIEEG_BOARD", 56),
    ("NEUROPAWN_KNIGHT_BOARD", 57),
    ("SYNCHRONI_TRIO_3_CHANNELS_BOARD", 58),
    ("SYNCHRONI_OCTO_8_CHANNELS_BOARD", 59),
    ("OB5000_8_CHANNELS_BOARD", 60),
    ("SYNCHRONI_PENTO_8_CHANNELS_BOARD", 61),
    ("SYNCHRONI_UNO_1_CHANNELS_BOARD", 62),
    ("OB3000_24_CHANNELS_BOARD", 63),
    ("BIOLISTENER_BOARD", 64),
    ("IRONBCI_32_BOARD", 65),
    ("NEUROPAWN_KNIGHT_BOARD_IMU", 66),
    ("MUSE_S_ATHENA_BOARD", 67),
)

# Stored config values for the boards the rest of the app already names.
_VALUE_FOR_ENUM = {
    "SYNTHETIC_BOARD": "synthetic",
    "MUSE_S_ATHENA_BOARD": "muse_s_athena",
    "MUSE_2_BOARD": "muse_2",
    "MUSE_S_BOARD": "muse_s",
    "MUSE_2016_BOARD": "muse_2016",
}
_PREFERRED = ("synthetic", "muse_s_athena", "muse_s", "muse_2", "muse_2016")

# Other BrainFlow BLE boards, matched as a substring of the advertised name.
_OTHER: tuple[tuple[str, str, str, int], ...] = (
    ("ganglion", "GANGLION_BOARD", "GANGLION_BOARD", 1),
    ("brainbit", "BRAINBIT_BOARD", "BRAINBIT_BOARD", 7),
    ("callibri", "CALLIBRI_EEG_BOARD", "CALLIBRI_EEG_BOARD", 9),
    ("enophone", "ENOPHONE_BOARD", "ENOPHONE_BOARD", 37),
    ("notion", "NOTION_2_BOARD", "NOTION_2_BOARD", 14),
    ("gforce", "GFORCE_PRO_BOARD", "GFORCE_PRO_BOARD", 16),
    ("galea", "GALEA_BOARD", "GALEA_BOARD", 3),
)

_UNREACHABLE = (
    "bluetooth",
    "bluez",
    "dbus",
    "org.bluez",
    "adapter",
    "not authorized",
    "permission denied",
    "access denied",
)


class BluetoothUnreachable(Exception):
    """The process cannot open a host Bluetooth adapter."""


def list_boards() -> list[dict[str, object]]:
    """One row per BrainFlow board the Setup dropdown can offer."""

    pairs = _board_pairs()
    rows = []
    for enum_name, board_id in pairs:
        if enum_name in {"NO_BOARD", "PLAYBACK_FILE_BOARD", "STREAMING_BOARD"}:
            continue
        value = _VALUE_FOR_ENUM.get(enum_name, enum_name)
        rows.append(
            {
                "value": value,
                "enum": enum_name,
                "board_id": int(board_id),
                "label": _board_label(enum_name, int(board_id)),
            }
        )
    order = {name: index for index, name in enumerate(_PREFERRED)}
    rows.sort(key=lambda row: (order.get(str(row["value"]), len(order)), str(row["label"])))
    return rows


def classify_headset(name: str) -> dict[str, object] | None:
    """Map an advertised BLE name to a BrainFlow board, or None if it is not one."""

    cleaned = " ".join(name.split())
    if not cleaned:
        return None
    compact = cleaned.lower().replace(" ", "").replace("_", "").replace("-", "")
    if "athena" in compact or compact.startswith("muses"):
        note = ""
        if "athena" not in compact:
            note = (
                "Muse S and Athena both advertise as MuseS. "
                "This selects MUSE_S_ATHENA_BOARD (67); choose muse_s for an older Muse S."
            )
        return _hit(cleaned, "muse_s_athena", 67, "MUSE_S_ATHENA_BOARD", note)
    if compact.startswith("muse"):
        return _hit(
            cleaned,
            "muse_2",
            38,
            "MUSE_2_BOARD",
            "Muse 2 and Muse 2016 share this name. This selects MUSE_2_BOARD (38); "
            "choose muse_2016 for a 2016 headset.",
        )
    for needle, board, enum_name, board_id in _OTHER:
        if needle in compact:
            return _hit(cleaned, board, board_id, enum_name, "")
    return None


def scan_headsets(
    timeout: float = 5.0,
    scanner: Callable[[float], list[dict]] | None = None,
) -> dict[str, object]:
    """Scan for compatible headsets. ``scanner`` is a test hook."""

    try:
        raw = scanner(timeout) if scanner is not None else _discover(timeout)
    except BluetoothUnreachable:
        return {"available": False, "devices": [], "error": BLUETOOTH_HINT}
    except Exception as exc:
        logger.info("ble_scan_failed", extra={"error": str(exc)})
        if _looks_unreachable(exc):
            return {"available": False, "devices": [], "error": BLUETOOTH_HINT}
        return {"available": False, "devices": [], "error": str(exc)}
    devices: list[dict[str, object]] = []
    for row in raw:
        hit = classify_headset(str(row.get("name") or ""))
        if hit is None:
            continue
        rssi = row.get("rssi")
        devices.append(
            {
                "name": hit["name"],
                "mac": _format_mac(str(row.get("mac") or "")),
                "rssi": int(rssi) if isinstance(rssi, int | float) else None,
                "serial": hit["serial"],
                "board": hit["board"],
                "board_id": hit["board_id"],
                "board_label": hit["board_label"],
                "note": hit["note"],
            }
        )
    devices.sort(key=lambda item: (item["rssi"] is None, -(int(item["rssi"] or 0)), str(item["name"])))
    return {"available": True, "devices": devices, "error": None}


def _hit(name: str, board: str, board_id: int, enum_name: str, note: str) -> dict[str, object]:
    live = _live_id(enum_name)
    return {
        "name": name,
        "serial": name,
        "board": board,
        "board_id": live if live is not None else board_id,
        "board_label": enum_name,
        "note": note,
    }


def _live_id(enum_name: str) -> int | None:
    enum = _import_board_ids()
    if enum is None or not hasattr(enum, enum_name):
        return None
    return int(getattr(enum, enum_name))


def _board_pairs() -> list[tuple[str, int]]:
    enum = _import_board_ids()
    if enum is None:
        return list(_FALLBACK_IDS)
    return [(name, int(member)) for name, member in enum.__members__.items()]


def _import_board_ids() -> object | None:
    try:
        from brainflow.board_shim import BoardIds
    except Exception:
        return None
    return BoardIds


def _board_label(enum_name: str, board_id: int) -> str:
    words = enum_name.removesuffix("_BOARD").replace("_", " ").title()
    return f"{words} · {enum_name} ({board_id})"


def _format_mac(value: str) -> str:
    text = value.strip().upper().replace("-", ":")
    hexes = re.findall(r"[0-9A-F]{2}", text)
    if len(hexes) == 6:
        return ":".join(hexes)
    return text


def _looks_unreachable(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(needle in text for needle in _UNREACHABLE)


def _discover(timeout: float) -> list[dict]:
    missing = 0
    last: Exception | None = None
    for attempt in (_discover_bleak, _discover_simpleble):
        try:
            return attempt(timeout)
        except ImportError:
            missing += 1
        except BluetoothUnreachable:
            raise
        except Exception as exc:
            if _looks_unreachable(exc):
                raise BluetoothUnreachable(BLUETOOTH_HINT) from exc
            last = exc
    if missing == 2 or last is None:
        raise BluetoothUnreachable(BLUETOOTH_HINT)
    raise last


def _discover_bleak(timeout: float) -> list[dict]:
    from bleak import BleakScanner

    async def _go() -> list[dict]:
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        except TypeError:
            found = await BleakScanner.discover(timeout=timeout)
        rows: list[dict] = []
        if isinstance(found, dict):
            for address, payload in found.items():
                device, adv = payload if isinstance(payload, tuple) else (payload, None)
                name = getattr(device, "name", None) or getattr(adv, "local_name", None) or ""
                rssi = getattr(adv, "rssi", None) if adv is not None else getattr(device, "rssi", None)
                rows.append({"name": str(name or ""), "mac": str(address), "rssi": rssi})
            return rows
        for device in found:
            rows.append(
                {
                    "name": str(getattr(device, "name", "") or ""),
                    "mac": str(getattr(device, "address", "") or ""),
                    "rssi": getattr(device, "rssi", None),
                }
            )
        return rows

    return asyncio.run(_go())


def _load_simpleble():
    try:
        import simplepyble
    except ImportError:
        import simpleble as simplepyble
    return simplepyble


def _discover_simpleble(timeout: float) -> list[dict]:
    simplepyble = _load_simpleble()
    adapters = simplepyble.Adapter.get_adapters()
    if not adapters:
        raise BluetoothUnreachable(BLUETOOTH_HINT)
    adapter = adapters[0]
    adapter.scan_for(max(1, int(timeout * 1000)))
    rows = []
    for peripheral in adapter.scan_get_results():
        rows.append(
            {
                "name": str(peripheral.identifier() or ""),
                "mac": str(peripheral.address() or ""),
                "rssi": peripheral.rssi(),
            }
        )
    return rows
