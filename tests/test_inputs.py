"""OSC parsing, UDP delivery, CSV playback, and optional-input errors."""

import asyncio
from pathlib import Path

import pytest
from liminalgate.errors import InputError
from liminalgate.inputs.brainflow_input import resolve_board_id
from liminalgate.inputs.lsl_input import LSLInput
from liminalgate.inputs.osc_mindmonitor import OscMindMonitorInput, known_osc_address, parse_message
from liminalgate.inputs.playback import ColumnMap, PlaybackInput, inspect_csv, items_from_row
from liminalgate.signals import Event, Sample
from pythonosc.udp_client import SimpleUDPClient


def test_parse_mind_monitor_paths():
    eeg = parse_message("/muse/eeg", (1, 2, 3, 4, 9), fs=256, scale=2, now=10.0)
    assert isinstance(eeg[0], Sample)
    assert eeg[0].channels == ("TP9", "AF7", "AF8", "TP10", "AUX1")
    assert eeg[0].values[0] == 2.0
    assert eeg[0].values[-1] == 18.0

    # Eight floats are one sample: four EEG channels plus four auxiliary.
    wide = parse_message("/muse/eeg", (1, 1, 1, 1, 2, 2, 2, 2), now=1.0)
    assert len(wide) == 1
    assert wide[0].channels[-1] == "AUX4"
    batched = parse_message("/muse/eeg", (1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3), now=1.0)
    assert len(batched) == 3
    assert batched[1].values == (2.0, 2.0, 2.0, 2.0)

    quality = parse_message("/muse/elements/horseshoe", (1, 2, 4, 1), now=1.0)
    assert quality[0].stream == "quality"
    blink = parse_message("/muse/elements/blink", (1,), now=1.0)
    assert isinstance(blink[0], Event) and blink[0].kind == "blink"
    jaw = parse_message("/muse/elements/jaw_clench", (1,), now=1.0)
    assert jaw[0].kind == "jaw_clench"
    marker = parse_message("/Marker/3", (1,), now=1.0)
    assert marker[0].osc == 3
    assert parse_message("/Marker/3", (0,), now=1.0) == []
    bands = parse_message("/muse/elements/theta_absolute", (0.1, 0.2, 0.3, 0.4), now=1.0)
    assert bands[0].stream == "device_band"
    assert parse_message("/muse/not-a-stream", (1,), now=1.0) == []
    ppg = parse_message("/muse/ppg", (0.1, 0.8, 0.4, 0.2), now=1.0)
    assert ppg[0].stream == "ppg"
    assert ppg[0].channels == ("ambient", "ir", "red", "ch4")
    assert ppg[0].rate == 64.0
    device = parse_message("/muse/elements/heart_rate", (72.0,), now=1.0)
    assert device[0].stream == "device_hr"
    assert device[0].values == (72.0,)
    assert parse_message("/muse/elements/heart_rate", (0.0,), now=1.0) == []
    assert parse_message("/muse/algorithm/heart", (64.0,), now=1.0)[0].values == (64.0,)
    batt = parse_message("/muse/batt", (80, 3900, 3700, 33), now=1.0)
    assert batt[0].channels[3] == "temp"
    assert batt[0].values[3] == 33
    assert known_osc_address("/muse/ppg")
    assert known_osc_address("/muse/elements/heart_rate")
    assert not known_osc_address("/muse/not-a-stream")


def test_osc_udp_roundtrip():
    received: list = []

    async def run():
        plugin = OscMindMonitorInput("127.0.0.1", 0, fs=256, scale=1)
        loop = asyncio.get_running_loop()

        def emit(item):
            loop.call_soon_threadsafe(received.append, item)

        await plugin.start(emit)
        client = SimpleUDPClient("127.0.0.1", plugin.bound_port)
        client.send_message("/muse/eeg", [10.0, 11.0, 12.0, 13.0])
        client.send_message("/Marker/1", [1])
        for _ in range(50):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.02)
        await plugin.stop()

    asyncio.run(run())
    kinds = [type(item) for item in received]
    assert Sample in kinds and Event in kinds
    sample = next(item for item in received if isinstance(item, Sample))
    assert sample.values == (10.0, 11.0, 12.0, 13.0)


