# Phase B — per-video chain (math / technical)

Two stages, both spawned by the orchestrator. **Stage 1 (scripting)** is a subagent on your
most capable model that authors the complete script — narration AND every frame's `visual`
description — and reports to the orchestrator. **Stage 2 (production)** is a producer
subagent that takes the finished script through verification, codegen, render, compile,
subtitle, and audit. One author for spoken + shown keeps the two tracks consistent about
what's factual and which half carries a detail; the producer still does all review/QA and
applies QA-driven fixes.

**Orchestrator: brief by REFERENCE, not by transcription.** Spawn each Stage-1, reviewer and
Stage-2 agent with a short prompt that (a) names the video, mode and playbook section to read,
(b) carries the handful of facts specific to THIS video — the measured numbers from the audit,
the fixes the author already applied, the scope boundary against its siblings — and (c) stops.
Do not restate this file in the prompt. On one lecture the orchestrator hand-wrote five
~2,500-word reviewer briefs and five ~2,500-word producer briefs that were each ~80 % a
paraphrase of the playbook; that is output tokens spent to produce a worse copy of a file the
agent can read, and the paraphrases drift from the original the moment this file changes. The
per-video facts are the only part that cannot live here.

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

**Colour is not yours to choose.** When the lecture has a staged `color_scheme.json`, the
rendered prompt already carries it as background — which quantity wears which colour, for
the whole lecture. Write every `visual` in terms of the QUANTITY ("the slope", "the step
size"); the colour follows it automatically downstream, so you never need to mention one.
Name a raw colour only where the frame's meaning depends on it (a red warning, a gold boxed
result, "the two curves must read as distinct") and only in a colour the scheme has not
committed elsewhere. Hard-coding your own colour words makes the script fight the
producer's plan.

**The `visual` is a shot description, not a build spec.** You are the director: say WHAT is
on screen, roughly WHERE (left / right / top band / a two-column split), WHEN each thing
appears (the `On "…"` cue phrase — verbatim, occurring exactly once in the narration) and what
CHANGES (grows, is struck through, slides into the left panel). Roughly 400–900 characters per
frame; a worked-example frame may run longer. It never contains canvas coordinates, measured
widths ("measured 7.165u at 1.0; show 1.15 -> 8.24u"), `scale` values, Manim class or method
names (`Tex`, `Ellipse`, `set_stroke`, `Indicate`), arc angles, or any restatement of the Manim
rules. The codegen system prompt owns every one of those, and a rules block copied into the
script drifts from it — one shipped copy banned `tex_to_color_map` and `Indicate(color=)`,
both of which the real system prompt uses in its own examples — and is then handed to the
producer once per frame next to the authoritative version. Fit is not yours either: write
"must fit the right column" or "break the long line after the equals sign", and the producer
measures the actual mobject (Stage 2 step 4). On one production video the `visual` was 87 % of
`script.json` (63 k of 73 k chars); the author measured 108 labels in Manim, the reviewer
measured them again, and the producer — the only one holding the code the number applies to —
measured them a third time, while sibling videos on the identical prompt ran at a quarter of
the size with nothing lost. Detail belongs in the narration and in the cue phrases; everything
else in the reference is cost, not direction.

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

**Run the mechanical audit FIRST — the orchestrator does this, not the reviewer.**

```bash
python scripts/audit_script.py pipeline/<L>/Video-N --siblings
```

It does, in ~2 s, the half of this review that is pure string arithmetic: cue uniqueness
(case-insensitive AND punctuation-free), cue substring/prefix collisions, cue-span overlaps,
final-cue margin measured from where the phrase ENDS, first-beat latency, every span > 6 s
with nothing scheduled, `metadata` consistency, the TTS gate, digits in spoken text, the
calibrated `visual.reference` scope regexes, colour words, on-screen strings that would crash
`Tex()`, and n-gram overlap against sibling videos. Findings come back tiered BLOCK / CHECK.
`--self-test` proves every detector fires on a known-bad input and stays silent on a clean
one; run it if you ever doubt a clean result.

**Paste its output into the reviewer's brief and tell the reviewer NOT to redo those checks.**
Before this existed, five reviewers on one lecture each re-authored the same cue checker,
margin calculator, gap table and overlap probe — roughly half of 1.08M tokens spent on
arithmetic. The reviewer's tokens should go to what needs judgement: the mathematics (SymPy),
fit/scroll simulation, source fidelity, pedagogy, narration↔visual agreement, and mannered
prose.

