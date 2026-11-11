"""Optional BrainFlow input, including the synthetic board and Muse S Athena.

BLE from inside Docker needs the host network namespace and the system D-Bus
socket. See the README. The library is imported only when this input starts.

EEG samples are multiplied by ``scale``. BrainFlow and the muse-lsl stack can
disagree on the Athena microvolt conversion by about 4.5×; keep the factor
with the session instead of hard-coding it.

TODO: expose every BrainFlow preset toggle in the UI. v0.1 reads the default
EEG preset and, when the board description has them, the auxiliary motion
preset and the ancillary optical preset.
"""

from __future__ import annotations

import logging
import threading
import time

from liminalgate.errors import InputError
from liminalgate.signals import Sample

logger = logging.getLogger("liminalgate.brainflow")

_ATHENA_NAMES = ("MUSE_S_ATHENA_BOARD", "MUSE_S_ANTHENA_BOARD")
_BOARDS = {
    "synthetic": ("SYNTHETIC_BOARD",),
    "muse_s_athena": _ATHENA_NAMES,
    "athena": _ATHENA_NAMES,
    "muse_2": ("MUSE_2_BOARD",),
    "muse_s": ("MUSE_S_BOARD",),
    "muse_2016": ("MUSE_2016_BOARD",),
}


def resolve_board_id(board_ids: object, name: str) -> int:
    """Resolve a short name against a BrainFlow ``BoardIds`` enum (or a stand-in)."""

    key = name.strip().lower().replace("-", "_")
    attrs = _BOARDS.get(key)
    if attrs is None:
        known = ", ".join(sorted(_BOARDS))
        raise InputError(f"unknown BrainFlow board {name!r}; expected one of: {known}")
    for attr in attrs:
        if hasattr(board_ids, attr):
            return int(getattr(board_ids, attr))
    if key in {"muse_s_athena", "athena"}:
        raise InputError("This BrainFlow build has no Muse S Athena board id. Install brainflow>=5.22.2.")
    raise InputError(f"BrainFlow has no board id named {attrs[0]}")


class BrainFlowInput:
    name = "brainflow"

    def __init__(
        self,
        board: str,
        scale: float = 1.0,
        mac: str = "",
        serial: str = "",
        other_info: str = "",
    ) -> None:
        self.board_name = board
        self.scale = float(scale)
        self.mac = mac
        self.serial = serial
        self.other_info = other_info
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._board = None
        self._emit = None

    async def start(self, emit) -> None:
        self._emit = emit
        self.error = None
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._serve, name="liminalgate-brainflow", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=30):
            self._stop.set()
            raise InputError("BrainFlow did not start within 30s")
        if self.error:
            raise InputError(self.error)

    async def stop(self) -> None:
        self._stop.set()
        board = self._board
        if board is not None:
            try:
                if board.is_prepared():
                    board.stop_stream()
                    board.release_session()
            except Exception:
                logger.exception("brainflow_stop_failed")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        self._board = None

    def _serve(self) -> None:
        try:
            self._run_board()
        except InputError as exc:
            self.error = str(exc)
        except Exception as exc:
            logger.exception("brainflow_failed")
            self.error = str(exc)
        finally:
            self._ready.set()

    def _run_board(self) -> None:
        try:
            from brainflow.board_shim import (
                BoardIds,
                BoardShim,
                BrainFlowInputParams,
                BrainFlowPresets,
            )
        except ImportError as exc:
            raise InputError("brainflow is not installed. pip install 'brainflow>=5.22.2' (optional extra).") from exc
        board_id = resolve_board_id(BoardIds, self.board_name)
        params = BrainFlowInputParams()
        if self.mac:
            params.mac_address = self.mac
        if self.serial:
            params.serial_number = self.serial
        if self.other_info:
            params.other_info = self.other_info
        board = BoardShim(board_id, params)
        self._board = board
        board.prepare_session()
        board.start_stream()
        self._ready.set()
        logger.info("brainflow_streaming", extra={"board": self.board_name, "id": board_id})
        presets = _preset_plan(BoardShim, board_id, BrainFlowPresets)
        while not self._stop.is_set():
            for kind, preset, names, rows, rate in presets:
                try:
                    data = board.get_board_data(preset=preset) if preset is not None else board.get_board_data()
                except Exception:
                    continue
                if data is None or data.shape[1] == 0:
                    continue
                self._emit_matrix(data, names, rows, rate, kind)
            self._stop.wait(0.04)
        try:
            board.stop_stream()
            board.release_session()
        except Exception:
            logger.exception("brainflow_release_failed")

    def _emit_matrix(self, data, names: tuple[str, ...], rows: list[int], rate: float, kind: str) -> None:
        emit = self._emit
        if emit is None or not rows:
            return
        count = data.shape[1]
        for index in range(count):
            values = tuple(float(data[row, index]) * (self.scale if kind == "eeg" else 1.0) for row in rows)
            emit(Sample(time.time(), kind if kind != "optical" else "ppg", names, values, rate))


def _preset_plan(board_shim: object, board_id: int, presets: object) -> list[tuple]:
    plan = []
    default = getattr(presets, "DEFAULT_PRESET", None)
    eeg_rows = list(board_shim.get_eeg_channels(board_id))  # type: ignore[attr-defined]
    try:
        eeg_names = tuple(board_shim.get_eeg_names(board_id))  # type: ignore[attr-defined]
    except Exception:
        eeg_names = tuple(f"CH{i + 1}" for i in range(len(eeg_rows)))
    if len(eeg_names) != len(eeg_rows):
        eeg_names = tuple(f"CH{i + 1}" for i in range(len(eeg_rows)))
    rate = float(board_shim.get_sampling_rate(board_id))  # type: ignore[attr-defined]
    plan.append(("eeg", default, eeg_names, eeg_rows, rate))
    auxiliary = getattr(presets, "AUXILIARY_PRESET", None)
    if auxiliary is not None:
        try:
            acc = list(board_shim.get_accel_channels(board_id, auxiliary))  # type: ignore[attr-defined]
            gyro = list(board_shim.get_gyro_channels(board_id, auxiliary))  # type: ignore[attr-defined]
            motion_rate = float(board_shim.get_sampling_rate(board_id, auxiliary))  # type: ignore[attr-defined]
            if acc:
                plan.append(("acc", auxiliary, ("x", "y", "z"), acc[:3], motion_rate))
            if gyro:
                plan.append(("gyro", auxiliary, ("x", "y", "z"), gyro[:3], motion_rate))
        except Exception:
            logger.info("brainflow_no_motion_preset", extra={"board": board_id})
    ancillary = getattr(presets, "ANCILLARY_PRESET", None)
    if ancillary is not None:
        try:
            optical = list(board_shim.get_optical_channels(board_id, ancillary))  # type: ignore[attr-defined]
            optical_rate = float(board_shim.get_sampling_rate(board_id, ancillary))  # type: ignore[attr-defined]
            if optical:
                names = tuple(f"opt{i + 1}" for i in range(len(optical)))
                plan.append(("optical", ancillary, names, optical, optical_rate))
        except Exception:
            logger.info("brainflow_no_optical_preset", extra={"board": board_id})
    return plan
