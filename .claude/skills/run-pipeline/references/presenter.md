# On-screen presenter (opt-in)

Run this only when the user passed `--presenter` or `--presenter-hook-only`. The presenter is a
lip-synced person or character made from ONE photo, on Modal (`scripts/presenter.py`):

- **hook** = the first frame: the presenter large on the RIGHT (x ≥ 1180 px), face-only —
  LongCat-Video-Avatar 1.5, 720p on an H200 (`scripts/longcat_avatar_modal.py`);
- **wipe** = every later frame: head and shoulders — SoulX-FlashTalk-14B, 480×480
  (`scripts/flashtalk_modal.py`), one clip per frame, all of a video's clips in ONE H100 container.
  Lip-sync, expressions and nods; no hands. `--presenter-hook-only` skips it. Where it sits:
  - **Manim:** a 380 px rounded navy window bottom-right (x ≥ 1504, y ≥ 664 px); the presenter
    fades out and in where each frame changes.
  - **Folio:** a 334 px square of the folio paper with the chrome's double hairline in the TOP-RIGHT
    corner (box 1530,56; reserved x ≥ 1496, y ≤ 424), like a small plate tipped into the page; each
    frame dissolves 0.3 s from the previous frame's last picture, so the presenter never blinks out
    between paragraphs. Folio's hook plates continue the scene behind the figure (`"hook": true`,
    see phase-b-folio.md Stage 3b).

(`presenter.py enable … --longcat-wipe` gives the older waist-up LongCat window with hand gestures —
~6× the cost, and the gestures proved unreliable; only on request. Its extra checks are at the end.)

It works in every mode. Its zones are reserved where the visuals are authored (Manim codegen prompt,
folio scene prompt, the red boxes on `audit_frames.py` / `folio.py still` stills), and
`compile_video.py` / `folio.py compile` key it over the finished cut.

Cost: hook ≈ US$0.075/s (≈ US$2.40 for a 30 s hook); wipe ≈ US$0.003/s + ~3 min start-up per video
(≈ US$2.50 per 10 minutes) — ≈ US$5 per 10-minute video. State the estimate for the run before the
first `generate`.

## Once per machine / presenter

```bash
pip install modal 'rembg[cpu]' && modal setup                # if not done for Drama 3
venv/bin/python scripts/longcat_avatar_modal.py download     # hook weights (~57 GB) -> Modal Volume, once
venv/bin/python scripts/flashtalk_modal.py download          # wipe weights (~54 GB) -> Modal Volume, once
venv/bin/python scripts/presenter.py setup <photo> [--name default]
```

