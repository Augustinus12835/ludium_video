# Ludium Video

An AI-powered pipeline that produces educational videos — natural narration,
word-accurate subtitles and animated visuals — driven end-to-end from
[Claude Code](https://claude.com/claude-code). It covers two families of subjects:

- **Math and technical** — calculus, linear algebra, statistics, physics, finance,
  computer science: every frame a Manim animation, every calculation checked
  with SymPy.
- **Humanities** — history, philosophy, literature, religion: ~20-minute
  illustrated documentary episodes (**folio**, "a book of plates") built on an
  argued narration, with engraved plates, portraits, maps and word-by-word
  captions — see [Humanities pipeline (folio)](#humanities-pipeline-folio).

You provide the **source material** that grounds a topic: a reference text
chapter or book (PDF, Markdown, AsciiDoc), a slide deck (PPTX), or a recorded
talk or lecture (YouTube URL or raw video/audio) — including your own. The
pipeline transcribes and cleans the material, reorganizes it into
self-contained videos, writes a script grounded in the source, and renders the
visuals synchronized to the ElevenLabs narration word by word. For math and
technical subjects it checks every calculation with SymPy and animates each
frame in Manim; for the humanities it writes an argument map before the
narration and composes each scene in Remotion.

```
Source → Transcribe → Clean → Segment → per video: Script → Verify math → TTS → Animate → Compile → Subtitles
```

## Example videos

Start with **[The Video That Made Itself](https://www.youtube.com/watch?v=x-iBXDS6faQ)** —
a walkthrough of this pipeline, produced end to end by the pipeline: every
animation, the narration, and the subtitles in it came out of the flow described
below, with the repository's own documentation as the source material.

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

The only paid external service the math and technical pipeline needs is
**ElevenLabs**, used for both text-to-speech and Scribe speech-to-text (the
humanities pipeline adds image generation — see below). TTS returns exact word timestamps, which is what lets
animations and subtitles sync to the narration at word precision. Narration is
written TTS-safe (numbers and symbols spelled out in words), so subtitles are
compacted back to written form for display — 1973, 30,000, 6.02 × 10²³, ΔX,
dy/dx, λ₁, ≤ — with timestamps preserved (`scripts/utils/subtitle_compact.py`;
`--no-compact` opts out).

## Getting started

For the math and technical pipeline you need two accounts, nothing else is paid:
a **Claude subscription** (Pro or Max) at [claude.ai](https://claude.ai) and an
**ElevenLabs account** at [elevenlabs.io](https://elevenlabs.io). No GitHub
account needed. (The humanities pipeline needs two more API keys — see
[Humanities pipeline (folio)](#humanities-pipeline-folio).)

**1. Install Claude Code** — open the Terminal app, paste:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

then run `claude` and follow the sign-in prompts.

**2. Get your ElevenLabs credentials** — on
[elevenlabs.io](https://elevenlabs.io): Developers (bottom-left) → **API Keys** →
create a key with BOTH **Text to Speech** and **Speech to Text** permissions;
then open **Voices**, pick a voice, and copy its **Voice ID**.

**3. Let Claude install the project.** Run `claude` and paste:

```
Set up https://github.com/Augustinus12835/ludium_video for me: clone it and
install the system and Python dependencies it needs.
```

Claude clones the code and installs everything for your OS.

**4. Add your credentials** — your keys stay in a local file that only you
edit and that git never uploads. Inside the `ludium_video` folder:

```bash
cp .env.example .env
```

Open `.env` in any text editor and paste your API key after
`ELEVENLABS_API_KEY=` and your voice ID after `ELEVENLABS_VOICE_ID=`. Then set
up the bundled math pronunciation dictionary (one command; add your own terms
later in the ElevenLabs dashboard — the pipeline always uses the latest
version):

```bash
venv/bin/python scripts/setup_pronunciation_dict.py
```

**5. Make your first video** — inside the `ludium_video` folder, run `claude`
and type one of:

```
/run-pipeline https://www.youtube.com/watch?v=... --math        # pure math
/run-pipeline https://www.youtube.com/watch?v=... --technical   # anything else: science, finance, CS, engineering
```

A YouTube URL is just one option — see [Input types](#input-types) for the
others (a recording of your own, a book chapter, a slide deck). Finished
videos land in `pipeline/<source>/Video-N/final_video.mp4` with subtitles
next to them.

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
| `--folio` | Humanities (history, philosophy, literature, religion) | A narration-first documentary: engraved plates, portrait cards, maps and type laid over the narration in Remotion, with burned-in word-synchronous captions |

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

## Humanities pipeline (folio)

`--folio` is a separate pipeline for subjects taught through argument and
evidence rather than derivation. It shares transcription, cleaning, TTS and
subtitles with the math/technical pipeline and replaces the rest:

```
Source → Clean (research dossier) → Segment (~20-min episodes) → per video:
  Argument map → review → Narration → review → TTS → word timeline →
  Director (folio.json) → Image assets → Scene authors (Remotion TSX) → Render → Compile → Subtitles
```

- **Two-stage scripts.** The cleaned source is a *research dossier* with numbered
  paragraphs. A subagent first writes an argument map (thesis, 3–6 movements,
  evidence by paragraph, an explicit cut list), a clean-context reviewer checks it
  against the dossier, and only then is the narration written — at ~75% of the
  dossier's length, gated by `audit_script.py` (no long verbatim runs, sourced
  figures, cadence, movement shape).
- **A book of plates.** A director subagent lays 3–5 scenes a minute over the
  word-timed narration; images are generated as 19th-century steel engravings
  normalised to one sepia duotone (plates, portraits, ink-cutout objects,
  "then-states", grounded maps); scene-author subagents write free-form Remotion
  scenes against a primitive library (`remotion/src/folio.tsx`) and check every
  one with rendered stills. Captions are burned in, word by word.

### Sources

Folio takes the same inputs as the math and technical pipeline. Two examples:

- **A recorded lecture course** — your own lectures, or a published course such as
  [Open Yale Courses](https://oyc.yale.edu). Use the YouTube or recording input
  with `--folio`.
- **A book** — e.g. an [Open Book Publishers](https://www.openbookpublishers.com)
  title. An episode manifest divides the book into ~20-minute episodes;
  `docs/examples/plato_republic_episodes.json` does this for Sean McAleer's
  *Plato's 'Republic': An Introduction* (26 episodes), and
  `scripts/obp_pdf_to_markdown.py` converts a publisher PDF with a text layer
  into one Markdown file per section, no OCR.

### Additional API keys

Folio generates its images through two more services, on top of ElevenLabs:

| Key (`.env`) | Service | Used for |
|---|---|---|
| `OPENAI_API_KEY` | [OpenAI](https://platform.openai.com) Images API (`gpt-image-2.5-sunburst` by default; set `FOLIO_GPT_MODEL=gpt-image-2` if your account lacks it) | engraved plates, portraits, objects and then-states |
| `GOOGLE_CLOUD_API_KEY` | [Gemini API](https://aistudio.google.com/apikey) (Gemini 3 Pro Image with Google Search grounding) | maps, drawn from verified geography |

Budget roughly **US$5–7 of images per 20-minute episode** (≈120–200 images at
≈$0.04; maps ≈$0.13). Setting `FOLIO_DEFAULT_TIER=lite` moves every non-map
image to Gemini flash-lite, so only the Google key is needed.

### Extra setup

Rendering uses [Remotion](https://www.remotion.dev): install Node.js 18+, then

```bash
cd remotion && npm ci
```

Remotion is free for individuals and small teams; larger companies need a
[company licence](https://www.remotion.dev/license). The bundled fonts are
Google Fonts under the SIL Open Font License / Apache 2.0
(`remotion/public/fonts/licenses/`).

### Usage

```
/run-pipeline https://www.youtube.com/watch?v=... --folio          # an open-course lecture
/run-pipeline Republic_E01_Two_Questions_And_A_Walk_To_The_Piraeus --folio  # an episode named in a manifest
```

The playbook the skill follows is
`.claude/skills/run-pipeline/references/phase-b-folio.md`.

## Repository layout

```
ludium_video/
├── scripts/                 # Pipeline scripts (pipeline.py is the orchestrator; folio.py for folio)
│   └── utils/               # TTS rules, narration safety gate, prompt constants
├── templates/               # Manim system prompt, teaching style guide, folio director/scene prompts
├── remotion/                # Folio render harness: src/folio.tsx library, fonts, paper textures
├── docs/                    # Pronunciation dictionary reference; examples/ (episode manifest)
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
