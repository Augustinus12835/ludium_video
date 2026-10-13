# Ludium Video

An AI pipeline that turns lectures and books into narrated educational videos,
driven end to end from [Claude Code](https://claude.com/claude-code):

- **Math and technical** (calculus, linear algebra, physics, finance, CS): every
  frame a Manim animation, every calculation checked with SymPy.
- **Humanities** (history, philosophy, literature): ~20-minute documentary
  episodes in the look of an illustrated book of engravings (**folio** mode).

Narration is synthesized with your choice of ElevenLabs, Cartesia, HeyGen or
**Drama 3**, an open-weights model you run on a [Modal](https://modal.com) GPU, and
animations and subtitles sync to it word by word.

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

You need a **Claude subscription** (Pro or Max) and **one TTS provider** — a hosted one, or
Drama 3 on Modal (see "Free narration" below).

1. **Install Claude Code:** `curl -fsSL https://claude.ai/install.sh | bash`, then run `claude` and sign in.
2. **Choose a TTS provider** and get an API key plus the ID of the voice to narrate in:

   | `TTS_PROVIDER` | Key | Voice |
   |---|---|---|
   | `elevenlabs` | `ELEVENLABS_API_KEY` (*Text to Speech* enabled) | `ELEVENLABS_VOICE_ID` |
   | `cartesia` | `CARTESIA_API_KEY` | `CARTESIA_VOICE_ID` |
   | `heygen` | `HEYGEN_API_KEY` | `HEYGEN_VOICE_ID` |
   | `drama3` | a Modal account (`pip install modal && modal setup`) | 1–3 reference clips in `inputs/voice/drama3_refs/` |

   Any voice works, including a clone of your own.
3. **Install the project:** run `claude` and paste
   `Set up https://github.com/Augustinus12835/ludium_video for me: clone it and install its dependencies.`
4. **Add your keys:** in the `ludium_video` folder, `cp .env.example .env`, set
   `TTS_PROVIDER` and fill in that provider's key and voice.
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

## Free narration: Drama 3 on Modal

[Fish Audio Drama 3](https://huggingface.co/fishaudio/drama-3) is an open-weights TTS model that
clones a voice zero-shot from a few short clips. `TTS_PROVIDER=drama3` runs it on a rented
[Modal](https://modal.com) H100 (`scripts/drama3_modal.py`) and force-aligns the result on the
same GPU, so animations and subtitles get word timestamps exactly as from a hosted provider.

**Why it matters:** Modal's Starter plan includes **US$30 of free compute every month**, and the
model's weights (~40 GB) sit within Modal's 1 TiB of free storage. With Claude Code doing every
judgement step under your subscription and Manim rendering on your own machine, **math and
technical video production — even at large volume — is essentially free.**

| What | Cost on Modal (our runs, Oct 2026) |
|---|---|
| Session start-up (load weights, ~3–4 min of H100) | ~US$0.25, paid once per batch |
| Narration, 16 requests in flight | ~US$0.02 per 10 minutes |
| One 10-minute video on its own | ~US$0.27 |
| One 10-minute video in a batch of ~8 | ~US$0.05 |
| **US$30 free credit, batched** | **~500–600 ten-minute videos a month (~100 hours of narration)** |

For comparison, a hosted provider at ~US$40 per million characters costs about US$0.35 for the
same 10-minute video. Batching is what keeps it cheap: `/run-pipeline` voices a whole lecture in
one session (`scripts/tts_drama3_batch.py`), and pairs a lecture of 4 or fewer videos with the next.

Setup, once:

1. `pip install modal && modal setup` (free account; credentials go to `~/.modal.toml`).
2. `python scripts/drama3_modal.py download` copies the weights to a Modal Volume (minutes, CPU only).
3. Put 1–3 clips of the narrator in `inputs/voice/drama3_refs/`: `ref_1.wav` + `ref_1.txt` (its exact
   transcript), up to 60 s each, clean speech in the delivery you want. Record yourself, or render
   the clips once from a hosted voice you like — the clone copies their timbre, pacing and
   pronunciation (rare names it never hears may vary between frames, so include them in a clip).
4. Set `TTS_PROVIDER=drama3` in `.env`.

Drama 3 is under the [Fish Audio Community License](https://huggingface.co/fishaudio/drama-3):
free for research and for commercial use while your annual revenue is under US$500k, with
"Built with Fish Audio" shown on a related page; clone only voices you have consent to use.
Modal prices and free credit are as of October 2026 — check [modal.com/pricing](https://modal.com/pricing).

**Transcribing raw audio or video (optional).** YouTube sources use the video's
captions, and books and decks need no transcription. Only an audio/video file, or a
YouTube video without captions, has to be transcribed: add `ELEVENLABS_API_KEY`
with *Speech to Text* enabled for ElevenLabs Scribe (about US$0.22 an hour of audio,
pay as you go — whichever TTS provider you chose). Without it, local files fall
back to local Whisper (`pip install openai-whisper`).

## Humanities (folio) setup

Folio also generates images, so it needs two more keys in `.env`:

| Key | Used for |
|---|---|
| `OPENAI_API_KEY` | engraved plates, portraits and objects — requires `gpt-image-2.5-sunburst` (gpt-image-2.5); there is no fallback model |
| `GOOGLE_CLOUD_API_KEY` | maps (Gemini with Google Search grounding); optional motion plates (Gemini Omni Flash) |

Plates are 4K canvases (3840×2160) that a camera travels over, and images cost roughly
**US$10–12 per 20-minute episode**. Moving plates (`scripts/folio_motion.py`) are optional
and off by default (~US$1.10 per 10-second clip). Rendering uses
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
