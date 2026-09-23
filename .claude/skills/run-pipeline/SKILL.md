---
name: run-pipeline
description: Run the Ludium Video production pipeline in --no-review mode and supervise it end-to-end. Use when the user says "/run-pipeline", "run-pipeline", or asks to babysit a pipeline run from source material through final video. Every LLM step runs as a subagent (never an LLM API) — a scripting subagent authors what is said and shown, a producer subagent does codegen and QA; the orchestrator auto-recovers from failures.
---

# Ludium Video Pipeline Supervisor

Produce finished videos from one piece of source material end to end: resolve the source,
run the pipeline, fix failures, audit frames. The user invokes this to walk away, so the run
is unattended — see "Running unattended" below. The deliverable is
`pipeline/<L>/Video-N/final_video.mp4` + `subtitles.srt` per video.

**Every LLM step is a subagent, never an LLM API** — clean, segment, script, math
verification, colour scheme/plan, frame codegen. `render_step_prompt.py` renders the exact
prompt each step needs; the subagent works from that plus what a blind API call lacks
(codegen gets a render→look→fix loop, math verification runs SymPy itself). `pipeline.py` is
called only for deterministic slices (`--from X --to Y`: transcribe, tts, animate-render,
compile, subtitle). The only paid external calls are ElevenLabs (TTS + Scribe). Use your most
capable available model for every subagent, and pass it explicitly rather than relying on
inheritance.

