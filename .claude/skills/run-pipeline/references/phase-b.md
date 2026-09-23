# Phase B — per-video chain (math / technical)

Two stages, both spawned by the orchestrator. **Stage 1 (scripting)** authors the complete
script — narration and every frame's `visual` description — and reports to the orchestrator.
**Stage 2 (production)** takes the finished script through verification, codegen, render,
compile, subtitle and audit. One author for spoken + shown keeps the two tracks consistent;
review and QA stay with other agents.

**Orchestrator: brief by reference.** Spawn each Stage-1, reviewer and Stage-2 agent with a
short prompt that names the video, mode and the section of this file to read, plus the facts
specific to this video (audit numbers, fixes already applied, scope boundary against
siblings). Don't paraphrase this file into the brief — the agent can read it, and a paraphrase
drifts.

Both agents re-check disk state at each step and skip what's already complete. **Work in your
own scratchpad subdirectory** named for your video and stage (e.g. `<scratchpad>/v3_script/`,
`<scratchpad>/v3_prod/`) and keep every temp `.py`, prompt dump and SymPy check there.
Parallel agents share one scratchpad root, and a temp file named after a stdlib module
(`struct.py`, `types.py`, `json.py`) shadows it for anything run from that directory.

## Stage 1 — scripting (reports to the orchestrator)

### 1. script

Render the prompt to your scratchpad:
`render_step_prompt.py script --video-dir <dir> --mode <math|technical> > <scratch>/script_prompt.json`.
Follow it and write the JSON verbatim to `Video-N/script.json` (no fences). Narration,
`frame_class` declarations and each frame's `visual` are all yours: what's shown must agree
with what's said (same values, counts, claims), and each detail lands in the half that carries
it best. Then regenerate `script.md`:

```bash
venv/bin/python -c "from pathlib import Path; from scripts.utils.script_parser import load_script, save_script; vd=Path('pipeline/<L>/Video-N'); save_script(load_script(vd), vd, write_json=False, write_md=True)"
```

**Colour is not yours to choose.** The rendered prompt carries the lecture's
`color_scheme.json` as background. Write every `visual` in terms of the quantity ("the slope",
"the step size"); the colour follows downstream. Name a raw colour only where the frame's
meaning depends on it (a red warning, "the two curves must read as distinct"), and only one the
scheme hasn't committed elsewhere.

**The `visual` is a shot description, not a build spec.** Say what is on screen, roughly where
(left / right / top band / two-column split), when each thing appears (the `On "…"` cue
phrase — verbatim, occurring exactly once in the narration) and what changes (grows, is struck
through, slides into the left panel). Roughly 400–900 characters per frame; a worked-example
frame may run longer. No canvas coordinates, measured widths, `scale` values, Manim
class/method names, arc angles, or restated Manim rules — the codegen system prompt owns those,
and a copy in the script drifts from it. Fit is not yours either: write "must fit the right
column" or "break after the equals sign"; the producer measures the real mobject (Stage 2
step 4).

