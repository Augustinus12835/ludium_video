# Ludium Video — AI Educational Video Production Pipeline

## Overview

Produces educational videos from source/reference material (YouTube URL, raw
video/audio, PDF book chapter, PPTX deck) — animated Manim visuals, ElevenLabs
narration, word-accurate subtitles. Content is transcribed, cleaned, and
reorganized into self-contained concept videos; the script is written from the
cleaned source and verified against it.

**Pipeline flow:**
```
Source → Transcription → Cleaning → Segmentation → Per-video processing
                                                         ↓
                                Script → Verify math → TTS → Animate → Compile → Subtitles
```

**The architectural rule: every LLM step is a Claude Code subagent, never an
API call.** `scripts/render_step_prompt.py` renders the exact prompt for each
LLM step (clean, segment, script, verify_math, color_scheme, color_plan, manim codegen); the
`/run-pipeline` skill spawns subagents that follow those prompts.
`scripts/pipeline.py` runs the deterministic steps (transcribe, tts,
animate-render of pre-authored sources, compile, subtitle) and halts with the
exact `render_step_prompt.py` command when it reaches a subagent-authored step.
The only paid external service the math/technical pipeline uses is ElevenLabs (TTS + Scribe
transcription); the folio (humanities) pipeline adds image generation (see "Folio").

## Project Structure

```
ludium_video/
├── pipeline/                  # Output by lecture (e.g. pipeline/Calculus_1_Lecture_01/Video-1/)
├── inputs/                    # Source files you provide
├── scripts/                   # Pipeline scripts
├── templates/                 # Manim system prompt, teaching style guide, folio director/scene prompts
├── remotion/                  # Folio render harness (src/folio.tsx, fonts, paper textures)
├── docs/                      # ElevenLabs pronunciation dictionary reference
└── .env                       # ElevenLabs credentials (NEVER commit)
```

## Common Workflows

### Running the Full Pipeline

**Production runs go through `/run-pipeline`** — it authors the LLM steps with
subagents and calls `pipeline.py` for the rest.

```bash
# Resume / run the non-LLM steps of a source (halts with guidance at LLM steps)
python scripts/pipeline.py run LECTURE_NAME --math --no-review

# Run a specific video only
python scripts/pipeline.py video LECTURE_NAME/Video-3 --technical --no-review

# Check status
python scripts/pipeline.py status LECTURE_NAME
```

With a YouTube URL whose title makes a poor folder name, pass `--folder <Name>`
to name the pipeline folder explicitly. **Grounding a recorded lecture:** if you
have the lecturer's official notes/handout, save them as Markdown at
`pipeline/<L>/source_lecture_notes.md` before the clean step — `render_step_prompt.py
clean|script` inject them automatically as ground truth for every equation and
worked example (an ASR transcript never sees the board).

