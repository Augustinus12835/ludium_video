# Ludium Video

An AI pipeline that turns lectures and books into narrated educational videos,
driven end to end from [Claude Code](https://claude.com/claude-code):

- **Math and technical** (calculus, linear algebra, physics, finance, CS): every
  frame a Manim animation, every calculation checked with SymPy.
- **Humanities** (history, philosophy, literature): ~20-minute documentary
  episodes in the look of an illustrated book of engravings (**folio** mode).

Narration is synthesized with ElevenLabs, and animations and subtitles sync to
it word by word.

## See it

- **[The Video That Made Itself](https://www.youtube.com/watch?v=x-iBXDS6faQ)**:
  the math/technical pipeline, explained by a video it made.
- **[From Book to Documentary](https://www.youtube.com/watch?v=v2g8pILvm10)**:
  the folio pipeline, from a book to a finished film, and how each script and
  picture is checked.
- **[Plato's Republic, Episode 1](https://www.youtube.com/watch?v=rkzo8eYfgPY)**:
  a folio episode made from an open textbook.

More math and technical videos: the [Ludium YouTube channel](https://www.youtube.com/@LudiumAI).

## How it works

Judgement steps (cleaning, segmenting, scriptwriting, review, frame authoring)
run as **Claude Code subagents** under your Claude subscription, so there is no
Anthropic API key and no per-token bill. Deterministic steps (transcription,
TTS, rendering, compiling, subtitles) run through `scripts/pipeline.py`, which
is resumable from disk.

```
Source → Transcribe → Clean → Segment → per video: Script → Review → TTS → Visuals → Compile → Subtitles
```

## Getting started

You need a **Claude subscription** (Pro or Max) and an **ElevenLabs account**.

1. **Install Claude Code:** `curl -fsSL https://claude.ai/install.sh | bash`, then run `claude` and sign in.
2. **Get ElevenLabs credentials:** an API key with *Text to Speech* and *Speech to Text* enabled, and a Voice ID.
3. **Install the project:** run `claude` and paste
   `Set up https://github.com/Augustinus12835/ludium_video for me: clone it and install its dependencies.`
4. **Add your keys:** in the `ludium_video` folder, `cp .env.example .env`, fill in
   `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`, then run
   `venv/bin/python scripts/setup_pronunciation_dict.py`.
5. **Make a video:** inside the folder, run `claude` and type

```
/run-pipeline https://www.youtube.com/watch?v=... --math        # pure math
/run-pipeline inputs/my_recording.mp4 --technical               # science, finance, CS, engineering
/run-pipeline https://www.youtube.com/watch?v=... --folio       # humanities
```

Sources can be a YouTube URL, a video or audio file, a PDF or Markdown book
chapter, or a PPTX deck, including your own lectures
(`.claude/skills/run-pipeline/references/sources.md`). Finished videos land in
`pipeline/<source>/Video-N/final_video.mp4` with subtitles beside them.

## Humanities (folio) setup

Folio also generates images, so it needs two more keys in `.env`:

| Key | Used for |
|---|---|
| `OPENAI_API_KEY` | engraved plates, portraits and objects (`gpt-image-2.5-sunburst`; set `FOLIO_GPT_MODEL=gpt-image-2` if your account lacks it) |
| `GOOGLE_CLOUD_API_KEY` | maps (Gemini with Google Search grounding) |

Images cost roughly **US$5–7 per 20-minute episode**. Rendering uses
[Remotion](https://www.remotion.dev): install Node.js 18+, then
`cd remotion && npm ci`. A book can be divided into episodes with a manifest;
`docs/examples/plato_republic_episodes.json` is a worked example.

## More

- `CLAUDE.md`: modes, day-to-day workflows (fixing a frame, re-voicing a
  sentence, subtitles) and the script reference.
- `.claude/skills/run-pipeline/`: the playbooks the supervisor skill follows.

## License

MIT, see [LICENSE](LICENSE). Bundled fonts are under the SIL Open Font License /
Apache 2.0 (`remotion/public/fonts/licenses/`).
