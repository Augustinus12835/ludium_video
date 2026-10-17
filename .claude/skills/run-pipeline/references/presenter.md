# On-screen presenter (opt-in)

Run this only when the user passed `--presenter` or `--presenter-hook-only`. The presenter is a
lip-synced person or character made from ONE photo by LongCat-Video-Avatar 1.5 on Modal
(`scripts/presenter.py`, `scripts/longcat_avatar_modal.py`):

- **hook** = the first frame: the presenter large on the RIGHT (x ≥ 1180 px), 720p on an H200;
- **wipe** = every later frame: a 380 px rounded window bottom-right (x ≥ 1504, y ≥ 664 px), one
  480p clip per frame on an H100, so the presenter returns to rest where each frame changes;
  `--presenter-hook-only` skips it.

It works in every mode. Its zones are reserved where the visuals are authored (Manim codegen prompt,
folio scene prompt, the red boxes on `audit_frames.py` / `folio.py still` stills), and
`compile_video.py` / `folio.py compile` key it over the finished cut.

Cost: hook ≈ US$0.075/s; wipe ≈ US$0.02/s + ~2 min model load per frame clip (≈ US$16 per
10-minute video in all; hook only ≈ US$2.40). State the estimate for the run before the first `generate`.

## Once per machine / presenter

```bash
pip install modal 'rembg[cpu]' && modal setup                # if not done for Drama 3
venv/bin/python scripts/longcat_avatar_modal.py download     # weights (~57 GB) -> Modal Volume, once
venv/bin/python scripts/presenter.py setup <photo> [--name default]
```

`setup` removes the background (rembg; a photo already on flat green is keyed directly) and writes
`inputs/presenter/<name>/hook.png` (full figure, 2:3) + `wipe.png` (upper body with the hands, square)
on chroma green. **Read both pictures**: the person whole, no background fringe, and the hands visible
in `wipe.png` (gestures happen there). Then write `"look"` in `inputs/presenter/<name>/presenter.json`,
one sentence describing what you see ("A woman in her thirties with shoulder-length black hair,
silver earrings and a navy blazer"). LongCat's prompt opens with it, and `generate` refuses while it
is empty. Optional keys: `"style"` (default "Consistent with the reference image."),
`"panel"` / `"border"` (window colours; default navy/gold for Manim, paper/ink for folio).

If no photo was given, ask for one. Present only people who have agreed.

## Once per lecture (Phase A, before any frame is authored)

```bash
venv/bin/python scripts/presenter.py enable pipeline/<L> [--name default] [--no-wipe]
```

## Per video

After TTS (folio: after the word timeline):

1. **Direct.** One clean-context subagent:
   `venv/bin/python scripts/presenter.py prompt pipeline/<L>/Video-N --out <scratch>/presenter_N.md`.
   It writes `Video-N/presenter/directions.json` (anchor-based: a verbatim phrase per direction,
   gesture and/or face from the menu, one gesture per frame, the hook face-only) and runs
   `presenter.py plan` until it is clean. No directions file = the presenter just talks.
2. **Generate in the background** (all of a batch's videos in one call; every clip runs in its own
   container, in parallel):
   ```bash
   venv/bin/python scripts/presenter.py generate pipeline/<L>/Video-1 pipeline/<L>/Video-2 … \
       2>&1 | tee /tmp/run_pipeline_logs/<L>_presenter.log
   ```
   A clip is reused while its audio, direction, picture and prompt are unchanged, so re-voicing one
   frame regenerates only that frame's clip. A ~1-minute frame takes ~20 min of wall time; author
   frames meanwhile.
3. **Check the clips.** Read a contact sheet of `presenter/hook_green.mp4` and two or three
   `wipe_<N>_green.mp4` (`ffmpeg -i … -vf "fps=1/4,scale=-1:240,tile=6x1" -frames:v 1 sheet.png`):
   on-model face, green kept flat, gestures where directed.
4. **Hand check** (whenever a frame has a hand gesture):
   ```bash
   venv/bin/python scripts/presenter.py hands pipeline/<L>/Video-N
   ```
   It scores each gesture frame after its sentence against the presenter's resting picture and writes
   `presenter/hands_<section>.png` (rest | 2 × during | 6 × after). Read every sheet, FLAG or not:
   a hand still raised in most "after" tiles is a floating hand. Fix it with
   `presenter.py reset pipeline/<L>/Video-N <frame> […]` and then `generate` again. That adds one clip
   from the resting picture where the sentence ends, cross-faded in, and the other clips are kept.
5. **Compile** as usual. `compile_video.py` / `folio.py compile` refuse while a clip is missing or
   stale, then key hook + window over the cut (`final_video_board.mp4` = visuals only;
   `--no-presenter` compiles just that for a quick look). Extract a still in the hook and one in a
   later frame and Read them, checking that nothing the viewer needs is under the presenter.

## Fixes

- **A frame's visuals under the presenter:** fix the frame or scene source (it was authored outside
  its zone), re-render, recompile. The presenter clips are not touched.
- **A direction:** edit `presenter/directions.json`, then `generate` (only that frame's clips) and compile.
- **The look or the pictures:** edit `presenter.json` / replace `hook.png` / `wipe.png`. Every clip is
  then stale, which costs a full regeneration.
