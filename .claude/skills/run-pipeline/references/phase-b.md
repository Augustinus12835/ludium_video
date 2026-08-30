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
narration, and Greek-letter compounds hyphen-bound: `delta-X`, never `delta X`). The reviewer
runs SymPy itself on each calculation; `frame_class` is right; every frame's narration is
spoken verbatim (there is no downstream rewrite for any class), so it must already be
TTS-safe; displayed code/results match the narration. Also hunt phonetic respellings ("it
ter", "too pull") — they ship VERBATIM into the SRT and no gate flags them; reword around the
token instead.

**Measure, don't guess.** Every real finding on a 120-frame unit came from two things the
reviewer was told to do: (1) build representative `MathTex` in actual Manim, measure widths
against the **13.0 u × 7.4 u safe zone** (a Layout-B half column is ~5.8–7.0 u with ~6 rows
before auto-scroll) and simulate `make_step_column` against the declared row count — a
column that scrolls drops its TOP row, and three times that row was the frame's punchline;
(2) compute each beat's spoken offset at ~150 wpm and report, PER FRAME, the first-beat and
last-beat offsets and every span > 6 s with nothing scheduled — the most common defect is a
frame whose `visual` schedules nothing for its first 10–20 s, or a 25 s hold on one table
row. Every frame must put something on screen within ~5 s and name a beat per
sentence-group. Also verify: every `On "…"` cue phrase occurs EXACTLY ONCE in its frame's
narration (a later cue containing an earlier one as a substring misfires silently); a
terminal reveal has margin before the narration end (script seconds run 5–10 % long against
real TTS and compile trims anything past the audio); quoted on-screen note strings compile —
bare `^`, `_`, `\sin` outside `$…$` in a `Tex` note crash, `$` inside a `MathTex` spec
crashes the other way; no raw Unicode math glyphs (`×`, `⋯`, `→`) in strings bound for
MathTex; any `scale_to_fit_width` instruction is conditional ("if wider than 12.5 u") with
one shared scale per stack. Ask for the numbers, not a verdict — an author's "no gap > 10 s"
has been wrong by 20 s more than once.

**Cross-video duplication (every multi-video lecture).** The dominant defect on an 8-video
lecture was repetition BETWEEN videos: each script agent re-establishes context at the top of
its slice (recapping the class, re-deriving the helper for the third time) — often from
material outside its own slice. Give the reviewer `Video-<N-1>/script.json` explicitly (it
will not go looking) and make it check first: any frame whose code block AND narration
substantially repeat a sibling frame, and any material belonging to the sibling's slice; an
n-gram overlap against all earlier siblings separates real repetition from boilerplate. A
script's own claim that it is "deliberately different from the previous video" is not
evidence.

**Wording nits are claims, not corrections.** The reviewer verifies the author's maths with
SymPy but its own suggested prose is unverified; a nit that adds an explanatory aside ("by
coincidence", "because…") once introduced a provable falsehood. Relay new wording as
something the author must check; staging/timing/layout nits are safe.

**Source-error detection**: when a calculation or claim is wrong, decide whether the error
originates in the source (wrong in `content.txt` too) or is a script slip. Fix the script
either way, but flag every source-originated error (wrong→right value) — the Stage-1 return
must carry these so the orchestrator corrects the source files. Also flag vague placeholders
the script had to concretize. Adjudicate before propagating: grep the RAW transcript /
`content_cleaned.txt` / the textbook chapter. If the wording is the published source's own,
fix only the script and leave the source files alone. If the claim lives only in a **Key
Takeaways** bullet of `segments.json` / `content.txt`, it is OUR machine-authored artifact
(it has invented a polynomial the lecture never states) — fix `segments.json` + every
`Video-N/content.txt` copy, not `content_cleaned.txt`.

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
frames `{"frame_type":"math","math_steps":[…],"final_answer":…,"math_context":…}`; code
frames (technical) `{"frame_type":"code","code_steps":[…],"original_narration":<script
narration verbatim>}`; visual frames a minimal `{"frame_type":"visual"}`. Your own SymPy
execution is the verification — there is no separate SymPy gate.

**No spoken-text output from this step.** Do NOT write `natural_narration` (retired
2026-08-30 — it fixed nothing the TTS gate detects and silently mutated ~22% of verified
frames, breaking cue anchors). TTS, subtitles and codegen all read `script.json`'s
narration verbatim for every frame class. If SymPy shows a spoken value is WRONG, the fix is
a script fix: route the exact wrong→right wording to a one-shot subagent on the scripting
model that edits `script.json frames[N].narration` (keeping every `On "…"` cue phrase in
`visual.reference` verbatim), record it in `issues_found`, and flag it in your report.
Deliberate source rounding is not an error — `math_steps` show the value the narration
speaks; when a script follows the source's rounded path on purpose, make that explicit in
narration and in `visual.reference`, so nobody "corrects" it to more digits later.

Schema traps that ship silently: every `math_steps[]` entry needs **`operation`** (not
`description` — one consumer KeyErrors, the other renders an EMPTY gloss and the codegen
agent sees bare LaTeX); a `code` frame gets its FULL entry regardless of any "minimal
`{\"frame_type\": \"visual\"}` for non-math" wording in a rendered prompt — a code frame
written as `visual` loses both its traced data and the Code Block layout. Derive per-frame
entries from THIS video's `frame_class` counts; never carry a templated sentence over from
another video.

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
   binding; rules 36–66 are the silent-defect catalogue (everything that renders SUCCESS
   and is wrong) — read them before the first frame and again when a still looks "slightly
   off". Prompts carry the VIDEO COLOR PLAN block — apply it exactly (tex forms →
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

**Preflight + post-render checks (per frame) — each has caught a defect the render called
SUCCESS:**

- **Cues from the real audio, never the script's seconds.** Dump `audio/frame_N_timestamps.json`
  and resolve every `On "…"` cue phrase to its real time before authoring; estimate-vs-real
  drift reaches ±15 s per frame in both directions (a "never earlier than 84 s" guard once
  scheduled a reveal 4.5 s after the narrator moved on). Cheap triage on an authored frame:
  mp3 duration vs `word_count / 2.5` — a gap > 3 s means the whole schedule needs re-deriving.
- **Prove every probe can fail.** `preflight_manim.py` run from the wrong cwd prints CLEAN
  having compiled nothing; feed it a copy with `\frac{a` and confirm DIRTY. Same for every
  probe you write (clock sim, width probe, scroll detector, collision harness): a check that
  has never fired is untested, not reassuring — build a negative control. Eight collision
  harnesses in one week each passed a known-bad control for a different reason (zero-height
  `Line`, `Axes` bbox vs stroke, 4 bezier points, `sys.exit(0)` on import…). A collision
  audit needs three comparison classes — canvas edge, label × label, label × DRAWN GEOMETRY
  (polylines, dots, axes) — one class alone reports clean over the others' defects.
- **Width/height probe** — print `.width`/`.height` of every long expression against 13.0 u
  (safe) / the column width; fix by explicit breaks, not scale.
- **t2c glyph-count diff** — beyond `lint_manim_t2c.py`, compare the glyph count of the plain
  vs coloured `MathTex` for every `tex_to_color_map` expression (probe the mobject AWAY from
  the origin). Identical width + passing dry run + a dropped subscript is real.
- **Hand-rolled parallel renders**: never share a `--media_dir` between concurrent `manim`
  jobs — the Tex cache races and one dies with `installation does not support converting
  .dvi to SVG`, which is a LIE (dvisvgm is fine). `render_manim_scene()` and
  `preflight_manim.py` are already per-call safe. A batch script's "ALL RENDERS FINISHED"
  line proves nothing — count the mp4s.
- **Never patch a source while its render run is in flight** — the animate step reads every
  `frame_N_manim.py` into memory up front, so a mid-run patch renders from the OLD source with
  a NEWER mp4 mtime (the freshness guard passes). Kill, patch, re-render that frame alone,
  then verify the rendered OUTPUT with a measurement (bbox/stroke-pixel probe), not timestamps.
- **After render**: assert every `frames/frame_N.mp4` is LONGER than `audio/frame_N.mp3` (a
  short render silently drops the tail and drifts A/V across the video), and extract a still
  at the last scheduled reveal to prove compile's `-t` clamp won't cut it.
- `MANIM_RENDER_TIMEOUT` (default 1800 s) — a dense 4K frame that times out is resumed with a
  higher ceiling, never a lower resolution. A render at LOW CPU that never finishes is the
  zero-length-`Line` Cairo hang (rule 28), not slowness.

After all frames, the color-link lint (warning-level):

```bash
venv/bin/python -c "from scripts.generate_math_animation import check_color_links; check_color_links('pipeline/<L>/Video-N')"
```

Fix real missed links and re-render that frame. A warning means one of THREE things: a real
missed link; a norm-collision artifact (the lint strips `\`, `{}`, `()`, spaces and substring-
matches, so key `s(t)` → `st` fires on "step" and `\mu` on "the**m u**nder" — tighten the key,
e.g. `s(t) =`); or an uncolourable token (lives only inside a `\frac` numerator / `\sqrt` —
colour the enclosing quantity and note it). Leaving a quantity OUT of the plan is often right
when a frame prescribes its own local sign semantics; keep GREEN/RED_C free for sign accents.

### 5–6. finish + audit

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from animate --no-review --<mode>
```

(reuses the authored frames → compile → subtitle), then the frame audit: `audit_frames.py`
contact sheets + full-res stills, fix clear defects, re-render, recompile.

Audit notes: the "static ≥ 6 s" flag is a near-100 % FALSE positive on Manim frames (thin
strokes on black fall under its pixel-diff threshold) — count contact-sheet tiles before
acting on it; but real dead time IS the most common genuine defect — find it by budgeting
reveals against `audio/frame_N_timestamps.json`, and look at the EARLY tiles of any frame
> 20 s (a long verbal lead-in with a lone title). A t ≈ 2 s montage of every frame catches
black or title-only openings the sampler misses. Read the LATE phase of staged frames too.
Anything near a fraction bar / border / arrow needs a full-res still or a pixel probe, not a
thumbnail.

**After ANY frame re-render on a compiled video, run `compile_video.py` and
`generate_subtitles.py --video N --force` explicitly** — `pipeline.py … --from animate` keys
on `final_video.mp4` EXISTING and prints VIDEO COMPLETE over the stale build. Then assert
both directions of freshness: every `frame_N.mp4` newer than its `frame_N_manim.py` (else
an edit never rendered) and older than `final_video.mp4` (else a render never compiled).
Ten mp4s sharing one mtime after a cosmetic batch rewrite is a false alarm — settle it with
a re-render + pixel diff.

### Stage 2 return

Frames authored (by class); any frame needing >1 attempt and its bug; unresolved frames;
whether `final_video.mp4` exists and its duration; the audit result; and the
**`source errors corrected`** list — every source-originated error as
`{what, where in content, wrong→right}`, plus any placeholder concretized. The orchestrator
propagates those to the source files.