**Multi-tier stacks (Layout C).** A reference may reserve a full-width bottom band or ask for
a multi-tier stack, not both. Layout C holds three tiers — two if any row is long, two-line or
a boxed result. A fourth tier means the supporting picture becomes a corner inset or a half
column. Scaling down does not buy tiers (most of each tier's height is fixed spacing), so the
fix for an overflowing stack is fewer rows: merge a definition with its conclusion, or lay a
triple out as one horizontal row. State the tier count in the reference ("four tiers plus a
bottom sketch") so reviewer and producer can see the hazard. The question is not "does my
content fit" but "does the last tier push the first off the top".

Report to the orchestrator once the script is written. Review issues come back via
`SendMessage`; apply every script-content fix yourself — you are the sole writer of
`script.json`. After any narration change, recompute `word_count`/`timing`/`metadata.*` and
regenerate `script.md` last.

### 1b. script review (orchestrator-run; reviewer = clean-context subagent)

The script is the source of every downstream artifact, so it's reviewed before TTS and render
money is spent. The orchestrator spawns a clean-context reviewer with `script.json` +
`script.md`, the source (`content.txt`, `content_cleaned.txt`, `source_lecture_notes.md` if
present) and the mode. It returns an issue list to the orchestrator, which relays it to the
Stage-1 agent; bounded to 2 rounds. The reviewer only reports — it never edits `script.json`
(concurrent whole-file writes clobber each other).

**Run the mechanical audit first (orchestrator):**

```bash
venv/bin/python scripts/audit_script.py pipeline/<L>/Video-N --siblings
```

It covers the string arithmetic in seconds, tiered BLOCK / CHECK: cue uniqueness and
collisions, listed cues that leave a phrase unscheduled, final-cue margin, first-beat latency,
unscheduled gaps, metadata consistency, the TTS gate, digits in spoken text, the scope gate on
`visual.reference` (widths, scales, coordinates, Manim API, rules blocks), colour words,
`Tex()`-crashing note strings, and n-gram overlap with siblings. Paste its output into the
reviewer's brief and tell the reviewer not to redo those checks. A BLOCK on final-cue margin is
fixed by retargeting the cue 2–3 s earlier, never by adding words at the end.

The reviewer's effort goes to what needs judgement:

- **Coverage and fidelity** to the source — nothing invented, nothing important dropped.
- **Narration ↔ visual agreement** — counts, values and claims match; nothing important
  spoken but unshown or shown but never spoken; `metadata.frame_count == len(frames)`.
- **Pedagogy** — prerequisite-first order, a real hook and synthesis.
- **Mathematics** — the reviewer runs SymPy on each calculation.
- **Fit and scroll** — measured, not guessed, with the shared prober:

  ```python
  from scripts.utils.manim_probe import simulate_stack, measure, compile_check, glyph_parity
  simulate_stack(rows, scale=0.75, step_buff=0.28, notes=[...])   # scroll verdict + per-row detail
  measure(r"\frac{u}{v}", scale=0.75)                             # width/height/glyphs, T1 preamble
  ```

  It uses the render's T1 `fontenc` preamble (a bare `from manim import *` probe measures
  OT1, which we never ship) and `make_step_column`'s real geometry. Widths go against the
  13.0 u × 7.4 u safe zone (a Layout-B half column is ~5.8–7.0 u). The scroll trigger is the
  `board_top` 2.3 → `scroll_bottom` −3.2 band (~5.5 u usable, ~6.3 u when the first row is
  tall), not the 7.4 u safe height; overflow drops the top row, usually the punchline. Check
  references against the Layout C rule in Stage 1. Report the simulation as a table over
  candidate sizes.
- **Mannered prose** — metaphor or flourish standing in for a literal statement, or a
  metaphor whose connotations assert mechanics that aren't true. Report location and why; the
  author rewrites. Full rule: SKILL.md "Narration style".

**Fit findings go to the author as direction, to the producer as numbers.** "Step 4 is ~15 u
at the stack's scale; break after the equals sign" — the author makes the directorial fix
(line break, two-line form, one fewer column) and never transcribes a width into the
`visual`. The producer re-measures in code.

**Reviewer wording is a claim, not a correction.** The reviewer's suggested prose is
unverified; an added explanatory aside can introduce a falsehood. Relay new wording as
something the author must check. Staging/timing/layout nits are safe.

**Source-error detection.** When a calculation or claim is wrong, decide whether it originates
in the source (wrong in `content.txt` too) or is a script slip. Fix the script either way, and
report every source-originated error (wrong→right) so the orchestrator corrects the source
files; also report vague placeholders the script had to concretize. Adjudicate against the raw
transcript / `content_cleaned.txt` / the textbook chapter:
- Wording that is the published source's own → fix only the script.
- A claim that lives only in a Key Takeaways bullet of `segments.json` / `content.txt` is our
  machine-authored artifact → fix `segments.json` and every `Video-N/content.txt` copy, not
  `content_cleaned.txt`.

**Frame class.** A frame declared `visual` skips `verify_math` entirely (no steps, no math
layout, no colour-link lint), so a frame that puts a real identity on screen but is filed
`visual` is a displayed result nothing checks. This is the most repeated script defect, usually
on the frame doing the video's characteristic computation; look for identities composed rather
than copied from `content.txt`. Narration is spoken verbatim for every class, so it must
already be TTS-safe; displayed code and results match the narration.

**Cross-video duplication (multi-video lectures).** Script agents tend to re-establish context
at the top of their slice — recapping, re-deriving a helper already derived — often from
outside their own slice. Give the reviewer `Video-<N-1>/script.json` explicitly and have it
check for frames whose code and narration substantially repeat a sibling, and for material
belonging to a sibling's slice (the audit's n-gram overlap flags candidates). A script's claim
that it is "deliberately different" is not evidence. Also flag phonetic respellings ("it ter",
"too pull") — they ship verbatim into the SRT; reword around the token.

