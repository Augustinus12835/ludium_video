# Phase B — per-video chain (math / technical)

Two stages, both spawned by the orchestrator. **Stage 1 (scripting)** is a subagent on your
most capable model that authors the complete script — narration AND every frame's `visual`
description — and reports to the orchestrator. **Stage 2 (production)** is a producer
subagent that takes the finished script through verification, codegen, render, compile,
subtitle, and audit. One author for spoken + shown keeps the two tracks consistent about
what's factual and which half carries a detail; the producer still does all review/QA and
applies QA-driven fixes.

Both agents: re-check disk state at each step and skip what's already complete. **Work in
your own scratchpad subdirectory** — `mkdir` one named for your video and stage (e.g.
`<scratchpad>/v3_script/`, `<scratchpad>/v3_prod/`) and keep every temp `.py`, prompt dump
and SymPy check inside it. Agents run in parallel and share one scratchpad root, so a
generic filename there is not just a content collision: a temp script named after a stdlib
module **shadows it** for anything run from that directory. A stray `struct.py` in the root
once broke `import sympy` for three concurrent producers at once, and the error it raised
(`AttributeError: module 'struct' has no attribute 'calcsize'`) points nowhere near the
cause. Never write a bare `verify.py`, `check.py`, `struct.py`, `types.py`, `json.py` to
the root.

## Stage 1 — scripting (reports to the orchestrator)

### 1. script

Render the exact prompt to your scratchpad:
`render_step_prompt.py script --video-dir <dir> --mode <math|technical> > <scratch>/script_prompt.json`.
Read it and follow it exactly, writing the JSON verbatim to `Video-N/script.json` (no
fences). The WHOLE script is yours — narration, `frame_class` declarations, and each frame's
`visual` description. You are the one mind across both tracks: what's shown must agree with
what's said (same values, same counts, same claims), and each detail lands in the half that
carries it best. Then regenerate `script.md`:

```bash
venv/bin/python -c "from pathlib import Path; from scripts.utils.script_parser import load_script, save_script; vd=Path('pipeline/<L>/Video-N'); save_script(load_script(vd), vd, write_json=False, write_md=True)"
```

Report to the orchestrator once the script is written. Review issues (below) come back to
you via `SendMessage`; apply every script-content fix yourself — you are the sole author of
`script.json`. After any narration change, recompute `word_count`/`timing`/`metadata.*` and
regenerate `script.md` LAST.

### 1b. script review (orchestrator-run; reviewer = clean-context subagent)

The script is the source of every downstream artifact, so it's reviewed before TTS/animation
money. The **orchestrator** spawns a separate clean-context reviewer — the author doesn't
grade its own work — with `script.json` + `script.md`, the source (`content.txt`,
`content_cleaned.txt`), and the mode. It returns an issue list to the orchestrator, which
relays it to the Stage-1 scripting agent; bounded to 2 rounds. The reviewer only reports —
it never edits `script.json` (single-writer rule; concurrent whole-file writes silently
clobber narration fixes).

What it checks: coverage and fidelity to the source (nothing invented, nothing important
dropped); narration↔visual consistency (the `visual` shows what the narration states —
counts, values, and claims match; nothing important spoken but unshown, or shown but never
spoken); `metadata.frame_count == len(frames)`, frame numbers gapless; pedagogy
(prerequisite-first order, a real hook and synthesis); TTS-safety of spoken text (pre-empt
the `narration_check.py` gate — no raw numerals/Greek/symbols/differentials/bare acronyms in
narration). The reviewer runs SymPy itself on each calculation; `frame_class` is right (a
`visual` frame skips the verify_math TTS rewrite, so its narration must already be
TTS-safe); displayed code/results match the narration.

**Source-error detection**: when a calculation or claim is wrong, decide whether the error
originates in the source (wrong in `content.txt` too) or is a script slip. Fix the script
either way, but flag every source-originated error (wrong→right value) — the Stage-1 return
must carry these so the orchestrator corrects the source files. Also flag vague placeholders
the script had to concretize.

### Stage 1 return

Frame count and per-class breakdown; review rounds and what changed; any source-originated
errors spotted at script time (`{what, where in content, wrong→right}`).

## Stage 2 — production (producer)

### 2. verify_math

