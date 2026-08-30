# Failure-class playbooks

Always read the log tail before picking a fix; resume rather than restart (disk state is
authoritative); 3 attempts per failing step, then surface to the user. Never delete files
unless clearly corrupt output (truncated mp4, zero-byte audio) — check size + mtime first.

## Manim render failure

Symptoms: `FAIL: Manim render failed`, LaTeX errors, `cannot import name`, an
`AttributeError` traceback naming `frame_<N>_manim.py`.

Read the frame's `.py` and the error tail; diagnose with `templates/manim_system_prompt.md`.
Recurring culprits: unbalanced `{}` in `MathTex`; `\cancel` (swap for `Cross()`);
`font_size=` passed to axis-label getters (use `MathTex(...).scale(0.7)`);
`BackgroundRectangle(opacity=…)` vs `fill_opacity=…`; `add_step` overflow (reduce steps /
scale down); a symbol outside amsmath/amssymb; a literal Unicode `×`/`⋯` inside `MathTex`
(use `\times`/`\cdots`); a zero-length `Line(p, p)` (hangs the render forever). Make a
targeted `Edit` — don't rewrite the file unless it's structurally rotten. Re-render the
single frame:

```bash
venv/bin/python -c "
from scripts.generate_math_animation import render_manim_scene
from pathlib import Path
p = Path('pipeline/<L>/Video-<N>/frames/frame_<F>_manim.py')
ok, msg = render_manim_scene(p.read_text(), str(p.parent / 'frame_<F>.mp4'), <duration>)
print(('OK ' if ok else 'FAIL: ') + msg[-1500:])"
```

(`<duration>` from `ffprobe` on the frame's mp3.) Then resume — the animate step skips
frames that already have their mp4.

`subprocess.TimeoutExpired` on a 4K render = a dense frame exceeded the ceiling (default
1800s). Resume with `MANIM_RENDER_TIMEOUT=2700` (or higher); never lower the resolution or
trim content. Dense worked-example and physics frames hit this most.

## TTS narration check (halts the tts step before audio)

`✗ TTS narration check failed` names each frame, its spoken source, and offending tokens by
category. The gate only detects — false positives are expected and land here for triage.
The canonical category list and conversions live in `scripts/utils/narration_check.py` and
the prompt rules (`scripts/utils/tts_rules.py`). Summary:

| Category | Convert to |
|---|---|
| `numeral` | spelled out ("nineteen eighty-three") |
| `greek` / `math_symbol` | the name ("alpha", "square root of", "times", "degrees") |
| `hex` / `opaque` | spaced characters ("0 x D E A D…") |
| `differential` | spaced ("d x") |
| `acronym` | spaced letters ("U T X O"; allowlisted ones aren't flagged). Includes mixed-case initialisms, where a lowercase letter inside the token does NOT make it a word — "VaR" is voiced "var", so write "value at risk"; also "CVaR", "DoS" — and plural/possessive forms ("UTXOs" → "U T X Os"). |
| `code_token` | spoken prose ("my func", "is equal to") |
| `variable_a` | uppercase the variable: "A-one", "A times t", "slope A" (lowercase `a` reads as the article; only `a` collides) |
| `sentence_a` | rephrase so "A" isn't the first word ("Matrix A times…") |

Preferred notation forms (correct, never flagged): `Ax`→"A-X", `A_x`→"A-X",
`sigma_n`→"sigma-N", `a_1`→"A-one", `Â`→"A-hat", `\bar{x}`→"X-bar". Subscripts are
hyphen-bound with NO spoken "sub" (2026-08-25) — legacy "A-sub-X" narration is not
wrong, just verbose; don't rewrite an already-voiced video for it.

Fix the source the report names — `script.json` narration for every frame class (only a
LEGACY pre-2026-08-30 video names `natural_narration` in `math_verification.json`; fix both
there when unsure). On-screen text keeps
its normal form; only spoken text changes. The gate fires in Stage 2 (the producer runs
tts): mechanical token conversions (the table above) may be applied directly; anything that
genuinely rephrases a sentence (`sentence_a`, pacing rewording) is spoken-text authoring —
route it through a one-shot subagent on the scripting model. Resume `--from tts`; for a
confirmed false positive only, resume once with `SKIP_NARRATION_CHECK=1`.

If an edit meaningfully lengthens a frame's audio on an animated frame, re-render that frame
after tts so the animation re-aligns (see CLAUDE.md "Fixing TTS / Narration").

## API overload / transient network

ElevenLabs 429 / overloaded: wait 60s, resume; again → 5 min; third time → surface the
status to the user. Network blips (`ConnectionResetError`, `ReadTimeout`): retry
immediately. ElevenLabs `quota_exceeded` is a hard wall — surface it.

## Producer looks dead (orchestrator)

**File mtime is not a liveness signal.** A producer reading contact sheets and full-res
stills writes nothing for a long stretch — one can go ~30 minutes without touching a file
mid-audit and still be alive. Spawning a "recovery" agent against a live producer risks two
agents editing the same frames.

Before concluding a producer died, check in this order:

1. `ps -eo pid,etimes,pcpu,args | grep "manim render"` — an active render at high CPU means
   it's working. (A render at *low* CPU that never finishes is usually a zero-length-`Line`
   Cairo hang in the frame source — kill it and fix the frame, it's not a dead producer.)
2. Is the last-written artifact a *terminal* one for its step? `audit/` fully populated with
   no fixes yet = it is reading, not dead.
3. `SendMessage` it. Delivery is queued "at its next tool round", so a reply proves life;
   silence proves nothing on its own.

Only if it stopped with a **task-notification carrying a failure status** is it genuinely
gone. Then take over from disk state rather than restarting the video — the pipeline
resumes, so rendered frames survive. If you already spawned a recovery agent and the
original turns out alive, `TaskStop` the recovery agent and verify it wrote nothing
(`find <dir> -newermt '-N minutes'`) before continuing.

## Other failures

| Symptom | Fix |
|---|---|
| ffmpeg size mismatch / "Stream specifier matches no streams" at compile | A frame mp4 is corrupt — re-render that frame, resume. |
| `FileNotFoundError: …/audio/frame_N.mp3` | TTS skipped a frame — re-run `generate_tts_elevenlabs.py <dir>/script.md`, resume. |
| Subtitle OOM / crash | Non-blocking — skip with `--from <next-step>`, tell the user subtitles need a rerun. |
| Subagent returns malformed JSON for a JSON step | Re-spawn once with the parse error appended; twice → inspect and surface. |
| "All complete" but no `final_video.mp4` | Run `compile_video.py` directly. |