**Cross-lecture claims** in a sequential course must be grounded in the prior lecture's
source, not memory: agents both re-teach earlier material without a back-pointer and invert
it (attributing a call to the wrong class). Hand the reviewer the prior lecture's source code /
notes and its `segments.json`; video titles alone often catch an inversion.

### Stage 1 return

Frame count and per-class breakdown; review rounds and what changed; any source-originated
errors spotted at script time (`{what, where in content, wrong→right}`).

## Stage 2 — production (producer)

### 2. verify_math

Per frame, render
`render_step_prompt.py verify_math --video-dir <dir> --frame N --prior-context <ctxfile>`,
work through the math, and run SymPy yourself (temp `.py` via `venv/bin/python`) on each step
and the final answer. Keep a running `math_context` across frames. Write
`Video-N/math_verification.json`: top-level
`{"success": true, "video_title": …, "requires_math": true, "frames": {…}}`; math frames
`{"frame_type":"math","math_steps":[…],"final_answer":…,"math_context":…}`; code frames
(technical) `{"frame_type":"code","code_steps":[…],"original_narration":<script narration
verbatim>}`; visual frames `{"frame_type":"visual"}`. Your SymPy run is the verification —
there is no separate gate.

This step produces no spoken text (the old `natural_narration` field is legacy-only; TTS,
subtitles and codegen read `script.json` narration verbatim). If SymPy shows a spoken value is
wrong, edit `script.json frames[N].narration` with the exact wrong→right wording, keeping every
`On "…"` cue phrase verbatim, record it in `issues_found`, and report it. Deliberate source
rounding is not an error: `math_steps` show the value the narration speaks, and the rounding
should be explicit in narration and `visual.reference` so nobody "corrects" it later.

Schema facts that fail silently:
- Every `math_steps[]` entry needs **`operation`** (not `description`) — one consumer
  KeyErrors, the other renders an empty gloss.
- A **`code` frame gets its full entry** even if a rendered prompt says "minimal
  `{"frame_type": "visual"}` for non-math"; written as `visual` it loses its traced data and
  the Code Block layout.
- Derive entries from this video's `frame_class` counts; never carry text over from another
  video.

### 2b. color plan (after all frames are verified)

Render `render_step_prompt.py color_plan --video-dir <dir>` and insert the result as the
top-level `color_plan` key of `math_verification.json` (`{}` if nothing recurs). Every Manim
prompt injects it as the VIDEO COLOR PLAN block.