Per frame, render
`render_step_prompt.py verify_math --video-dir <dir> --frame N --prior-context <ctxfile>`,
reason through the math, and run SymPy yourself (temp `.py` via `venv/bin/python`) to
confirm each step and the final answer. Maintain a running `math_context` across frames.
Write `Video-N/math_verification.json` in the schema the prompt's `notes` field specifies:
top-level `{"success": true, "video_title": …, "requires_math": true, "frames": {…}}`; math
frames `{"frame_type":"math","math_steps":[…],"final_answer":…,"natural_narration":
<TTS-safe spoken text>,"math_context":…}`; visual frames a minimal
`{"frame_type":"visual"}`. Your own SymPy execution is the verification — there is no
separate SymPy gate.

**`natural_narration` is spoken text → the scripting model.** Verify the math yourself
(SymPy, `math_steps`, `final_answer`, `math_context`), leaving `natural_narration` unset.
Then spawn ONE nested subagent (same capable model as Stage 1) for the video with every math
frame's verified steps, the script narration, and the prompt's TTS rules; it writes each
math frame's `natural_narration` (TTS-safe spoken text — this is what ElevenLabs reads
verbatim for math frames). This is a one-shot downstream rewrite — tell it plainly it has no
channel to peers and must put everything actionable in its final report. Merge its output
into `math_verification.json` before step 2b / tts.

### 2b. color plan (after all frames are verified)

Render `render_step_prompt.py color_plan --video-dir <dir>`, work out the video-wide
semantic color plan (2–6 recurring quantities → one palette color each, with exact tex forms
and note words), and insert it as the top-level `color_plan` key of
`math_verification.json` (`{}` if nothing recurs). Every Manim prompt injects it as the
VIDEO COLOR PLAN block — it's what keeps the same quantity the same color across all frames.

### 3. tts

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from tts --to tts --no-review --<mode>
```

If the pre-TTS narration gate halts it, triage per references/recovery.md ("narration
check"); `SKIP_NARRATION_CHECK=1` only for a confirmed false positive.

### 4. animate — frame authoring (the proven loop)

List the frames: `render_step_prompt.py manim --video-dir <dir> --pretty`.

Per `needs_authoring` frame N:

1. `render_step_prompt.py manim --video-dir <dir> --frame N` → `{system, user, notes}` is
   the exact codegen prompt. Read both fields. The `system` field IS
   `templates/manim_system_prompt.md` — its layout factories, safe zones, and rules are
   binding. Prompts carry the VIDEO COLOR PLAN block — apply it exactly (tex forms →
   `t2c=`, drawn graph objects → `.set_color()`, note words → `label_t2c=`).
2. Write the scene to `frames/frame_N_manim.py`. Before rendering, the cheap preflights
   catch most failures: `scripts/preflight_manim.py` (LaTeX dry-run) and
   `scripts/lint_manim_t2c.py`.
3. Render and eyeball as you go:
   ```bash
   venv/bin/python -c "
   from scripts.generate_math_animation import render_manim_scene
   from pathlib import Path
   p = Path('pipeline/<L>/Video-N/frames/frame_N_manim.py')
   ok,msg = render_manim_scene(p.read_text(), str(p.parent/'frame_N.mp4'), <dur>)
   print(('OK ' if ok else 'FAIL: ')+msg[-1500:])"
   ```
   `<dur>` is the frame's manifest `duration`. Extract a still and Read it; fix and
   re-render until clean, ≤3 attempts per frame, then note it in the summary. The eyeball
   includes color links: plan quantities visibly share their color; colors reveal with the
   Write (no white-then-pop).

After all frames, the color-link lint (warning-level):

```bash
venv/bin/python -c "from scripts.generate_math_animation import check_color_links; check_color_links('pipeline/<L>/Video-N')"
```

Fix real missed links and re-render that frame; a warning can be a false positive (quantity
off-screen that frame) — use judgment and note it.

### 5–6. finish + audit

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from animate --no-review --<mode>
```

(reuses the authored frames → compile → subtitle), then the frame audit: `audit_frames.py`
contact sheets + full-res stills, fix clear defects, re-render, recompile.

### Stage 2 return

Frames authored (by class); any frame needing >1 attempt and its bug; unresolved frames;
whether `final_video.mp4` exists and its duration; the audit result; and the
**`source errors corrected`** list — every source-originated error as
`{what, where in content, wrong→right}`, plus any placeholder concretized. The orchestrator
propagates those to the source files.
