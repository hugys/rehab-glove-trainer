# Rehab Glove Trainer

Desktop app (Python + PySide6) for the vision-based rehab glove. It is built on the team's
MediaPipe script (not included in this repo; its logic is ported into `rehab_app/engine/`).

- **Reference:** guided **4 s** recordings of the **unaffected** hand. 3 takes are averaged
  into one reference by default (1–5 under **Advanced**).
- **Live sessions:** the **affected** hand at a speed the clinician picks.
- **Per-finger error:** distance from the reference, for diagnosis only, in **% of hand
  size** (wrist crease to middle-finger knuckle, measured by the camera), so no ruler is
  needed. If the patient's hand size is entered, mm is shown next to it.
- **Info icons:** every setting or number that may not be obvious has a small **i**. Hover
  over it or click it for a plain-language explanation. All the texts are in
  `rehab_app/ui/info_text.py`.
- **Stall-triggered glove assist:** if a finger stops moving for a time *t* that the
  physician sets, the glove's motor for that finger drives it to the end position.
- **Video:** every reference take and session repetition is recorded as MP4 and can be
  played back in the app.
- **Storage:** SQLite, with CSV export.

## Run

**Desktop icon (macOS):** run `./make_desktop_app.sh` once. It creates **Rehab Glove
Trainer.app** on the Desktop, which you double-click to start the app without a Terminal.
Output goes to `data/launcher.log`. Re-run the script if you move the project folder.

