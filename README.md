# LiminalGate

Open, self-hosted neuro gateway for live EEG, custom bands, markers, and biofeedback. v0.4 is one container. A browser on the same LAN (laptop, phone, or a TV) shows the dashboard. No telemetry. An optional access token can close the LAN API.

The default input is a synthetic theta-dominant signal, so the app runs with no headset. Mind Monitor OSC is the path that is meant to work with a Muse.

## Quick start

```bash
docker compose up --build
```

Open `http://<this-machine>:8080`.

The first view is the band-power chart (about 5 minutes), the relative band bars, and markers. EEG, the spectrum, and heart rate are folded. Ratios, the session card, and recordings stay hidden until you open **View**. **Calm** leaves the band chart and the signal-quality dots, for a TV.

**Filtered** EEG is a 1–45 Hz display filter plus the powerline notch. **Raw** is the stream as received, including the ~800 µV offset. Recording is always raw.

Click **View → Tone** once (browsers block audio until a click). Click **Record**, then **Stop**. The session is written under the `liminalgate-data` volume. Open **View → Recordings** to download it, or copy the volume out:

```bash
docker compose cp liminalgate:/data ./recordings
```

Bind-mount a folder instead of the named volume if you prefer. The directory must be writable by uid 1000.

```yaml
volumes:
  - ./recordings:/data
```

To publish only on this machine:

```bash
LIMINALGATE_BIND_WEB=127.0.0.1:8080:8080 \
LIMINALGATE_BIND_OSC=127.0.0.1:5000:5000/udp \
docker compose up --build
```

## Inputs

One input runs at a time. Switch it in **Setup** and click **Apply**. The choice is saved in `data/config.json`, and that file overrides the environment variables below the next time the container starts (not the token, bind host, or listen port).

| Input | What it does | Main settings |
| --- | --- | --- |
| OSC | Mind Monitor over UDP | Port, bind host |
| BrainFlow | Headset over BLE, including Muse S Athena | Board, MAC, serial, scan |
| LSL | One Lab Streaming Layer EEG inlet | Stream name |
| Playback | Replay a CSV | File, speed, loop |
| Synthetic | Built-in demo, no headset | none |

### OSC / Mind Monitor

`LIMINALGATE_INPUT=osc`, `LIMINALGATE_OSC_PORT` (default 5000), `LIMINALGATE_OSC_HOST` (default `0.0.0.0`).

1. In Mind Monitor set **OSC Stream Target** to this computer's IP and the published UDP port.
2. In Setup choose **OSC** and Apply. UDP is published even while another input is selected, so you do not recreate the container to switch.
3. Setup shows a packet count and the OSC paths that have actually arrived.

Expected paths: `/muse/eeg` (TP9, AF7, AF8, TP10, then optional AUX, in µV), `/muse/acc`, `/muse/gyro`, `/muse/ppg`, `/muse/optics`, `/muse/batt`, `/muse/elements/*_absolute`, `/muse/elements/horseshoe`, `/muse/elements/blink`, `/muse/elements/jaw_clench`, and `/Marker/1` … `/Marker/5`. See the [Mind Monitor FAQ](https://mind-monitor.com/FAQ.php). Horseshoe 1 is good, 2 is medium, 4 is bad; 3 is treated as poor.

Record at a constant interval. A 1-second export is about one row per second and is not an EEG stream.

### BrainFlow

`LIMINALGATE_INPUT=brainflow`, `LIMINALGATE_BRAINFLOW_BOARD` (default `synthetic`), `LIMINALGATE_BRAINFLOW_MAC`, `LIMINALGATE_BRAINFLOW_SERIAL`, `LIMINALGATE_BRAINFLOW_OTHER_INFO`.

Setup lists the boards this BrainFlow build knows. **Scan for devices** reports name, MAC, and RSSI. A `MuseS-` advertisement fills the MAC, the serial (the advertised name), and `MUSE_S_ATHENA_BOARD` (67). Muse S and Athena share that name, so switch the board to `muse_s` for an older Muse S. `Muse-` maps to Muse 2 (`MUSE_2_BOARD`, 38).

BLE does not cross the default Docker bridge. Use `compose.host.yaml` (below). If the container cannot see an adapter, the scan says: `Bluetooth isn't reachable from this container. Run with compose.host.yaml.`

### LSL

`LIMINALGATE_INPUT=lsl`, `LIMINALGATE_LSL_NAME` (empty uses the first EEG stream).

**Find streams** resolves for a short time. A missing `pylsl` returns an empty list and a message. LSL multicast needs host networking, same as BLE. A marker inlet and clock correction are still to do.

### Playback

`LIMINALGATE_INPUT=playback`, `LIMINALGATE_PLAYBACK_PATH`, `LIMINALGATE_PLAYBACK_SPEED` (1 follows the sample rate, 0 plays as fast as possible), `LIMINALGATE_PLAYBACK_LOOP` (default on).

The file picker lists CSVs under the data directory and `examples/`. Files without TP9, AF7, AF8, and TP10 are labeled `(not EEG)` and cannot be selected. **Upload a CSV** writes the file to `data/uploads/`.

While a file is playing, the header pill names the file, the position, the duration, and the loop count. Pause, resume, restart, and the seek slider sit next to it. `POST /api/playback/control` takes `{"action": "pause"|"resume"|"restart"|"seek", "position": seconds}`.

AUX columns are stored in the recording and left out of the EEG lanes, so a railed AUX channel does not set the scale. Mind Monitor's `Elements` column and a sibling `*_events.tsv` (LiminalGate's own markers, matched by sample index) are emitted as markers.

