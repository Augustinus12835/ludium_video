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
LLM step (clean, segment, script, verify_math, color_plan, manim codegen); the
`/run-pipeline` skill spawns subagents that follow those prompts.
`scripts/pipeline.py` runs the deterministic steps (transcribe, tts,
animate-render of pre-authored sources, compile, subtitle) and halts with the
exact `render_step_prompt.py` command when it reaches a subagent-authored step.
The only paid external service is ElevenLabs (TTS + Scribe transcription).

## Project Structure

```
ludium_video/
├── pipeline/                  # Output by lecture (e.g. pipeline/Calculus_1_Lecture_01/Video-1/)
├── inputs/                    # Source files you provide
├── scripts/                   # Pipeline scripts
├── templates/                 # Manim system prompt + teaching style guide
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

### Pipeline Modes

Two modes. Math is auto-detected from folder prefixes (`Calculus_`,
`Linear_Algebra_`, `Statistics_`, `Probability_`, `Differential_Equations_`);
otherwise pass a flag explicitly.

- **Math** (`--math`) — pure math. The script declares each frame's
  `frame_class` (`math`/`visual`) at generation time. Math frames get
  `math_steps` verified with SymPy; `visual` frames (intuition, concept maps,
  big-picture structure) skip verification and are authored free-form from
  narration + visual description. Because visual narration skips the
  verify_math TTS rewrite, the math script prompt injects
  `MATH_NARRATION_TTS_RULES` (scripts/utils/tts_rules.py) so narration is
  TTS-safe at script time. Manim animates every frame.
- **Technical** (`--technical`) — math + diagrams + code (finance, CS,
  engineering, physics). Frame classes are `math`/`code`/`visual`: math frames
  get `math_steps` + SymPy, code frames get `code_steps` (traced execution) and
  the Manim code-block layout, visual frames are free-form.

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
2. **Find the true narration source.** TTS does NOT always read `script.json`:
   `generate_tts_elevenlabs.py:get_natural_narration()` prefers
   `natural_narration` from `math_verification.json` when the frame is a
   verified math frame (`verification_status` ∈ `correct`/`corrected`).
   - Math frame with `natural_narration` → fix it in `math_verification.json`.
   - Visual frame (or no entry) → fix `script.json` `frames[N].narration`.
   - When in doubt, fix BOTH so they stay consistent.
3. Apply the spoken-text rule: **ZERO Arabic numerals in spoken narration** —
   spell every number out in English, matching the value the slide displays.
   Same for differentials (`d x` not `dx`), Greek letters, bare acronyms
   (spaced letters), and the lowercase variable `a` (write "A" — lowercase `a`
   reads as the article "uh"). Full rules: `VERIFY_MATH_SYSTEM` in
   `scripts/utils/verify_prompts.py` and `scripts/utils/tts_rules.py`.
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
bare initialisms, code tokens, the lowercase variable `a`, sentences starting
with the name `A`. `scripts/utils/narration_check.py` scans the same source TTS
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
| `generate_scripts.py` | Script prompt templates (math/technical) + helpers |
| `verify_math.py` | SymPy helpers for math verification |
| `generate_tts_elevenlabs.py` | TTS audio + exact word timestamps (pronunciation dictionary support) |
| `fix_tts_sentence.py` | Zero-shift sentence-swap TTS fix (edit source → run → recompile) |
| `generate_math_animation.py` | Render pre-authored `frame_N_manim.py` in parallel; color-link lint |
| `preflight_manim.py` / `lint_manim_t2c.py` | Manim authoring preflight + t2c lint helpers |
| `compile_video.py` | Compile frames + audio into final_video.mp4 |
| `generate_subtitles.py` | SRT subtitles from stored word timestamps (Scribe fallback) |
| `audit_frames.py` | Frame visual-QA: contact sheets + full-res busy-moment stills |
| `utils/narration_check.py` | Pre-TTS gate: detects TTS-unsafe tokens in spoken narration |
| `utils/tts_rules.py` | Canonical TTS spell-out rule blocks injected into script prompts |
| `utils/verify_prompts.py` | verify_math / verify_code / color_plan prompt constants |
| `utils/stt.py` | ElevenLabs Scribe transcription (all sources) |
| `utils/script_parser.py` | script.json/script.md load/save |

## API Keys (.env)

```env
ELEVENLABS_API_KEY=sk_...         # needs text_to_speech AND speech_to_text enabled
ELEVENLABS_VOICE_ID=...           # narrator voice
ELEVENLABS_PRONUNCIATION_DICT_ID= # optional; see docs/elevenlabs_pronunciation_dict.md
```

No other keys. LLM steps run as Claude Code subagents under your subscription.
