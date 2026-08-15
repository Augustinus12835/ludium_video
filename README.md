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

## What you need

Two accounts, nothing else is paid:

1. A **Claude subscription** (Pro or Max) at [claude.ai](https://claude.ai) —
   all AI steps run inside Claude Code under your subscription.
2. An **ElevenLabs account** at [elevenlabs.io](https://elevenlabs.io) — used
   for the narration voice and for transcription.

No GitHub account is needed — downloading the code is anonymous.

## Getting started — complete walkthrough

Everything below is typed into the **Terminal** app. Copy and paste each block
in order. The walkthrough assumes Ubuntu/Debian Linux; for macOS or Windows,
see the note at the end of this section.

**Step 1 — install the system tools** (Python, git, ffmpeg, LaTeX for the
animations). One command, takes a few minutes:

```bash
sudo apt update && sudo apt install -y git python3-venv ffmpeg \
    texlive-latex-extra texlive-fonts-extra texlive-science cm-super \
    dvisvgm libpango1.0-dev
```

**Step 2 — install Claude Code** and sign in with your Claude account:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then run `claude` once and follow the sign-in prompts. (Full instructions:
[claude.com/claude-code](https://claude.com/claude-code).)

**Step 3 — download this project** (this copies the code into a `ludium_video`
folder — no account needed):

```bash
git clone https://github.com/Augustinus12835/ludium_video.git
cd ludium_video
```

**Step 4 — install the Python packages** (self-contained inside the project
folder, doesn't touch the rest of your system):

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**Step 5 — connect your ElevenLabs account.** In your browser, on
[elevenlabs.io](https://elevenlabs.io):

- Create an **API key**: click your profile (bottom-left) → **API Keys** →
  **Create API Key**. Make sure the key has BOTH **Text to Speech** and
  **Speech to Text** permissions enabled. Copy the key (starts with `sk_`).
- Pick a **voice**: open **Voices**, choose any voice you like (or a clone of
  your own voice), click it, and copy its **Voice ID**.

Then create your settings file and open it in a simple editor:

```bash
cp .env.example .env
nano .env
```

Paste your key after `ELEVENLABS_API_KEY=` and your voice ID after
`ELEVENLABS_VOICE_ID=`, then press `Ctrl+O`, `Enter` to save and `Ctrl+X` to
exit.

**Step 6 — set up the pronunciation dictionary** (one command; it uploads the
bundled math dictionary — `sinh`/`cosh`, Greek letters, math abbreviations —
to your ElevenLabs account and saves its ID into `.env` automatically):

```bash
venv/bin/python scripts/setup_pronunciation_dict.py
```

Later you can add your own entries (names, domain terms, acronyms) to the
dictionary in the ElevenLabs dashboard — the pipeline always uses the latest
version. Reference: `docs/elevenlabs_pronunciation_dict.md`.

**Step 7 — make your first video.** Start Claude Code inside the project
folder:

```bash
claude
```

and type (replace the URL with any recorded math talk):

```
/run-pipeline https://www.youtube.com/watch?v=... --math
```

That's it — Claude supervises the whole run. Finished videos land in
`pipeline/<source>/Video-N/final_video.mp4` with subtitles next to them.

> **macOS**: install [Homebrew](https://brew.sh), then
> `brew install git python ffmpeg pango pkg-config` and
> `brew install --cask mactex-no-gui`; everything else is identical.
> **Windows**: use WSL (Ubuntu) and follow the steps as written, or see
> [Manim's install guide](https://docs.manim.community/en/stable/installation.html)
> for native setup.

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