`setup` removes the background (rembg; a photo already on flat green is keyed directly) and writes
`inputs/presenter/<name>/hook.png` (full figure, 2:3) + `wipe_head.png` (head and shoulders, square)
+ `wipe.png` (upper body, only for a LongCat wipe) on chroma green. **Read the pictures**: the person
whole, no background fringe, and in `wipe_head.png` the face large with the shoulders in, the hands
out of shot (re-crop it by hand if the automatic square is off). Then write `"look"` in
`inputs/presenter/<name>/presenter.json`, one sentence describing what you see ("A woman in her
thirties with shoulder-length black hair, silver earrings and a navy blazer"). Every prompt opens
with it, and `generate` refuses while it is empty. Optional keys: `"style"` (default "Consistent with the reference image."),
`"panel"` / `"border"` (Manim window colours; default navy/gold — a folio window is always the paper square).

If no photo was given, ask for one. Present only people who have agreed.

## Once per lecture (Phase A, before any frame is authored)

```bash
venv/bin/python scripts/presenter.py enable pipeline/<L> [--name default] [--no-wipe | --longcat-wipe]
```

## Per video

After TTS (folio: after the word timeline):

1. **Direct.** One clean-context subagent:
   `venv/bin/python scripts/presenter.py prompt pipeline/<L>/Video-N --out <scratch>/presenter_N.md`.
   It writes `Video-N/presenter/directions.json` (anchor-based: a verbatim phrase per direction,
   a face from the menu, or `nod`; no hand gestures, the hook face-only) and runs
   `presenter.py plan` until it is clean. No directions file = the presenter just talks.
2. **Generate in the background** (all of a batch's videos in one call: the hooks in parallel LongCat
   containers, then one FlashTalk container per video, in parallel):
   ```bash
   venv/bin/python scripts/presenter.py generate pipeline/<L>/Video-1 pipeline/<L>/Video-2 … \
       2>&1 | tee /tmp/run_pipeline_logs/<L>_presenter.log
   ```
   A clip is reused while its audio, direction, picture and prompt are unchanged, so re-voicing one
   frame regenerates only that frame's clip. A 10-minute video takes ~30 min of wall time; author
   frames meanwhile.
3. **Check the clips.** Read a contact sheet of `presenter/hook_green.mp4` and two or three
   `wipe_<N>_green.mp4` (`ffmpeg -i … -vf "fps=1/4,scale=-1:240,tile=6x1" -frames:v 1 sheet.png`):
   on-model face, green kept flat, mouth moving, expressions changing, and **no drawn-in text** in the
   bottom third of a long clip's last ~20 s.
4. **Compile** as usual. `compile_video.py` / `folio.py compile` refuse while a clip is missing or
   stale, then key hook + window over the cut (`final_video_board.mp4` = visuals only;
   `--no-presenter` compiles just that for a quick look). The window track
   (`presenter/wipe_window.mov`) is rebuilt automatically when its digest — the wipe plan with path-free
   audio names, the clips' digests, the window's look — no longer matches, so a re-generated clip never
   compiles with a stale window. Extract a still in the hook and one in a later frame and Read them,
   checking that nothing the viewer needs is under the presenter.

### LongCat wipe only (`--longcat-wipe`): hand checks before compiling

Directions there may carry hand gestures (`point_up`, `think`, `point_screen`, `count`, `open_palm`,
`caution`), ONE per frame. Then:

- **Hand check:**
   ```bash
   venv/bin/python scripts/presenter.py hands pipeline/<L>/Video-N
   ```
   It scores each gesture frame after its sentence against the presenter's resting picture and writes
   `presenter/hands_<section>.png` (rest | 2 × during | 6 × after). Read every sheet, FLAG or not:
   a hand still raised in most "after" tiles is a floating hand. Fix it with
   `presenter.py reset pipeline/<L>/Video-N <frame> […]` and then `generate` again. That adds one clip
   from the resting picture where the sentence ends, cross-faded in, and the other clips are kept.

   Two defects the hand check cannot see — look for them on the sheets yourself:
   - **Ghost hand:** during a one-handed gesture, the resting (clasped) hands stay at the waist as
     well, so she has three hands. Look at the waist in one full-res still mid-gesture: ONE hand
     there is right, two clasped hands is the ghost. Fix: drop that direction's `gesture` (keep
     `face`), `generate`. Rerolling did not clear it.
   - **Drawn-in captions** late in a long clip (seen ~99 s into a 109 s frame): pseudo-text across the
     body, light on green, so it survives the key. Tile the last ~20 s of every long clip
     (`fps=1/3,tile`) and look at the bottom third. Fix: put `"reset": true` on a direction whose
     sentence ends before the text appears, then `generate`. Only that new tail clip is generated.

## Fixes

- **A frame's visuals under the presenter:** fix the frame or scene source (it was authored outside
  its zone), re-render, recompile. The presenter clips are not touched.
- **A direction:** edit `presenter/directions.json`, then `generate` (only that frame's clips) and compile.
- **A mispronounced sentence:** `fix_tts_sentence.py` swaps it at zero shift and re-stamps that frame's
  clips as current (the lip-sync drifts inside the one sentence only), so nothing regenerates; just
  recompile. `--regenerate-presenter` leaves them stale if you would rather pay for exact lip-sync.
- **The look or the pictures:** edit `presenter.json` / replace `hook.png` / `wipe_head.png`. Every clip is
  then stale, which costs a full regeneration.