A file shorter than about 30 seconds warns that loop will restart it that often. A file slower than about 8 rows/s warns that Mind Monitor needs a Constant interval.

`examples/demo.csv` is a short synthetic mix in Mind Monitor column names. Copy it into `/data` before selecting it inside the container.

### Synthetic

`LIMINALGATE_INPUT=synthetic`. Nothing else to set. Apply starts the built-in signal, including a PPG-like pulse so the heart card has something to read.

## Recordings

**View → Recordings** lists `sub-*/ses-*` under the data directory: local start time, duration, EEG sample count, and which streams were written (eeg, edf, events, ppg, hr, optics, battery, acc, gyro).

- **Download zip** is the whole session: EEG CSV, EDF when present, events TSV, physio (PPG, heart rate, optics, battery), motion (accelerometer, gyroscope), and the JSON sidecars.
- Each file can be downloaded on its own.
- **Delete** asks for confirmation. The session that is recording right now cannot be deleted.

The same routes are under `/api/sessions`. When `LIMINALGATE_TOKEN` is set, they require `Authorization: Bearer`, `X-Liminal-Token`, or `?token=`. The dashboard stores the token in `localStorage` and sends it on downloads. `/api/health` stays open.

```
GET    /api/sessions
GET    /api/sessions/{subject}/{session_id}/archive
GET    /api/sessions/{subject}/{session_id}/files/{relative path}
DELETE /api/sessions/{subject}/{session_id}
POST   /api/playback/upload?name=clip.csv
```

On disk:

```
data/dataset_description.json
data/sub-<subject>/ses-<YYYYMMDDTHHMMSS>/
  session.json
  eeg/sub-..._task-<task>_eeg.csv
  eeg/sub-..._task-<task>_eeg.edf
  eeg/sub-..._task-<task>_eeg.json
  eeg/sub-..._task-<task>_channels.tsv
  eeg/sub-..._events.tsv
  motion/  acc.csv gyro.csv
  physio/  ppg.csv optics.csv battery.csv hr.csv
```

`session.json` records the device, the profile, notes, and `eeg_scale`. XDF is not written.

## Portainer

Build from the repository. The build context can be the git URL:

```
https://github.com/ivlovric/LiminalGate.git#main
```

The image is built locally (`liminalgate:0.4.0` in `compose.yaml`). **Re-pull image** does not rebuild that image. Bump the image tag and deploy again when you want a new build.

If 8080 or 5000 is already taken on the host, publish different host ports:

```yaml
ports:
  - 8088:8080
  - 5001:5000/udp
```

Point Mind Monitor at the host UDP port you published (5001 in that example). The app inside the container still listens on 8080 and 5000 unless you are on the host network.

## Host network (BLE and LSL)

```bash
docker compose -f compose.yaml -f compose.host.yaml up --build
```

`compose.host.yaml` sets `network_mode: host`, mounts `/var/run/dbus`, and sets `apparmor:unconfined`. Published `ports:` are ignored in this mode. The app binds `LIMINALGATE_PORT` and `LIMINALGATE_OSC_PORT` on the host. Set both when 8080 or 5000 is already in use:

```bash
LIMINALGATE_PORT=8088 LIMINALGATE_OSC_PORT=5001 \
  docker compose -f compose.yaml -f compose.host.yaml up --build
```

The healthcheck calls `127.0.0.1:$LIMINALGATE_PORT`. A saved `data/config.json` still overrides the UI fields (input, OSC port, scale, and the rest of Setup) on the next start. It does not override `LIMINALGATE_PORT` or the token.

## Muse S Athena scale

Athena's 14-bit full scale is about 1450 µV (`16383 × 0.0885`). The unsigned ADC midpoint sits near 800 µV; that offset on a raw trace is not a scale error. BrainFlow and muse-lsl have been reported to disagree by about 4.5×. LiminalGate does not hard-code a correction. **EEG scale** (`LIMINALGATE_EEG_SCALE`, default `1`) is applied once at the input and stored in the session. Mind Monitor OSC is already in µV, so leave the scale at 1 for that input.

## Signal, heart rate, markers