The palette comes from the lecture's `color_scheme.json`, which the rendered prompt carries as
a binding inheritance. Copy every scheme entry whose quantity appears in this video with its
colour unchanged, extend its `tex` list with any forms this video adds, then add entries for
video-local quantities in colours the scheme hasn't used. Never re-colour a scheme quantity or
reuse a scheme colour for something else — that's the cross-video drift the scheme prevents.
Copy each inherited entry's `scope` (keying cautions, e.g. `T` inside `T_e`, `c` inside
`\cos`) into your plan, trimmed to this video: codegen sees it as that quantity's `caution:`
and sees nothing else of the scheme.

- **Accents are fixed.** A final answer is boxed in the system prompt's green and an error is
  marked RED_C, even when the scheme spends those colours on a quantity. Never substitute your
  own shade. A lecture that needs different accents sets `_accents` in `color_scheme.json`.
- **One meaning per colour on screen.** A non-scheme quantity can still collide with a scheme
  one in a frame; re-colour the non-scheme one and say so in your report.
- **~3 colour links per frame.** Colour the most central and leave the rest default — don't
  drop them from the plan.

If a `visual` names a raw colour, treat it as a deliberate local accent and check it against
the scheme before honouring it.

### 3. tts

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from tts --to tts --no-review --<mode>
```

If the pre-TTS narration gate halts it, triage per references/recovery.md ("narration
check"); `SKIP_NARRATION_CHECK=1` only for a confirmed false positive.

### 4. animate — frame authoring

List the frames: `render_step_prompt.py manim --video-dir <dir> --pretty`.

The codegen system prompt is identical for every frame (it is
`templates/manim_system_prompt.md` filtered to the mode, ~55 KB; rules 36–80 catalogue defects
that render SUCCESS and are still wrong). Render it once per video and keep it:
`render_step_prompt.py manim --video-dir <dir> --frame <first N> --system-only >
<scratch>/codegen_system.json`. Go back to the catalogue when a still looks off.

Per `needs_authoring` frame N:

1. `render_step_prompt.py manim --video-dir <dir> --frame N --user-only` → the frame-specific
   half (narration, steps, word transcript, VIDEO COLOR PLAN). Apply the plan exactly (tex
   forms → `t2c=`, drawn objects → `.set_color()`, note words → `label_t2c=`).
2. Write `frames/frame_N_manim.py`. Cheap preflights before rendering:
   `scripts/preflight_manim.py` (LaTeX dry run) and `scripts/lint_manim_t2c.py`.
3. Render and look:
   ```bash
   venv/bin/python -c "
   from scripts.generate_math_animation import render_manim_scene
   from pathlib import Path
   p = Path('pipeline/<L>/Video-N/frames/frame_N_manim.py')
   ok,msg = render_manim_scene(p.read_text(), str(p.parent/'frame_N.mp4'), <dur>)
   print(('OK ' if ok else 'FAIL: ')+msg[-1500:])"
   ```
   `<dur>` is the frame's manifest `duration`. Extract a still and Read it; fix and
   re-render, ≤3 attempts per frame, then note it in the summary. Plan quantities should
   visibly share their colour, and colours reveal with the Write (no white-then-pop).

**Manim specifics** — each of these catches something that renders SUCCESS:

- **Cues from the real audio.** Resolve every `On "…"` cue against
  `audio/frame_N_timestamps.json` before authoring; script-estimate drift reaches ±15 s per
  frame. Quick triage on an authored frame: mp3 duration vs `word_count / 2.5` — a gap > 3 s
  means the schedule needs re-deriving.
- **LaTeX dry run** — run `preflight_manim.py` from the repo root; from the wrong cwd it
  reports clean having compiled nothing.
- **Width/height** — measure with `scripts/utils/manim_probe.py` (`measure`, `simulate_stack`)
  rather than a hand-written probe; it carries the render's T1 preamble and real
  `make_step_column` geometry. Measure every long expression and label row against 13.0 u /
  the column width before placing, then apply the fit in code, unconditionally, one shared
  scale per stack (rules 36–39). A line over by more than ~10 % gets an explicit break, not a
  smaller scale. Ignore any width in the script's `visual`.
- **Collision checks** need three classes: canvas edge, label × label, and label × drawn
  geometry (polylines, dots, axes) — one class alone reports clean over the others.
- **t2c glyph diff** — beyond `lint_manim_t2c.py`, compare glyph counts of the plain vs
  coloured `MathTex` for every `tex_to_color_map` expression (probe it away from the origin).
  Same width + passing dry run + a dropped subscript is a real defect.
- **Hand-rolled parallel renders**: never share a `--media_dir` between concurrent `manim`
  jobs — the Tex cache races and one dies with a misleading "does not support converting .dvi
  to SVG". `render_manim_scene()` and `preflight_manim.py` are already per-call safe. Count the
  mp4s rather than trusting a batch script's "finished" line.
- **Never kill renders by pattern** (`pkill -f "manim render"`) — sibling producers share the
  machine. Kill by PID (see recovery.md, "Background jobs").
- **Don't patch a source while its render run is in flight** — the animate step reads every
  `frame_N_manim.py` up front, so a mid-run patch renders the old source with a newer mp4
  mtime. Kill, patch, re-render that frame alone, then verify the output itself (still or
  pixel probe), not timestamps.
- **After render**: every `frames/frame_N.mp4` must be longer than `audio/frame_N.mp3` (a
  short render drops the tail and drifts A/V), and a still at the last scheduled reveal
  confirms compile's `-t` clamp won't cut it.
- `MANIM_RENDER_TIMEOUT` (default 1800 s): resume a timed-out dense 4K frame with a higher
  ceiling, never a lower resolution. A render at low CPU that never finishes is the
  zero-length-`Line` Cairo hang (rule 28).

After all frames, the colour-link lint (warning-level):

```bash
venv/bin/python -c "from scripts.generate_math_animation import check_color_links; check_color_links('pipeline/<L>/Video-N')"
```

A warning is one of three things: a real missed link (fix, re-render); a normalisation
collision (the lint strips `\`, `{}`, `()`, spaces and substring-matches, so `s(t)` → `st`
fires on "step" — tighten the key, e.g. `s(t) =`); or an uncolourable token inside a `\frac`
numerator / `\sqrt` (colour the enclosing quantity and note it). Leaving a quantity out of the
plan is often right when a frame sets its own local sign semantics.

### 5–6. finish + audit

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from animate --no-review --<mode>
```