Two calibration facts the audit encodes, so nobody re-derives them the hard way: cue phrases
appear as BOTH `On "…"` and lowercase `on "…"` (one script ran 37 % lowercase, and a
case-sensitive probe invented three dead spans that did not exist), and an absolute
`len(reference) > 1200` scope trigger is a FALSE POSITIVE for this corpus — a shipped-good
119-frame sample has a median of 1,186 chars with 44 % of known-good frames above 1,200, so
the audit reports chars-per-narration-word (median 10.3, p90 15.4) as context instead.

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

**Measure, don't guess.** Ask the reviewer for numbers, not verdicts. Use the shared prober —
do NOT re-author one:

```python
from scripts.utils.manim_probe import simulate_stack, measure, compile_check, glyph_parity
simulate_stack(rows, scale=0.75, step_buff=0.28, notes=[...])   # scroll verdict + per-row detail
measure(r"\frac{u}{v}", scale=0.75)                             # width/height/glyphs, T1 preamble
```

It injects the same T1 `fontenc` preamble the render uses (rule 67 — a bare `from manim
import *` probe measures OT1, an encoding we never ship, so its widths AND its crash verdicts
are wrong), replicates `make_step_column`'s real geometry including that the first group is
**centred** at `board_top` rather than hung from it, measures actual note labels, and carries
a deliberate pessimistic `safety` allowance because it is still an approximation. Run
`python scripts/utils/manim_probe.py --self-test` to see every probe fire on a control.

(1) Build representative `MathTex` in actual Manim and measure widths against the **13.0 u ×
7.4 u safe zone** (a Layout-B half column is ~5.8–7.0 u wide). Simulate `make_step_column`
against the declared row count with the config that will actually ship — `font_size`/`scale`,
`step_buff`, AND `add_step`'s note label (~0.12 u per row even when the label is empty).
**The scroll trigger is the `board_top` 2.3 → `scroll_bottom` −3.2 band — ~5.5 u usable
(~6.3 u when the first row is tall) — NOT the 7.4 u safe height**: a stack checked against
7.4 u "doesn't scroll", does, and drops its TOP row, which is usually the punchline. Report the
simulation as a table over candidate sizes and confirm on a rendered still at the punchline
beat, not on the arithmetic alone. (2) Compute each beat's spoken offset at ~150 wpm and
report, PER FRAME, the first-beat and last-beat offsets and every span > 6 s with nothing
scheduled — the most common defect is a `visual` that schedules nothing for its first 10–20 s
or holds one table row for 25 s. Every frame must put something on screen within ~5 s and name
a beat per sentence-group. Also verify: every `On "…"` cue phrase occurs EXACTLY ONCE in its
frame's narration (a later cue containing an earlier one as a substring misfires silently); a
terminal reveal has margin before the narration end (script seconds run 5–10 % long against
real TTS and compile trims anything past the audio); quoted on-screen note strings compile
(bare `^`, `_`, `\sin` outside `$…$` in a `Tex` note crash, `$` inside a `MathTex` spec crashes
the other way); no raw Unicode math glyphs (`×`, `⋯`, `→`) in strings bound for MathTex.