def test_playback_mind_monitor_and_native_csv(tmp_path: Path):
    mind = tmp_path / "mind.csv"
    mind.write_text(
        "TimeStamp,RAW_TP9,RAW_AF7,RAW_AF8,RAW_TP10,AUX_RIGHT,HSI_TP9,HSI_AF7,HSI_AF8,HSI_TP10,"
        "Accelerometer_X,Accelerometer_Y,Accelerometer_Z,Marker\n"
        "2026-11-11 12:00:00.000,1,2,3,4,8,1,1,2,4,0.1,0.2,0.9,\n"
        "2026-11-11 12:00:00.004,1.5,2.5,3.5,4.5,9,1,1,1,1,0.1,0.2,0.9,2\n",
        encoding="utf-8",
    )
    native = tmp_path / "native.csv"
    native.write_text(
        "timestamp,TP9,AF7,AF8,TP10\n0.000000,1,2,3,4\n0.003906,5,6,7,8\n",
        encoding="utf-8",
    )

    async def collect(path: Path, speed: float = 0):
        found: list = []
        plugin = PlaybackInput(str(path), speed=speed, loop=False, scale=1.0)
        await plugin.start(found.append)
        for _ in range(100):
            if plugin._task is not None and plugin._task.done():
                break
            await asyncio.sleep(0.01)
        await plugin.stop()
        return found

    mind_items = asyncio.run(collect(mind))
    eeg = [item for item in mind_items if isinstance(item, Sample) and item.stream == "eeg"]
    assert len(eeg) == 2
    assert eeg[0].channels == ("TP9", "AF7", "AF8", "TP10")
    assert eeg[0].values == (1.0, 2.0, 3.0, 4.0)
    assert any(isinstance(item, Event) and item.osc == 2 for item in mind_items)
    assert any(isinstance(item, Sample) and item.stream == "quality" for item in mind_items)

    native_items = asyncio.run(collect(native))
    assert [item.values[0] for item in native_items if isinstance(item, Sample)] == [1.0, 5.0]

    with native.open(newline="", encoding="utf-8") as handle:
        import csv

        reader = csv.DictReader(handle)
        columns = ColumnMap(list(reader.fieldnames or []))
        row = next(reader)
    assert items_from_row(columns, row, 1.0, 256.0)[0].channels[0] == "TP9"


def test_playback_missing_file(tmp_path: Path):
    plugin = PlaybackInput(str(tmp_path / "nope.csv"))

    async def run():
        await plugin.start(lambda _item: None)

    with pytest.raises(InputError, match="not found"):
        asyncio.run(run())


def test_playback_ignores_aux_reads_markers_and_warns(tmp_path: Path):
    path = tmp_path / "sub-01_ses-1_task-rest_eeg.csv"
    rows = ["timestamp,TP9,AF7,AF8,TP10,AUX4,Elements"]
    for index in range(8):
        stamp = 100.0 + index / 256.0
        if index == 4:
            stamp = 100.0 - 0.15
        rows.append(f"{stamp:.6f},1,2,3,4,1450,{'blink' if index == 3 else ''}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    events = tmp_path / "sub-01_ses-1_task-rest_events.tsv"
    events.write_text(
        "onset\tduration\ttrial_type\tvalue\tsource\tsample\n0.01\t0\tmarker\tthreshold\tui\t2\n",
        encoding="utf-8",
    )
    info = inspect_csv(path)
    assert info["eeg"] is True
    assert "Short file" in info["warning"]

    slow = tmp_path / "slow_eeg.csv"
    slow_rows = ["TimeStamp,RAW_TP9,RAW_AF7,RAW_AF8,RAW_TP10"]
    for index in range(6):
        slow_rows.append(f"2026-11-11 12:00:0{index}.000,1,2,3,4")
    slow.write_text("\n".join(slow_rows) + "\n", encoding="utf-8")
    slow_info = inspect_csv(slow)
    assert slow_info["rate"] < 2
    assert "too slow" in slow_info["warning"]

    async def collect():
        found: list = []
        plugin = PlaybackInput(str(path), speed=0, loop=False)
        plugin.seek(2 / plugin.rate if plugin.rate else 0)
        await plugin.start(found.append)
        for _ in range(100):
            if plugin._task is not None and plugin._task.done():
                break
            await asyncio.sleep(0.01)
        await plugin.stop()
        return found

    items = asyncio.run(collect())
    eeg = [item for item in items if isinstance(item, Sample) and item.stream == "eeg"]
    assert eeg and len(eeg[0].values) == 4
    assert all(eeg[index].t < eeg[index + 1].t for index in range(len(eeg) - 1))
    names = [item.name for item in items if isinstance(item, Event)]
    assert "threshold" in names
    assert "blink" in names
    plugin = PlaybackInput(str(path), speed=0, loop=False)
    plugin.pause()
    assert plugin.state["paused"] is True
    plugin.resume()
    assert plugin.state["paused"] is False


def test_brainflow_board_aliases():
    class Ids:
        SYNTHETIC_BOARD = -1
        MUSE_S_ATHENA_BOARD = 67
        MUSE_2_BOARD = 38

    assert resolve_board_id(Ids, "muse_s_athena") == 67
    assert resolve_board_id(Ids, "MUSE_S_ATHENA_BOARD") == 67
    assert resolve_board_id(Ids, "67") == 67
    assert resolve_board_id(Ids, "synthetic") == -1

    class Typo:
        MUSE_S_ANTHENA_BOARD = 66

    assert resolve_board_id(Typo, "athena") == 66
    with pytest.raises(InputError, match="unknown"):
        resolve_board_id(Ids, "not-a-board")


def test_lsl_missing_library_is_explicit():
    try:
        import pylsl  # noqa: F401
    except ImportError:
        pylsl = None
    if pylsl is not None:
        pytest.skip("pylsl is installed")

    async def run():
        plugin = LSLInput()
        await plugin.start(lambda _item: None)

    with pytest.raises(InputError, match="pylsl"):
        asyncio.run(run())