(reuses the authored frames → compile → subtitle), then the frame audit: `audit_frames.py`
contact sheets + full-res stills, fix clear defects, re-render, recompile.

Audit notes: the "static ≥ 6 s" flag is nearly always a false positive on Manim frames (thin
strokes on black fall under its pixel-diff threshold) — count contact-sheet tiles before acting
on it. Real dead time is the most common genuine defect: budget reveals against
`audio/frame_N_timestamps.json` and look at the early tiles of any frame > 20 s (a long verbal
lead-in under a lone title). A t ≈ 2 s montage of every frame catches black or title-only
openings. Anything near a fraction bar, border or arrow needs a full-res still.

**After any frame re-render on a compiled video**, run `compile_video.py` and
`generate_subtitles.py --video N --force` explicitly — `pipeline.py … --from animate` sees
`final_video.mp4` and reports complete over the stale build. Then check freshness both ways:
every `frame_N.mp4` newer than its source and older than `final_video.mp4`. The code's own
freshness guards are described in CLAUDE.md ("Patching a frame source…",
"`final_video.mp4` existing is not evidence…"); they prove the file is playable, not that it
holds the right footage — before reporting done, confirm duration ≈ the sum of the decoded
audio durations.

### Stage 2 return

Frames authored (by class); any frame needing >1 attempt and its bug; unresolved frames;
`final_video.mp4` duration; the audit result; and the **`source errors corrected`** list —
every source-originated error as `{what, where in content, wrong→right}`, plus any placeholder
concretized. The orchestrator propagates those to the source files.