**Fit findings go to the AUTHOR as direction and to the PRODUCER as numbers.** The reviewer's
own width simulation (item 1 above) decides whether a line fits its column. When one does not,
the fix is directorial and the author makes it — an explicit line break, a two-line form, one
fewer column — reported as "step 4 is ~15 u at the stack's scale; break after the equals sign".
It is never a measured width for the author to transcribe into the `visual`: the number only
means something against the actual mobject, which exists in Stage 2. The producer re-measures
every long expression in code and applies the fit unconditionally, one shared scale per stack
(step 4's width probe; system-prompt rules 36–39). History, so the lesson survives: this file
once required the `scale_to_fit_width` instruction to be *conditional*, and a 1.6 u overflow
shipped when codegen eyeballed it; requiring the AUTHOR to state measured widths instead
produced, within two days, scripts carrying 60–108 Manim-measured labels each plus an
1,800-char rules block on every frame. Both put the number in the wrong layer. The
`0.222 × chars + 2.2` formula **under**-estimates at `font_size=32` in six of seven recorded
cases, by up to 1.29 u, so it is only ever a trigger to go and measure — in Stage 2.

**Scope gate on `visual.reference`.** Flag any frame whose reference contains a measured width,
a canvas coordinate, a `scale` value, a Manim class or method name, a `CLASS RULES`-style
block, or runs past ~1,200 characters (worked-example frames excepted). The fix is to cut it
back to what / where / on-which-phrase — never to pad the other frames up to match. Cheap
detector:

```python
import json, re
for f in json.load(open("script.json"))["frames"]:
    r = f["visual"]["reference"]
    hits = re.findall(r"measured [\d.]+u|CLASS RULES|\b(?:Tex|MathTex|Ellipse|VGroup|set_stroke|Indicate)\(|\(-?\d\.\d, -?\d\.\d\)", r)
    if hits or len(r) > 1200: print(f["number"], len(r), hits[:4])
```

**Cross-video duplication (every multi-video lecture).** The dominant defect on an 8-video
lecture was repetition BETWEEN videos: each script agent re-establishes context at the top of
its slice (recapping the class, re-deriving the helper for the third time) — often from
material outside its own slice. Give the reviewer `Video-<N-1>/script.json` explicitly (it
will not go looking) and make it check first: any frame whose code block AND narration
substantially repeat a sibling frame, and any material belonging to the sibling's slice; an
n-gram overlap against all earlier siblings separates real repetition from boilerplate. A
script's own claim that it is "deliberately different from the previous video" is not
evidence.

**Mannered prose is a defect, not a taste question.** Check the narration for metaphor and
flourish standing in for direct statement — "a dial worth turning" for "a parameter worth
varying", "earns its keep" for "still matters". Two tests that separate decoration from a
working analogy: (1) is a literal phrase available and shorter? then use it; (2) do the
metaphor's connotations assert mechanics the code does not have? "the parent hands the method
down" implies a copy is transferred and is simply false about attribute lookup. Narration is
heard once and cannot be re-read, so a figure a reader would decode in a beat is a sentence the
listener loses. Report the location and why the literal statement is better, and let the AUTHOR
rewrite — per the rule immediately below.

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

**Cross-LECTURE claims must be checked against the PRIOR LECTURE'S SOURCE, not from memory.**
In a sequential course, script agents reach back to the previous lecture for continuity — and
they get it wrong in two distinct ways, both seen on one programming lecture:
- **Re-teaching it.** One video's opening frame reproduced an *already-published* video from two
  lectures earlier (same beats, same illustration, no back-pointer), burning 12 % of its runtime
  telling the viewer something they had been told twice. Only found because the reviewer went and
  read the earlier lecture.
- **Inverting it.** Another video's continuity beat said an earlier class "called the parent's
  `__init__` by name" — but that class is precisely the one with NO `__init__`, the subject of an
  earlier video; a sibling class makes that call. A viewer who watched the prior lecture hears
  the new one contradict it.
Neither is detectable from the current lecture's own files. So when a course has predecessors,
hand the reviewer the prior lecture's source code / notes **and** its `segments.json`, and
require every cross-lecture assertion to be grounded in one of them — video titles alone are
often enough to catch an inversion.

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

**You are not choosing the palette.** The lecture's colour scheme was decided once in Phase A
and staged at `pipeline/<L>/color_scheme.json`; the rendered prompt carries it as a binding
inheritance. Your plan **copies every scheme entry whose quantity appears in this video,
colour unchanged**, extends its `tex` list with any forms this video adds, and only then adds
entries for genuinely video-local quantities — in colours the scheme has not used. Never
re-colour a scheme quantity and never re-use a scheme colour for something else: that is
precisely the cross-video drift the scheme exists to stop (one quantity teal in Video-1 and
gold in Video-2 while every video's own lint passed).

⚠️ **A quantity that RECURS across videos but was left OUT of the scheme will drift — and the
selection rules actively push such quantities out.** The scheme caps at 3-7 entries and asks you
to keep GREEN/RED_C free, so a genuinely recurring quantity can lose its slot to those
constraints. On one nine-video lecture a per-kilometre rate was deliberately excluded to keep
RED_C free as the lecture's error accent; it then appeared in **four of nine videos**, and their
producers independently chose SAND (three of them) and GOLD (one) — the exact per-video drift
the scheme exists to stop, arrived at by following the scheme's own rules. (That pair was
near-identical in practice — delta (26,5,11) in RGB, indistinguishable as text — so it did not
warrant re-rendering a shipped video, but the mechanism will not always be so forgiving.)

**So: count a candidate's videos before dropping it.** If a quantity appears in ≥3 videos, it
belongs in the lecture scheme even if that means spending GREEN or RED_C, or exceeding seven
entries. If you must leave one out, name it in the scheme's `scope` text **with the colour every
video must give it**, so the constraint travels with the plan instead of being rediscovered.

Two things still on you, because the scheme cannot know them:
- **A colour must mean one thing at a time on screen.** A quantity outside the scheme may
  still collide with one inside it — gold doing double duty as both the true solution curve
  and a second slope makes them indistinguishable. If two things share a colour in one frame,
  re-colour the non-scheme one and say so in your report.
- **The ~3-links-per-frame cap still applies.** If more scheme quantities land on a frame than
  that, colour the most central and leave the rest default — don't drop them from the plan.

The script's `visual` fields are written in quantities, not colour words, precisely so this
step (and codegen) owns the mapping. If a `visual` names a raw colour, treat it as a
deliberate local accent the author needed, and check it against the scheme before honouring it.

### 3. tts

```bash
venv/bin/python scripts/pipeline.py video <L>/Video-N --from tts --to tts --no-review --<mode>
```

If the pre-TTS narration gate halts it, triage per references/recovery.md ("narration
check"); `SKIP_NARRATION_CHECK=1` only for a confirmed false positive.

### 4. animate — frame authoring (the proven loop)

List the frames: `render_step_prompt.py manim --video-dir <dir> --pretty`.

**Read the codegen SYSTEM prompt once per video, not once per frame.** It is identical for
every frame (it IS `templates/manim_system_prompt.md` — its layout factories, safe zones and
rules are binding; rules 36–73 are the silent-defect catalogue, everything that renders
SUCCESS and is wrong), ~90 KB, and the single largest thing in your context, so render it
once and keep it: `render_step_prompt.py manim --video-dir <dir> --frame <first N>
--system-only > <scratch>/codegen_system.json`. Re-read the catalogue when a still looks
"slightly off", not before every frame.

Per `needs_authoring` frame N:

1. `render_step_prompt.py manim --video-dir <dir> --frame N --user-only` → `{user, notes}`
   is the frame-specific half of the codegen prompt (narration, steps, word transcript,
   VIDEO COLOR PLAN). The system half is the one you already read. Apply the plan exactly
   (tex forms → `t2c=`, drawn graph objects → `.set_color()`, note words → `label_t2c=`).
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
- **Width/height probe — yours, not the script's. Import it from `scripts/utils/manim_probe.py`; do not re-author it.** Print `.width`/`.height` of every long
  expression and every label row against 13.0 u (safe) / the column width BEFORE placing
  anything, then apply the fit in code from the measured number, unconditionally, one shared
  scale per stack (rules 36–39). A line over by more than ~10 % is fixed by an explicit break,
  not by scale. The script's `visual` carries no widths, coordinates or scales by design
  (Stage 1 §1); if one does, ignore the number and measure — it was taken on a mobject that did
  not exist yet. A 1.6 u overflow once shipped when fit was left to eyeballing.
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

**Freshness guards the code enforces (no need to `rm` an mp4 first):** `prepare_frame_code()`
re-renders any frame whose `frame_N_manim.py` is newer than its mp4 (source only — the mp3 is
deliberately ignored so `fix_tts_sentence.py` never triggers a re-render), and
`detect_video_state()` / `verify_compilation()` treat a `final_video.mp4` that fails
`probe_duration()` as NOT done (a non-faststart mp4 writes its `moov` atom last, so size and
mtime prove nothing). **Still yours:** before reporting a video complete, confirm its duration
≈ the sum of the decoded audio durations — the guards prove the file is playable, not that it
holds the right footage.

### Stage 2 return

Frames authored (by class); any frame needing >1 attempt and its bug; unresolved frames;
whether `final_video.mp4` exists and its duration; the audit result; and the
**`source errors corrected`** list — every source-originated error as
`{what, where in content, wrong→right}`, plus any placeholder concretized. The orchestrator
propagates those to the source files.
