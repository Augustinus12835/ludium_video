# Ludium Video

An AI-powered pipeline that produces educational videos — animated Manim
visuals, natural narration, word-accurate subtitles — driven end-to-end from
[Claude Code](https://claude.com/claude-code).

You provide the **source material** that grounds a topic: a reference text
chapter (PDF, Markdown, AsciiDoc), a slide deck (PPTX), or a recorded talk
(YouTube URL or raw video/audio). The pipeline transcribes and cleans the
material, reorganizes it into self-contained concept videos, writes a
pedagogically structured script grounded in the source, checks every
calculation with SymPy, and renders each frame as a Manim animation
synchronized to the ElevenLabs narration word-by-word.

```
Source → Transcribe → Clean → Segment → per video: Script → Verify math → TTS → Animate → Compile → Subtitles
```

## Example videos

Videos produced with this pipeline are published on the
[Ludium YouTube channel](https://www.youtube.com/@LudiumAI), with companion
interactive courses on [ludium.ai](https://ludium.ai).

## How it works

There are two kinds of steps, and they run on different engines:

- **LLM steps** (clean, segment, script writing, math verification, Manim frame
  authoring) run as **Claude Code subagents** under your Claude subscription —
  no Anthropic API key, no per-token bill. `scripts/render_step_prompt.py`
  renders the exact prompt for each step; the `/run-pipeline` skill orchestrates
  the subagents that follow those prompts, including a closed
  render → screenshot → fix loop for every animated frame.
- **Deterministic steps** (transcription, TTS, rendering, compiling, subtitles)
  run through `scripts/pipeline.py`, which is file-based and fully resumable —
  every step detects its state from disk, so a failed run continues where it
  stopped.

The only paid external service is **ElevenLabs**, used for both text-to-speech
and Scribe speech-to-text. TTS returns exact word timestamps, which is what lets
animations and subtitles sync to the narration at word precision.

## Requirements

- [Claude Code](https://claude.com/claude-code) with a Claude subscription
  (all LLM steps run as subagents in your session)
- An **ElevenLabs** API key with both **text_to_speech** and **speech_to_text**
  permissions enabled, plus a **voice ID** (any voice, including a clone)
- Python 3.10+, ffmpeg, and a LaTeX distribution (for Manim) — see
  `requirements.txt` for the exact packages

## Setup

```bash
git clone <this-repo> && cd ludium_video
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID
venv/bin/python scripts/setup_pronunciation_dict.py
```

The last step uploads the bundled **pronunciation dictionary**
(`docs/elevenlabs_pronunciation_dict.pls` — `sinh`/`cosh`, Greek-letter words,
and other math notation TTS mangles) to your ElevenLabs account and wires its
ID into `.env`; it is attached to every TTS request from then on. Add entries
to the live dictionary to suit your own production needs (proper nouns, domain
terms, recurring acronyms) — requests always use the latest version. Reference:
`docs/elevenlabs_pronunciation_dict.md`.

## Usage

Open the repo in Claude Code and run the skill:

```
/run-pipeline https://www.youtube.com/watch?v=... --math
/run-pipeline inputs/my_recording.mp4 --technical
/run-pipeline Calculus_1_Lecture_07        # resume an existing pipeline folder
```

The skill supervises the whole run: it transcribes and cleans the source
material, segments it into self-contained concept videos, spawns a scripting
subagent and a production subagent per video, verifies every calculation with
SymPy, authors and renders each Manim frame (with visual QA), and compiles
`pipeline/<source>/Video-N/final_video.mp4` plus `subtitles.srt`.

### Modes

| Mode | For | Frames |
|------|-----|--------|
| `--math` | Pure math sources (auto-detected from folder prefixes like `Calculus_`, `Linear_Algebra_`) | Math frames get step-by-step SymPy-verified build-ups; `visual` frames are free-form explanatory animations |
| `--technical` | Math + diagrams + code (finance, CS, engineering, physics) | Adds `code` frames with traced code walkthroughs |

### Input types

- **YouTube URL** — captions are fetched when available; otherwise the audio is
  downloaded and transcribed with Scribe
- **Raw video/audio file** — transcribed with Scribe
  (`scripts/transcribe_lecture.py`)
- **PDF book chapter** — a subagent transcribes the PDF to Markdown (LaTeX
  preserved), then `scripts/clean_book_chapter.py` scaffolds the cleaning step
- **PPTX slide deck** — `scripts/clean_slides_pptx.py` extracts the deck and
  scaffolds the cleaning step

Details for each path: `.claude/skills/run-pipeline/references/sources.md`.

## Repository layout

```
ludium_video/
├── scripts/                 # Pipeline scripts (pipeline.py is the orchestrator)
│   └── utils/               # TTS rules, narration safety gate, prompt constants
├── templates/               # Manim system prompt + teaching style guide
├── docs/                    # Pronunciation dictionary reference
├── .claude/skills/run-pipeline/   # The supervisor skill Claude Code runs
├── pipeline/                # Output, one folder per source (gitignored)
└── inputs/                  # Your source files (gitignored)
```

## Maintenance workflows

`CLAUDE.md` documents the operating workflows Claude Code uses day to day:
fixing a frame from a screenshot, repairing a mispronounced sentence without
re-rendering (zero-shift sentence splicing), regenerating subtitles, and the
pre-TTS narration safety gate that blocks numerals/symbols TTS would garble.

## License

MIT — see [LICENSE](LICENSE).
