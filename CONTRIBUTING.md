# Contributing

LiminalGate is MIT licensed. Issues and pull requests are welcome.

## Setup

Python 3.11 or newer, and Node 22 for the dashboard.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
cd frontend && npm ci && npm run build
```

Optional device libraries (not required for tests):

```bash
python -m pip install -r requirements-optional.txt
```

## Checks

```bash
ruff check liminalgate tests
ruff format --check liminalgate tests
pytest
cd frontend && npm run build
```

GitHub Actions runs the same commands.

## Notes for changes

- Keep code, comments, and docs in English.
- Pin new dependencies. Do not bump NumPy to 2.x or SciPy past the 1.14 line without checking that the wheels still run on an Intel Ivy Bridge CPU (AVX, no AVX2). The pins in `requirements.txt` exist for that machine.
- New inputs implement `start(emit)` and `stop()`. `emit` receives `Sample` or `Event`. Apply `eeg_scale` at the input boundary, once.
- Band and ratio names are identifiers (`theta_peak`, not `theta-peak`). Expressions are the small parser in `liminalgate/expressions.py`; do not pass them to `eval`.
- Device events (blink, jaw clench) stay a different event kind from user markers.
- There is no telemetry. Do not add phone-home calls.
- v0.1 records CSV and EDF+. XDF is a documented TODO, not a silent stub that pretends to write a file.