**From a terminal:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python main.py                      # MediaPipe + auto-detected camera
python main.py --camera 1           # a specific camera
python main.py --video clip.mp4     # analyse a pre-recorded video instead of a live camera
python main.py --engine mock        # simulated hand (no camera)
python main.py --landmarks 15       # 15-point set (default 12)
```

- **Camera permission (macOS):** the first run asks for permission. If the camera stays
  black, allow your terminal (or Python) under **System Settings → Privacy & Security →
  Camera**, then restart the app.
- **Model:** `rehab_app/assets/models/hand_landmarker.task` is included. To use another
  one, pass `--model path`.

Tests: `pip install -r requirements-dev.txt && pytest`

## Workflow

1. **Patients:** name and affected hand. **Hand size** (wrist crease to middle-finger
   knuckle, in mm) is optional and sits under **Advanced** with a "measured" checkbox:
   - Tracking measures everything relative to the hand length the camera sees, as in the
     team script, so results are in % of hand size.
   - A measured hand size only adds the mm equivalent.
2. **Record reference:** choose the movement. The number of takes is under **Advanced**
   (3 by default). Each
   take is a 3-2-1 countdown followed by exactly 4 s of recording.
   - The healthy hand is wrist-zeroed, scaled to hand size, mirrored, and re-indexed to
     0–100 % progress.
   - Review the curves, play back the videos, then Accept or Redo.
3. **Session setup:** speed (25–200 % of the reference), repetitions, error threshold, and
   the glove-assist settings:
   - **Stall time t:** how long a finger may stop before the glove takes over.
   - **Start-up grace:** no stall is judged during the first seconds of a repetition, so a
     slow start isn't treated as a stop. 0 gives the original script behaviour.
4. **Live session:**
   - The camera view shows the tracked landmarks.
   - Each finger has an error bar and an assist state: `moving`, `ASSIST`, or `✓ done`.
   - Events are logged ("ring stopped at 27 % → glove assisting").
   - The glove command is shown and sent.
   - Each repetition is filmed.
5. **Results:** error vs progress, the trend across sessions, % of time above threshold,
   % of time the glove assisted, video playback, and CSV export.

A **patient view** opens on a second monitor (F11/Esc toggles full screen). It shows the
demo, the countdown, a large progress bar, and finger lights. A finger gets a purple ring
plus "glove helping" when it is being assisted.

## Glove assist (motors)

The script's `StallAssist` is used as-is, per finger:
- A finger counts as **stopped** if, during the last *t* seconds, its fingertip moved less
  than the "not moving" limit, **and** it is more than 5 % of the movement behind where the
  reference is at that moment.
  - The limit is *X* % of hand size, lowered automatically at slow speeds to half of what
    the reference itself moves in *t* (never below 1 % of hand size, about tracking jitter).
  - Without this, at 25 % speed a patient following the reference perfectly was flagged as
    stalled on every finger, because the reference itself moves less than *X* % per second.
- If it is stopped and has not yet reached the final pose of the reference, the finger's
  servo is driven toward that pose until the finger is within the completion tolerance.

| Setting | Where | Default |
|---|---|---|
| Stall time *t* | Session setup (per session) | 1.0 s |
| Start-up grace | Session setup | 2.0 s |
| "Not moving" threshold | Settings → Glove assist tuning | 4 % of hand size |
| "Complete" tolerance | Settings → Glove assist tuning | 10 % of each finger's own movement |
| Error threshold | Session setup | 11 % of hand size (≈ 10 mm on a 90 mm hand) |

**"Complete" per finger:** a finger has reached the end position when it is within 10 % of
*its own* movement in the reference (never less than 2 % of hand size).
- **Why per finger:** a flat 8 % of hand size was about half of the pinky's movement in a
  precision grip, but under 7 % of a full fist.
- **Direction-aware:** going past the end in the movement's direction also counts as done.
  A fist closed tighter than the reference is never pushed back open.
- **Fingers that shouldn't move:** if the reference moves a finger by less than 5 % of hand
  size, that finger must stay within 8 % of hand size of its position, in either direction.

**When a stall triggers:**
- The app logs **"Servo motor triggered"** in the event list and on the console, with the
  planned move per finger (e.g. `ring flex (CW) 64°`).
- `rehab_app/hardware/servo_motion.py` plans the move. It is a placeholder whose numbers need
  calibrating:
  - The finger's current position is 0–1 along the patient's own reference.
  - Degrees = (1 − position) × the movement's share × the finger's full servo travel.
  - The direction comes from flex vs extend.
  - `trigger()` is where angle/speed commands go once the firmware accepts them.

**Patient visual:** in a session, the patient view shows the recorded healthy-hand
reference (mirrored onto the affected side) in place of the generic demo. During a
repetition it moves in step with the repetition at the chosen speed, and a finger the glove
is driving glows purple.

**Serial protocol** (from the script): one line per update, `thumb,index,middle,ring,pinky`.
Each value is `+1` for CW (flex), `-1` for CCW (extend) or `0` for hold, at most 20 Hz. All
fingers are set to hold whenever a repetition ends or is aborted.

- The glove doesn't exist yet, so the link is **off**. The app still computes and displays
  every command and logs them in `session_frames.cmd_*`.
- To connect the glove, enable it in **Settings → Glove connection** and choose the port.

> **Direction sign:** the script's documentation says `-1 = extend` and `+1 = flex`, but its
> `StallAssist` returned the opposite sign. The app follows the documented protocol
> (`tracking_core.py`, marked with a comment). Check this against the firmware before
> driving real motors.

## Landmark sets

| Preset | MediaPipe indices |
|---|---|
| **12** (team script) | 0 · thumb 2, 3, 4 · index 6, 8 · middle 10, 12 · ring 14, 16 · pinky 18, 20 |
| **15** | 0 · thumb 2, 4 · MCP, PIP, TIP of index, middle, ring and pinky |

- Each reference is saved with its exact landmark indices.
- After switching sets, or editing a list in `config.py`, references must be re-recorded.
  The app shows them as missing.

## Code map

```
main.py                         startup, engine selection, camera/video source
rehab_app/engine/
  tracking_core.py              the team script's maths (normalise, resample, tolerance, error, StallAssist)
  pipeline.py                   record/run logic per frame - shared by real and mock engines
  mediapipe_engine.py           camera + MediaPipe LIVE_STREAM (HandStream, camera helpers from the script)
  mock_engine.py                simulated hand that stalls, for demos and tests
  interface.py                  the engine contract the UI uses
rehab_app/workers/engine_worker.py   30 fps thread; owns timing, takes, and MP4 recording
rehab_app/hardware/servo_link.py     ESP32 serial protocol (off until the glove exists)
rehab_app/hardware/servo_motion.py   per-finger servo direction + degrees on stall (placeholder numbers)
rehab_app/data/                 database.py (SQLite + migrations), video.py (MP4), export.py (CSV)
rehab_app/ui/                   windows, pages, widgets, video player, settings
```

## Data

- Database: `data/rehab.db`.
- Videos: `data/videos/patient_<id>/`. Each repetition's video is the same length as the
  recording, with frames repeated if the camera ran slower, so video time matches the
  logged `t_s`.
- Session CSVs have per-frame `err_*`, `state_*` and `cmd_*` columns, the same as the
  script's `session.csv` log.
  - `err_*` is in the unit given by `err_units` in the header: `pct` = % of hand size.
  - Sessions recorded before this change are `mm`, and they still display correctly.
