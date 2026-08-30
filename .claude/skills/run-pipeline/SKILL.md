---
name: run-pipeline
description: Run the Ludium Video production pipeline in --no-review mode and supervise it end-to-end. Use when the user says "/run-pipeline", "run-pipeline", or asks to babysit a pipeline run from source material through final video. Every LLM step runs as a subagent (never an LLM API) — a scripting subagent authors what is said and shown, a producer subagent does codegen and QA; the orchestrator auto-recovers from failures.
---

# Ludium Video Pipeline Supervisor

Produce finished videos from one piece of source material end to end: resolve the source,
run the pipeline, fix failures, audit frames. The user invokes this to walk away. The deliverable is
`pipeline/<L>/Video-N/final_video.mp4` + `subtitles.srt` per video.

**The architectural rule: every LLM call is a subagent, never an LLM API** — clean, segment,
script, math verification + color plan, and frame codegen. **Model routing: one scripting
agent owns everything that decides what is SAID and what is SHOWN** — the script step
(narration PLUS each frame's `visual` description) and script fixes coming out of review or
verify_math (spoken text is `script.json` for every frame class — the verify_math
`natural_narration` rewrite was retired 2026-08-30). Use your most capable available
model for that scripting stage. **The producer agent does everything else** — SymPy
verification, color plan, frame codegen, renders, and QA/audit (including applying QA-driven
fixes). One author for spoken + shown means the two tracks can't disagree about what's
factual or which half carries a detail. The scripting agent is spawned BY the orchestrator
and reports back to it (Phase B1) — it is NOT nested inside the producer; the producer
(Phase B2) is spawned only after the scripting artifacts exist on disk.

`render_step_prompt.py` renders the byte-identical prompt each step expects; the subagent
works from that plus the context a blind API call lacks — codegen gets a closed
render→look→fix loop, math verification runs SymPy itself. Never invoke an LLM API for an
LLM step; `pipeline.py` is called only for non-LLM slices (`--from X --to Y`: transcribe,
tts, animate-render, compile, subtitle). The only paid external calls are ElevenLabs (TTS +
Scribe transcription).

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

If the spec is ambiguous, pick the most likely match, state your interpretation, and proceed —
only stop if it's genuinely unparseable.

**Mode routing.** `--math` for pure math (auto-detected from folder prefixes like
`Calculus_`, `Linear_Algebra_`, `Statistics_`, `Probability_`, `Differential_Equations_`);
`--technical` for math + diagrams + code (finance, CS, engineering, physics). One is
required — there is no default mode.

**Voice.** Narration uses `ELEVENLABS_VOICE_ID` from `.env` — pass no `--voice-id`.

## How it runs

**Phase A — source-level prep, orchestrator, once**: transcribe → clean → coverage gate →
segment. **Phase B1 — scripting stage, one scripting subagent per video, reporting to the
orchestrator**: the script (narration + every frame's `visual`). The orchestrator runs the
script-review loop in the middle: it spawns a clean-context reviewer and relays the issue
list to the scripting agent via `SendMessage` — both report to the orchestrator, so the
relay is direct (no nested-agent dead ends). **Phase B2 — production, one producer subagent
per video**: spawned only after B1 returns; it takes the finished script through
verification, codegen, render, compile, subtitle, and frame audit. A fresh subagent per
video keeps each context clean and focused. Run multi-video sources in parallel batches of
~3 (4K renders are CPU-heavy); B1 agents may also run in parallel, and each video's B2
starts as soon as its B1 returns.

Run long `pipeline.py` slices in the background, tee output to `/tmp/run_pipeline_logs/`,
and watch with `Monitor`.

### Phase A

Skip any step whose output already exists (same resume detection as `pipeline.py`). Book
and PPTX sources arrive with `content_cleaned.txt` — start at segment.

1. **transcribe** —
   ```bash
   venv/bin/python scripts/pipeline.py run "<URL-or-folder>" --from transcribe --to transcribe --no-review [mode]
   ```
   With a YouTube URL whose title makes a poor folder name, add `--folder <Name>`. If the
   lecturer's official notes/handout exist, save them as Markdown at
   `pipeline/<L>/source_lecture_notes.md` NOW — `render_step_prompt.py clean|script` inject
   them automatically as ground truth for every equation and worked example (an ASR
   transcript never sees the board).
2. **clean** (subagents, one per chunk, in parallel) — render each chunk's exact prompt:
   ```bash
   # chunk count:
   venv/bin/python -c "import json,pathlib; from scripts.clean_transcript import extract_full_text; from scripts.render_step_prompt import chunk_text; t=json.loads(pathlib.Path('pipeline/<L>/transcript.json').read_text()); x=extract_full_text(t); print(len(chunk_text(x,25000)) if len(x)>=30000 else 1)"
   # per chunk i:
   venv/bin/python scripts/render_step_prompt.py clean --transcript pipeline/<L>/transcript.json --chunk-index <i>
   ```
   Each subagent returns only its cleaned text; join with `\n\n` → `content_cleaned.txt`.
3. **Coverage gate (required before segment).** Cleaning compresses but must never lose
   coverage — a clean subagent can silently drop the tail. Verify: (a) the last ~400 words
   of source and of `content_cleaned.txt` reach the same closing material; (b) `wc -w`
   both — cleaned text lands at ~55–70% of source; below ~45% or a chunk-sized hole means a
   dropped span, not aggressive editing; (c) every chunk `0..N-1` made it into the join.
   Re-clean any missing span before proceeding. Applies to every source type.
4. **segment** — very short content (a single self-contained chapter or question) can skip
   straight to `segment_concepts.py pipeline/<L> --single-video`. Otherwise:
   `render_step_prompt.py segment --content pipeline/<L>/content_cleaned.txt` → one
   subagent returns anchor-based JSON → save it →
   `segment_concepts.py pipeline/<L> --apply <response>`. On "Anchor split failed",
   re-spawn the subagent with the error appended.

### Phase B (per video) — B1 scripting → review relay → B2 production

The playbook is split into **Stage 1 (scripting)** and **Stage 2 (production)**; pass the
video path, the mode, and the playbook to both:
[references/phase-b.md](references/phase-b.md).

B1 is a **conversation with one scripting agent**, not a fire-and-forget spawn:

1. Spawn the Stage-1 scripting agent (most capable model). It authors `script.json`
   (narration + every frame's `visual`), regenerates `script.md`, and reports back to you.
2. Spawn the clean-context reviewer (the playbook's review step) and relay its issue list
   to the scripting agent via `SendMessage` — the scripting agent owns every script-content
   fix (narration AND visual); you may apply purely mechanical fixes (frame numbering,
   `metadata.frame_count`) yourself. Bounded to 2 rounds.
3. On pass: B1 is done. Spawn the Stage-2 producer to finish the video (verification,
   codegen, render, compile, subtitle, audit). Track which `agentId` owns which video at
   spawn time.

Both stages re-check disk state and skip completed steps (a resumed video may start straight
at B2). The producer's return includes a **`source errors corrected`** list — errors that
originate in the source content, not in the script; the scripting agent reports any it finds
at script time the same way.

**Propagate source corrections (orchestrator, serially, after each video returns).** For
each reported source error: re-verify the corrected value yourself, then edit BOTH
`pipeline/<L>/content_cleaned.txt` and `pipeline/<L>/Video-N/content.txt`, and grep to
confirm no stale copy remains. Do this in the orchestrator, not the parallel subagents —
`content_cleaned.txt` is shared.

## Recovery

The pipeline is fully resumable from disk state: read the log tail, identify the failure
class, apply a targeted fix, resume — never restart from scratch. At most 3 recovery
attempts per failing step; after that surface to the user with the error tail, what you
tried, and your next hypothesis. Playbooks for the known failure classes (Manim render
errors, the pre-TTS narration gate, API overloads, compile/subtitle issues):
[references/recovery.md](references/recovery.md).

## Frame audit — per video, as each one finishes

Frames are generated without vision, so once a video's `final_video.mp4` exists, run the
frame audit: `audit_frames.py` contact sheets + full-res busy-moment stills, fix
high-confidence defects in the frame source, re-render, recompile. Keep it bounded — the
user still does a final pass.

## Wrap-up

- Status table per video: `final_video.mp4` (size, duration); note frames that needed
  manual recovery and any source errors corrected.

## Boundaries

Writes stay under `pipeline/<L>/` and `/tmp/run_pipeline_logs/`. Never write `inputs/`
(except the one-off book-chapter conversion in sources.md) or `.env`.