**One agent owns the whole scripting stage per video** — everything that decides what is
said and what is shown: `script.json` (narration + each frame's `visual`) and script fixes
coming out of review or verification. A single author keeps the spoken and shown tracks from
disagreeing, and a single writer keeps concurrent edits from clobbering `script.json`. The
producer does everything else: verification, colour plan, codegen, renders, QA (including
QA-driven fixes).

## Invocation

```
/run-pipeline <source-spec> [--math | --technical]
```

`<source-spec>` is one of:

- **A YouTube URL** → `pipeline.py` fetches captions, or downloads audio and transcribes
  with Scribe.
- **A local video/audio file** → transcribed with Scribe
  (see [references/sources.md](references/sources.md)).
- **A PDF book chapter, Markdown/AsciiDoc chapter, or PPTX deck** →
  see [references/sources.md](references/sources.md); these arrive with
  `content_cleaned.txt`, so Phase A starts at segment.
- **An existing pipeline dir or `Video-N` path** → resume in place (`pipeline.py`
  auto-detects state; single video runs via `pipeline.py video`).

If the spec is ambiguous, pick the most likely match, state your interpretation, and proceed;
stop only if it's unparseable.

**Mode routing.** `--math` for pure math (auto-detected from folder prefixes like
`Calculus_`, `Linear_Algebra_`, `Statistics_`, `Probability_`, `Differential_Equations_`);
`--technical` for math + diagrams + code (finance, CS, engineering, physics). One is
required — there is no default mode.

**Voice.** Narration uses `ELEVENLABS_VOICE_ID` from `.env` — pass no `--voice-id`.

## How it runs

- **Phase A — source-level prep, orchestrator, once:** transcribe → clean → coverage gate →
  segment → colour scheme.
- **Phase B1 — scripting, one agent per video, reporting to the orchestrator:** the script;
  the orchestrator runs the review relay in the middle.
- **Phase B2 — production, one producer per video:** spawned only once B1's artifacts are on
  disk; takes the video through verification, codegen, render, compile, subtitle and frame
  audit.

A fresh agent per video keeps each context focused. Run multi-video sources in parallel
batches of ~3 (4K renders are CPU-heavy); B1 agents may also run in parallel, and each video's
B2 starts as soon as its B1 returns. Run long `pipeline.py` slices in the background, tee to
`/tmp/run_pipeline_logs/`, and watch with `Monitor`.

### Phase A

Skip any step whose output already exists. Book and PPTX sources arrive with
`content_cleaned.txt` — start at segment.

1. **transcribe**
   ```bash
   venv/bin/python scripts/pipeline.py run "<URL-or-folder>" --from transcribe --to transcribe --no-review [mode]
   ```
   With a YouTube URL whose title makes a poor folder name, add `--folder <Name>`. If the
   lecturer's official notes or handout exist, save them as Markdown at
   `pipeline/<L>/source_lecture_notes.md` now — `render_step_prompt.py clean|script` inject
   them as ground truth for every equation and worked example (the transcript never sees the
   board).
2. **clean** — one subagent per chunk, in parallel:
   ```bash
   # chunk count:
   venv/bin/python -c "import json,pathlib; from scripts.clean_transcript import extract_full_text; from scripts.render_step_prompt import chunk_text; t=json.loads(pathlib.Path('pipeline/<L>/transcript.json').read_text()); x=extract_full_text(t); print(len(chunk_text(x,25000)) if len(x)>=30000 else 1)"
   # per chunk i:
   venv/bin/python scripts/render_step_prompt.py clean --transcript pipeline/<L>/transcript.json --chunk-index <i>
   ```
   Each subagent returns only its cleaned text; join with `\n\n` → `content_cleaned.txt`.
   Give each chunk agent a chunk-namespaced file for its rendered prompt (`c<i>_prompt.json`):
   they share the lecture dir, and a generic name gets overwritten by a sibling, which
   silently cleans the wrong span.

   **Dated course?** (finance/economics whose evidence is market data) Append
   `--refresh-figures --recorded "<when>"` to every chunk render (policy: CLAUDE.md "Dated
   lectures"). Strip each chunk's JSON block from the prose, merge the lists into one
   `pipeline/<L>/figure_updates.json`, and gate with `scripts/audit_figure_updates.py
   pipeline/<L>` before segment — resolve every BLOCK (drop the entry or re-clean) rather than
   leaving it for the script stage. WebSearch is limited to ~200 calls per session, so
   front-load the lectures richest in live figures.
3. **Coverage gate** (every source type, before segment). A clean agent can silently drop a
   span or the tail. Check, in this order of weight:
   (a) the last ~400 words of source and cleaned text reach the same closing point;
   (b) every chunk `0..N-1` is in the join;
   (c) every chunk seam joins grammatically — each chunk agent may assume the other wrote the
   bridging sentence. Sweep for paragraphs that open lowercase or mid-clause and read each hit
   (most are legitimate prose continuing around a code block):
   ```python
   for i, para in enumerate(text.split("\n\n")):        # skip fenced blocks and md markers
       w = para.strip().split()[:1]
       if w and w[0][0].islower() and w[0] not in ("a", "an", "the"): print(i, para[:90])
   ```
   (d) `wc -w` ratio: lectures usually clean to ~55–70% of source. It is advisory — an
   administrative intro lecture can land far lower and still be complete — but below ~45%, or
   a chunk-sized hole, means a dropped span. Re-clean any missing span.
4. **segment** — very short content (a single self-contained chapter or question) can skip
   straight to `segment_concepts.py pipeline/<L> --single-video`. Otherwise:
   `render_step_prompt.py segment --content pipeline/<L>/content_cleaned.txt` → one subagent
   returns anchor-based JSON → save → `segment_concepts.py pipeline/<L> --apply <response>`.
   On "Anchor split failed", re-spawn with the error appended.
5. **Colour scheme (before any script).** Colour is decided once per lecture so a quantity
   can't change colour between videos:
   ```bash
   venv/bin/python scripts/render_step_prompt.py color_scheme --pipeline-dir pipeline/<L>
   #   → one subagent returns {name: {color, tex, note_words, scope}} → pipeline/<L>/color_scheme.json
   ```
   From then on the `script` render injects it as background (so `visual` text names
   quantities, not colours) and each video's `color_plan` render injects it as binding. Fix any
   stderr warning it prints on load (non-Manim constant, reserved WHITE/YELLOW, duplicate or
   missing colour) before scripting. Check that every quantity appearing in ≥3 videos made it
   into the scheme, even if that spends GREEN/RED_C or exceeds seven entries; a recurring
   quantity left out gets a different colour from each producer. If one must stay out, name it
   and its colour in the scheme's `scope` text.

### Phase B (per video) — B1 scripting → review relay → B2 production

The playbook is split into Stage 1 (scripting) and Stage 2 (production):
[references/phase-b.md](references/phase-b.md).

**Brief by reference.** Each agent's prompt names the video, mode and playbook stage to read,
carries only the facts specific to this video (audit numbers, fixes already applied, the scope
boundary against siblings, prior-lecture sources to check against), includes the standing
instruction below, and stops. Don't paraphrase the playbook into the brief — the agent can read
it, and a paraphrase drifts from it.

B1 is a conversation with one scripting agent:

1. Spawn the Stage-1 scripting agent. It writes `script.json`, regenerates `script.md`, and
   reports back.
2. Run `scripts/audit_script.py pipeline/<L>/Video-N --siblings` yourself (seconds; tiered
   BLOCK/CHECK — cue uniqueness, margins, gaps, scope gate, TTS, metadata, sibling overlap).
   Then spawn one clean-context reviewer with that output and tell it not to redo those checks;
   its effort goes to the maths, fit simulation, source fidelity, pedagogy and prose. Relay the
   combined issue list to the scripting agent via `SendMessage`; it applies every content fix.
   You may apply purely mechanical fixes (frame numbering, `metadata.frame_count`) yourself.
   At most 2 rounds.
3. On pass, spawn the Stage-2 producer. Record which agent owns which video.

The reviewer is the one deliberate second opinion in the pipeline. Don't add further
verification agents — every agent here checks its own work.

Both stages re-check disk state and skip completed steps (a resumed video may start at B2).
Each stage's return includes a **`source errors corrected`** list — errors in the source
content, not the script.

**Propagate source corrections (orchestrator, serially, after each video returns).** For each
reported source error, re-check the corrected value, edit both
`pipeline/<L>/content_cleaned.txt` and `pipeline/<L>/Video-N/content.txt`, and grep for stale
copies. Do this in the orchestrator — `content_cleaned.txt` is shared.

## Narration style

Give this rule to every Stage-1 agent and reviewer. **Say what you mean; when a literal
phrase is available, use it.** Mannered prose swaps a direct statement for a metaphor or
flourish — "a dial worth turning" for "a parameter worth varying", "earns its keep" for
"still matters". It matters more here than in print for two reasons:

- Narration is heard once. A listener can't re-read a clause to work out which half was
  literal, so a figure a reader would decode in a beat is a lost sentence.
- In technical material the metaphor's connotations are often false. "The parent hands the
  method down" implies a copy; "Python reaches up the chain" implies a search cost. Say "the
  subclass does not define `__str__`, so Python uses the parent's."

An analogy that does real work (a blueprint versus the houses built from it) is fine; name it
as an analogy. Reviewers flag decoration as a defect with its location and why the literal
version is better, and leave the rewrite to the author — reviewer-supplied prose is unverified
and has introduced falsehoods before.

## Running unattended

The user is away, so nobody answers mid-run questions. This applies to you and to every agent
you spawn.

**Standing instruction — include it in every subagent brief:**

> You are running unattended; nobody will answer a question until the job is done. A message
> with no tool call ends your turn and stops the work. Don't end a turn with a summary that
> announces the next step, an offer to continue, or a list of decisions that don't actually
> block you — make the routine call yourself and do the next thing. Status notes are fine in
> the same message as your next tool call. Stop only when your stage's return is complete, or
> when something genuinely needs the orchestrator (a missing input, a failure you've tried 3
> times, a paid action outside your stage). Destructive or outward-facing actions still need
> the confirmation this playbook specifies.

**When a subagent's turn ends,** treat its text as a report, not proof it finished. Check its
artifacts on disk against its stage's return list. If items are open and no blocker is stated,
`SendMessage` it naming them ("still open: frames 7 and 9 unrendered, subtitle not run —
continue; if blocked, say by what"). After 2–3 such nudges on the same video, take over from
disk state or surface it. If something it started is still running (a render, a background
command), wait for it before judging. Liveness checks are in
[references/recovery.md](references/recovery.md).

**You follow the same rule.** Keep going between videos without pausing to report; the user
reads the wrap-up. Stop early only for the cases in Recovery below.

## Recovery

The pipeline resumes from disk state: read the log tail, identify the failure class, apply a
targeted fix, resume — never restart from scratch. At most 3 recovery attempts per failing
step; then surface to the user with the error tail, what you tried, and your next hypothesis.
Playbooks for the known failure classes (Manim render errors, the pre-TTS narration gate, API
overloads, compile/subtitle issues, stale renders, session limits, agent liveness):
[references/recovery.md](references/recovery.md).

## Frame audit — per video, as each finishes

Frames are generated without vision, so once a video's `final_video.mp4` exists the producer
runs the frame audit (`audit_frames.py` contact sheets + full-res busy-moment stills), fixes
high-confidence defects in the frame source, re-renders and recompiles. Keep it bounded — the
user does a final pass.

## Wrap-up

- Status table per video: `final_video.mp4` (size, duration); frames that needed manual
  recovery; source errors corrected.

## Boundaries

Writes stay under `pipeline/<L>/`, `/tmp/run_pipeline_logs/` and the session scratchpad.
Never write `inputs/` (except the one-off book-chapter conversion in sources.md) or `.env`.