Samples go through a ring buffer. A sliding Welch PSD (4 s, 50% overlap, good channels, 1–45 Hz) produces absolute power in µV² and relative power. Band edges are inclusive. Ratio expressions such as `theta/delta` use a small parser (no `eval`).

The display stream is decimated (at most 32 Hz) and sent about ten times a second. Charts cap their frame rate. Recording keeps every raw sample, including AUX. The plot and the spectrum drop AUX so a railed auxiliary channel cannot flatten the EEG lanes.

A channel is left out of the mean when horseshoe or `is_good` says it is bad, when it is flat, or when samples sit on the ADC rail (near 0 µV only if the channel's median is above 200 µV, or at the top of the range, about 1400 µV and above). The ~800 µV baseline is the unsigned midpoint.

OSC bursts are paced on a 1/fs clock. A packet of samples is not stacked on one timestamp, and a timestamp is never emitted earlier than the previous sample. A real pause becomes a gap in the line.

The band chart is log10 µV² for delta, theta, alpha, beta, and gamma, smoothed with about a one-second time constant. Windows are 1, 5, 15, or 30 minutes. Device `/muse/elements/*_absolute` values are dashed lines when they arrive.

Heart rate uses `/muse/ppg` (ambient, IR, red, and any extra Athena channels), band-passed around 0.7–3.5 Hz. The strongest channel is peak-detected. Optics is used when PPG is absent. A headset path such as `/muse/elements/heart_rate` wins when it arrives. The header reads `no PPG data`, then `computing`, then the rate. `/muse/batt` index 3 is temperature in °C; the Heart card shows it only after that value arrives. There is no separate skin-temperature path.

Blink and jaw-clench marks stay off unless you turn them on. Marker keys are vibracije, puls tijela, izlazak, budjenje, and ostalo (keys 1–5). Markers also come from `POST /api/markers` and OSC `/Marker/N`.

Profiles are YAML: `classic`, `hypnagogic`, and `alpha_relaxation`. Saving from the UI writes `data/profiles/<name>.yaml`. Delete the file to restore the builtin. Host audio via `aplay` is off unless **Host speaker** is enabled.

## Configuration

Environment prefix `LIMINALGATE_`. `LIMINALGATE_INPUT` is an alias of `LIMINALGATE_INPUT_TYPE`.

| Variable | Default | Role |
| --- | --- | --- |
| `LIMINALGATE_INPUT` | `synthetic` | `synthetic`, `osc`, `playback`, `brainflow`, `lsl` |
| `LIMINALGATE_PORT` | `8080` | Web port. The healthcheck uses this value |
| `LIMINALGATE_TOKEN` | empty | When set, REST and the WebSocket require a bearer token |
| `LIMINALGATE_EEG_SCALE` | `1.0` | Multiplier for raw EEG |
| `LIMINALGATE_EEG_FS` | `256` | Nominal EEG rate until a stream reports its own |
| `LIMINALGATE_OSC_PORT` | `5000` | UDP listen port |
| `LIMINALGATE_OSC_HOST` | `0.0.0.0` | UDP bind address |
| `LIMINALGATE_PLAYBACK_PATH` | empty | CSV to play |
| `LIMINALGATE_PLAYBACK_SPEED` | `1` | `0` is as fast as possible |
| `LIMINALGATE_PLAYBACK_LOOP` | `true` | Restart at the end of the file |
| `LIMINALGATE_BRAINFLOW_BOARD` | `synthetic` | Board name or id |
| `LIMINALGATE_BRAINFLOW_MAC` | empty | Headset MAC |
| `LIMINALGATE_BRAINFLOW_SERIAL` | empty | Serial or advertised name |
| `LIMINALGATE_LSL_NAME` | empty | Empty selects the first EEG stream |
| `LIMINALGATE_DATA_DIR` | `data` | Sessions, profiles, saved UI config |
| `LIMINALGATE_POWERLINE_HZ` | `50` | Display notch, also stored on the session |
| `LIMINALGATE_DISPLAY_HZ` | `64` | Display decimation target, capped at 32 Hz |

API docs: `/docs`.

## Develop without Docker

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
cd frontend && npm ci && npm test && npm run build && cd ..
python -m liminalgate
```

`websockets` is a direct dependency. For UI work, run the API on 8080 and `npm run dev` in `frontend` (Vite proxies `/api` and `/ws`).

```bash
ruff check liminalgate tests
ruff format --check liminalgate tests
pytest
```

NumPy is pinned to 1.26.4 and SciPy to 1.14.1 so the wheels stay on baseline manylinux with runtime CPU dispatch. A 2012 Ivy Bridge CPU has AVX and not AVX2; newer NumPy wheels that require x86-64-v3 will not load there.

## Roadmap

- XDF writer
- LSL marker inlet and clock sync
- BrainFlow preset toggles in the UI
- More than one input at a time

## License

MIT. Copyright 2026 Ivica Lovrić.
