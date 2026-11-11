"""Lists the Setup panel can show: LSL streams on the network, CSV files on disk."""

from __future__ import annotations

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


def playback_files(data_dir: Path, extra: Path | None = None) -> list[str]:
    """CSV paths the playback picker can offer. The configured file is not invented."""

    roots = [data_dir]
    if extra is not None:
        roots.append(extra)
    found: list[str] = []
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
            found.append(text)
            if len(found) >= 200:
                return found
    return found
