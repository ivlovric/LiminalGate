"""Lists the Setup panel can show: LSL streams on the network, CSV files on disk."""

from __future__ import annotations

import csv
from pathlib import Path


def discover_lsl(timeout: float = 1.2) -> dict:
    """Resolve LSL streams without raising when pylsl is absent."""

    try:
        import pylsl
    except ImportError:
        return {
            "available": False,
            "streams": [],
            "error": "pylsl is not installed. LSL is optional.",
        }
    try:
        found = pylsl.resolve_streams(wait_time=float(timeout))
    except Exception as exc:
        return {"available": True, "streams": [], "error": str(exc)}
    streams: list[dict[str, object]] = []
    for info in found:
        try:
            streams.append(
                {
                    "name": str(info.name()),
                    "type": str(info.type()),
                    "channels": int(info.channel_count()),
                    "srate": float(info.nominal_srate() or 0.0),
                }
            )
        except Exception:
            continue
    return {"available": True, "streams": streams, "error": None}


def playback_files(data_dir: Path, extra: Path | None = None) -> list[dict[str, object]]:
    """CSV files the playback picker can offer. Non-EEG files stay visible but are labeled."""

    roots = [data_dir]
    if extra is not None:
        roots.append(extra)
    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.csv")):
            if not path.is_file():
                continue
            text = str(path)
            if text in seen:
                continue
            seen.add(text)
            eeg = _header_has_eeg(path)
            found.append({"path": text, "name": path.name, "eeg": eeg})
            if len(found) >= 200:
                return found
    return found


def _header_has_eeg(path: Path) -> bool:
    from liminalgate.inputs.playback import ColumnMap

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
    except OSError:
        return False
    if not header:
        return False
    return ColumnMap([str(name) for name in header]).has_eeg