**Dated lectures — refresh stale figures at clean time (`--refresh-figures`).** Opt-in,
and meant for finance/economics/business courses whose *evidence* is market data (market
sizes, yields, rankings, "over the past N years"); a math/physics/CS course whose theory has
moved on is caught by the script/review agents, not by this. For a lecture old enough that
its real-world numbers no longer describe the world (a 2008 finance lecture recorded during
the crisis), render the clean prompt with
`render_step_prompt.py clean --refresh-figures --recorded "Fall 2008"`. It adds a review
that sorts every number into three buckets and treats each differently: **worked-example
inputs are NEVER touched** (altering one breaks every figure downstream, the on-screen math
and verification); **period-anchored facts** (a crisis unfolding as the lecturer speaks) keep
their value but gain an explicit date so they cannot read as current; **currency-claiming
figures** (market sizes, "over the past N years", "rates are low right now", rankings,
current yields) are WEB-SEARCHED against a primary source — SIFMA, Fed/FRED, Treasury, BLS,
an exchange, a filing — never recalled, because a model's sense of a current value is staler
than the research. No solid source ⇒ keep the original and date it; an honestly dated old
number beats a confident wrong new one. Refreshes are SILENT in narration (never "the
lecturer said X, but today Y") and never silently reverse the argument — where a claim has
genuinely reversed, keep the dated original and add the change in one clause. Each change is
logged to `<L>/figure_updates.json` (original/refreshed/as_of/source/source_url/note; a cut
claim is `"refreshed": "(removed)"`), which `render_step_prompt.py script` then injects
automatically on file presence — no flag — so the scripting agent narrates the refreshed
value instead of re-staling it from memory. Multi-chunk lectures emit one JSON block per
chunk — merge the lists into ONE `figure_updates.json`. **Gate before segment:**
`scripts/audit_figure_updates.py pipeline/<L>` traces every entry into `content_cleaned.txt`
(tiers `verbatim` / `normalized` / `figures` = numbers present but the log entry is a
paraphrase / `MISSING`) and BLOCKs on a figure the prose does not carry or an entry with no
`source_url` — either is a recalled figure, not a researched one. (First course run: 123
entries over 23 units; one BLOCK was a refresh the agent researched, logged, and then never
wrote into the prose.)

### Pipeline Modes

Three modes. Math is auto-detected from folder prefixes (`Calculus_`,
`Single_Variable_Calculus_`, `Multivariable_Calculus_`, `Linear_Algebra_`,
`Statistics_`, `Probability_`, `Differential_Equations_`); otherwise pass a
flag explicitly.

- **Math** (`--math`) — pure math. The script declares each frame's
  `frame_class` (`math`/`visual`) at generation time. Math frames get
  `math_steps` verified with SymPy; `visual` frames (intuition, concept maps,
  big-picture structure) skip verification and are authored free-form from
  narration + visual description. Every frame's narration is spoken VERBATIM
  from `script.json` — verify_math checks the math and extracts on-screen
  steps only (its `natural_narration` TTS rewrite was retired 2026-08-30) —
  so the math script prompt injects `MATH_NARRATION_TTS_RULES`
  (scripts/utils/tts_rules.py) to make narration TTS-safe at script time.
  Manim animates every frame. The script's `visual` is a shot description —
  what, roughly where, and on which narration phrase (~400–900 chars) — never
  coordinates, measured widths, scales or Manim API: codegen measures and owns
  those.
- **Technical** (`--technical`) — math + diagrams + code (finance, CS,
  engineering, physics). Frame classes are `math`/`code`/`visual`: math frames
  get `math_steps` + SymPy, code frames get `code_steps` (traced execution) and
  the Manim code-block layout, visual frames are free-form.
- **Folio** (`--folio`) — humanities (history, philosophy, literature, religion). See below.

### Folio (humanities)

Narration-first documentary with a Remotion "book of plates" visual track. Playbook:
`.claude/skills/run-pipeline/references/phase-b-folio.md`.

- **Sources:** any input the other modes take — e.g. a recorded lecture course (your own, or
  Open Yale Courses → transcribe → clean) or a book divided into ~20-minute episodes by a manifest (worked example
  `docs/examples/plato_republic_episodes.json`: `scripts/obp_pdf_to_markdown.py` turns a
  publisher PDF with a text layer into per-section Markdown; `clean_book_chapter.py --manifest`
  with the `commentary` profile composes and cleans one episode). Details:
  run-pipeline `references/sources.md` "Humanities sources".
- **Segmentation:** a few LONG ~20-min videos, ~4,000 cleaned words each (3,000–5,000; the
  script narrates ~75% of its dossier at 150 words/min). `pipeline.py run <L> --folio` sizes it
  (`segment_concepts.target_video_count`); one video → `segment_concepts.py <L> --single-video
  --folio`, more → `render_step_prompt.py segment --folio`.
- **Two-stage scripts:** `content.txt` is a research DOSSIER with `[P1]…[Pn]` IDs.
  `render_step_prompt.py argument --mode folio` → `argument.json` (thesis, 3–6 movements,
  evidence by P-id, cut list), reviewed against the dossier (round 0); then
  `render_step_prompt.py script --mode folio` injects the approved map and asks for ~75% of the
  dossier's length, each frame tagged `movement` + `sources`. `audit_script.py` BLOCKs a
  ≥10-word verbatim run outside quotes and CHECKs overlap, length ratio, unsourced figures,
  filler, cadence and movement shape; `--argument` checks the map alone.
- **Visual track (after TTS):** `build_narration_timeline.py` → a director subagent writes
  `folio.json` (world, cast, 3–5 scenes/min on verbatim anchors, 6–10 generated assets/min,
  an `off_screen` list) → `folio.py place` / `audit` → `folio.py assets` (OpenAI gpt-image;
  maps on grounded Gemini Pro; every image normalised to one sepia duotone, objects become ink
  cutouts) → scene-author subagents write free-form `frames/scene_NN.tsx` against
  `remotion/src/folio.tsx` after LOOKING at the assets, iterating on `folio.py still --auto` →
  `folio.py lint` / `render` / `sheet` / `compile` → `generate_subtitles.py`. Captions are
  burned in, word by word, in a reserved band (y 872–1006).
- `pipeline.py … --folio` runs transcribe/clean/segment and TTS; its animate/compile steps
  halt with a pointer to the playbook. Setup: Node.js 18+, `cd remotion && npm ci`.

### Recompiling Video

```bash
python scripts/compile_video.py pipeline/LECTURE/Video-N
```

**`final_video.mp4` existing is not evidence the compile finished.** A non-faststart mp4
gets its `moov` atom written LAST, so a file still being written — or one whose ffmpeg
died partway — sits on disk at a plausible size and fails to probe (`moov atom not
found`). Mtime checks cannot catch it either, because mtime updates on every write.
`detect_video_state()` requires `probe_duration()` to succeed and `verify_compilation()`
reports `probe_ok`, but before reporting a video done, still confirm:

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
    pipeline/LECTURE/Video-N/final_video.mp4     # must print a duration
```
and that the duration ≈ the sum of the decoded audio durations.

**Patching a frame source after an animate pass re-renders it automatically.**
`prepare_frame_code()` in `generate_math_animation.py` compares the mp4's mtime against
`frame_N_manim.py` instead of merely checking that the mp4 exists, so `--from animate`
never keeps a stale render and prints VIDEO COMPLETE over it; you need not `rm` the mp4
first. The comparison deliberately ignores the mp3 — `fix_tts_sentence.py` rewrites audio
in place at the identical span and must not trigger a re-render.

### Fixing Frames From Screenshots

When the user reports a visual issue (overlapping text, boxes outside frame,
label collisions) with a screenshot:

1. Read the **title** and **timestamp** off the screenshot; they identify the
   pipeline folder/video and the frame.
2. Map the timestamp to a frame number using cumulative audio durations
   (**numeric frame order 0,1,2,…,10 — not `ls` order 0,10,1,2…**):
   ```bash
   cd pipeline/<L>/Video-<N> && total=0
   for f in $(ls audio/frame_*.mp3 | sort -t_ -k2 -n); do
     d=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f")
     end=$(echo "$total + $d" | bc -l)
     echo "$(basename $f): $total → $end"
     total=$end
   done
   ```
3. Read `frames/frame_<F>_manim.py` and diagnose the layout bug. Common
   culprits: `next_to(point, …)` (places the mobject edge at the point, often
   overlapping a sibling), insufficient `buff=`, labels placed without checking
   the target's width, missing `scale_to_fit_width` on overflowable content.
   The catalogue of defects that render SUCCESS and are still wrong is
   `templates/manim_system_prompt.md` rules 36–80 — check it before guessing.
4. Edit the Manim file with a targeted fix.
5. Re-render the single frame:
   ```bash
   venv/bin/python -c "
   from scripts.generate_math_animation import render_manim_scene
   from pathlib import Path
   p = Path('pipeline/<L>/Video-<N>/frames/frame_<F>_manim.py')
   ok, msg = render_manim_scene(p.read_text(), str(p.parent / 'frame_<F>.mp4'), <duration>)
   print(('OK ' if ok else 'FAIL: ') + msg[-1500:])
   "
   ```
6. **Visual confirmation is mandatory** — extract a still at the offending
   moment and Read it:
   ```bash
   ffmpeg -y -ss <seconds_into_frame> -i pipeline/<L>/Video-<N>/frames/frame_<F>.mp4 \
       -frames:v 1 -q:v 2 /tmp/fix_check.png
   ```
7. Recompile: `venv/bin/python scripts/compile_video.py pipeline/<L>/Video-<N>`

If the user sends multiple screenshots for the same video, fix all of them
before recompiling.

### Fixing TTS / Narration (pronunciation, garbled numbers)

Spoken problems live in the **narration text**, not the Manim `.py`. On-screen
numerals are correct and stay as digits; only the spoken text changes.

1. Identify the frame (workflow above).
2. **Find the true narration source.** `script.json` `frames[N].narration` for
   every frame class (since 2026-08-30). LEGACY videos only:
   `generate_tts_elevenlabs.py:get_natural_narration()` still prefers a
   verified `natural_narration` from `math_verification.json` when that field
   exists — the audio was voiced from it, so fix it there (both when in doubt).
3. Apply the spoken-text rule: **ZERO Arabic numerals in spoken narration** —
   spell every number out in English, matching the value the slide displays.
   Same for differentials (`d x` not `dx`), Greek letters, bare acronyms
   (spaced letters), and the lowercase variable `a` (write "A" — lowercase `a`
   reads as the article "uh"). Full rules: `scripts/utils/tts_rules.py`.
4. **Fix the audio with the sentence-swap tool (STANDARD workflow — zero
   shift, no Manim re-render).** `scripts/fix_tts_sentence.py` diffs the edited
   source against the audio's stored text, regenerates only the changed
   sentence(s), and splices each back time-stretched (pitch-preserving) to the
   exact original span — total duration and every other word's timing are
   unchanged.
   ```bash
   venv/bin/python scripts/fix_tts_sentence.py pipeline/<L>/Video-<N> --frame <F>
   venv/bin/python scripts/compile_video.py pipeline/<L>/Video-<N>
   venv/bin/python scripts/generate_subtitles.py pipeline/<L> --video <N> --force
   ```
   **Pacing policy: compressing (factor < 1.0) sounds fine; stretching
   (factor > 1.0) sounds bad.** When the new wording is shorter than the
   original span, reword it LONGER so atempo compresses rather than stretches —
   aim for a factor in ~0.8–1.0. Never accept a > 1.0 stretch or a held pause.
5. **Fallback — full regen + re-time** (only when the fix changes the sentence
   count or rewrites a sentence so heavily the swap's stretch would be
   extreme): back up the timestamp JSON, delete the frame's `.mp3`, re-run
   `generate_tts_elevenlabs.py`, then re-time/re-render the Manim frame at the
   new duration and recompile.

**Automated pre-TTS narration gate.** The tts step halts before any audio is
generated if TTS-unfriendly tokens survive in the spoken narration — raw
numerals, Greek letters, math symbols, hex strings, unspaced differentials,
unhyphenated Greek-letter compounds (`delta X` must be `delta-X`, or the voice
drops dead air between the tokens), bare initialisms, code tokens, the
lowercase variable `a`, sentences starting with the name `A`. `scripts/utils/narration_check.py` scans the same source TTS
reads and reports offenders by category; it detects only, never rewrites. Fix
each token in the named source and resume `--from tts`; bypass a confirmed
false positive with `SKIP_NARRATION_CHECK=1`.

### Generating Subtitles

Auto-runs in the pipeline. Built from per-frame ElevenLabs word timestamps
stacked by decoded audio durations, with a 300 ms display lead; expect the
printed drift line under ~120 ms. Scribe re-transcription runs only as a
fallback for videos without timestamp files.

```bash
python scripts/generate_subtitles.py pipeline/LECTURE --video 3
```

**Drift guard (`SUBTITLE_DRIFT_TOLERANCE`, default 0.5 s).** Long videos
accumulate ~19 ms/frame of 30 fps quantization; a 30-frame video can trip the
guard with a perfectly good word timeline. Prefer raising the tolerance
(`SUBTITLE_DRIFT_TOLERANCE=1.0`) over paying for a re-transcription. Never run
`compile_video.py` while `generate_subtitles.py` is uploading the video for
fallback transcription — compile and subtitle must run serially.

## Script Reference

| Script | Purpose |
|--------|---------|
| `pipeline.py` | Main orchestrator (`run`, `video`, `status`); deterministic steps + resume |
| `render_step_prompt.py` | Render any LLM step's exact prompt for a subagent |
| `transcribe_lecture.py` | Transcribe a local video/audio file (ElevenLabs Scribe; optional local Whisper) |
| `clean_transcript.py` | Prompt constants for the transcript-clean subagent |
| `clean_book_chapter.py` | Scaffold a book-chapter clean (.adoc/.md/.txt → clean_prompt.txt for a subagent) |
| `clean_slides_pptx.py` | Scaffold a PPTX-deck clean (extract + clean_prompt.txt for a subagent) |
| `segment_concepts.py` | Materialize segmentation: `--apply RESPONSE.json` or `--single-video` |
| `generate_scripts.py` | Script prompt templates (math/technical/folio) + argument-map helpers |
| `verify_math.py` | SymPy helpers for math verification |
| `setup_pronunciation_dict.py` | One-time: upload the bundled pronunciation dictionary, wire its ID into .env |
| `generate_tts_elevenlabs.py` | TTS audio + exact word timestamps (applies the pronunciation dictionary) |
| `fix_tts_sentence.py` | Zero-shift sentence-swap TTS fix (edit source → run → recompile) |
| `generate_math_animation.py` | Render pre-authored `frame_N_manim.py` in parallel; color-link lint |
| `preflight_manim.py` / `lint_manim_t2c.py` | Manim authoring preflight + t2c lint helpers |
| `compile_video.py` | Compile frames + audio into final_video.mp4 |
| `build_narration_timeline.py` | Folio: stack per-frame word timestamps into one narration timeline |
| `folio.py` | Folio: place · audit · assets · refinish · grid · contact · prompt · still · lint · render · sheet · compile · status |
| `obp_pdf_to_markdown.py` | Open-textbook PDF (text layer) → per-section markdown + footnotes + `sections.json` |
| `generate_subtitles.py` | SRT subtitles from stored word timestamps (Scribe fallback) |
| `audit_script.py` | Mechanical script QA before review: cues, margins, gaps, scope gate, TTS; folio dossier checks; `--argument` (`--self-test`) |
| `audit_figure_updates.py` | Dated-lecture gate: trace every `figure_updates.json` refresh into `content_cleaned.txt`, require `source_url` (`--self-test`) |
| `audit_frames.py` | Frame visual-QA: contact sheets + full-res busy-moment stills |
| `utils/narration_check.py` | Pre-TTS gate: detects TTS-unsafe tokens in spoken narration |
| `utils/manim_probe.py` | Shared Manim probes: scroll sim, width, Tex compile, glyph parity (`--self-test`) |
| `utils/tts_rules.py` | Canonical TTS spell-out rule blocks injected into script prompts |
| `utils/verify_prompts.py` | verify_math / verify_code / color_plan prompt constants |
| `utils/stt.py` | ElevenLabs Scribe transcription (all sources) |
| `utils/script_parser.py` | script.json/script.md load/save |
| `utils/ffmpeg_compile.py` | Segment-wise ffmpeg encode + concat demuxer (bounded memory; used by `folio.py compile`) |

## API Keys (.env)

```env
ELEVENLABS_API_KEY=sk_...         # needs text_to_speech AND speech_to_text enabled
ELEVENLABS_VOICE_ID=...           # narrator voice
ELEVENLABS_PRONUNCIATION_DICT_ID= # set by scripts/setup_pronunciation_dict.py (bundled
                                  # math dictionary; extend it for your own content)
```

No other keys for math/technical. LLM steps run as Claude Code subagents under your subscription.

Folio (humanities) only — image generation, roughly US$5–7 per 20-minute episode:

```env
OPENAI_API_KEY=...                # gpt-image plates, portraits, objects (FOLIO_GPT_MODEL, default gpt-image-2.5-sunburst; gpt-image-2 as fallback)
GOOGLE_CLOUD_API_KEY=...          # Gemini: grounded maps (and every image with FOLIO_DEFAULT_TIER=lite)
```
