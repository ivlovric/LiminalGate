# Changelog

## 0.4.2

- The band-power chart names each line: band and frequency range, in that line's colour. Solid lines are LiminalGate's own computation from the raw EEG. Dashed lines are the same band as reported by the device (Mind Monitor). A Device button next to the minute window shows those dashed lines; it starts off and is remembered in the browser. Click a legend entry to hide that line.
- The spectrum chart labels its axes: Hz and log10 power.
- Tone is a header button next to View. Click it to start the biofeedback tone, and click again to silence it.

## 0.4.1

- Band power, markers, and the other full-width cards keep their content height instead of being squeezed to the viewport.
- Session times in Recordings use the browser's local clock (`11.11.2026 15:27`). Individual files sit behind Files; Download zip and Delete stay on the row.
- The page has space below the last card.

## 0.4.0

- Recordings can be listed, downloaded (one zip or a single file), and deleted from the dashboard. The same routes honor `LIMINALGATE_TOKEN`.
- Playback shows the file name, position, and loop count, and can pause, resume, restart, and seek. AUX columns are not EEG lanes. Mind Monitor `Elements` markers and LiminalGate `events.tsv` markers are replayed. A short file or a ~1 row/s export is called out before you play it. A CSV can be uploaded from the browser.
- OSC sample times no longer step backwards after a burst that ran ahead of the wall clock.
- The dashboard keeps charts inside their cards, and redraws are capped so playback stays usable on a small machine.
- The container healthcheck follows `LIMINALGATE_PORT`.
