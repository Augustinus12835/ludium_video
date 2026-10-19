# Ludium Video

An AI pipeline that turns lectures and books into narrated educational videos, driven end to end
from [Claude Code](https://claude.com/claude-code).

| Mode | For | Visuals |
|---|---|---|
| `--math` / `--technical` | calculus, linear algebra, physics, finance, CS | Manim animation per frame; every calculation checked with SymPy |
| `--folio` | history, philosophy, literature | ~20-min documentary episodes as an illustrated book of engravings |

Narration comes from ElevenLabs, Cartesia, HeyGen or **Drama 3** (open weights, on your own
[Modal](https://modal.com) GPU). Animations and subtitles sync to it word by word.

## See it

- **[The Video That Made Itself](https://www.youtube.com/watch?v=x-iBXDS6faQ)**: the math/technical
  pipeline, explained by a video it made. *(It says you need an ElevenLabs subscription; the
  provider is now your choice, and Drama 3 can run free — see below.)*
- **[From Book to Documentary](https://www.youtube.com/watch?v=v2g8pILvm10)**: the folio pipeline,
  and how each script and picture is checked.
- **[Plato's Republic, Episode 1](https://www.youtube.com/watch?v=rkzo8eYfgPY)**: a folio episode
  from an open textbook.
- More on the [Ludium YouTube channel](https://www.youtube.com/@LudiumAI).

## How it works

```
Source → Transcribe → Clean → Segment → per video: Script → Review → TTS → Visuals → Compile → Subtitles
```

- **Judgement steps** (clean, segment, script, review, frame authoring) run as Claude Code
  subagents under your Claude subscription: no Anthropic API key, no per-token bill.
- **Deterministic steps** (transcribe, TTS, render, compile, subtitles) run through
  `scripts/pipeline.py`, resumable from disk.

## Getting started

You need a **Claude subscription** (Pro or Max) and **one TTS provider**.

1. **Install Claude Code:** `curl -fsSL https://claude.ai/install.sh | bash`, then `claude` and sign in.
2. **Install the project:** run `claude` and paste
   `Set up https://github.com/Augustinus12835/ludium_video for me: clone it and install its dependencies.`
3. **Add your keys:** in `ludium_video`, `cp .env.example .env`, set `TTS_PROVIDER` and that
   provider's key and voice (any voice works, including a clone of your own):

   | `TTS_PROVIDER` | Key | Voice |
   |---|---|---|
   | `elevenlabs` | `ELEVENLABS_API_KEY` (*Text to Speech* enabled) | `ELEVENLABS_VOICE_ID` |
   | `cartesia` | `CARTESIA_API_KEY` | `CARTESIA_VOICE_ID` |
   | `heygen` | `HEYGEN_API_KEY` | `HEYGEN_VOICE_ID` |
   | `drama3` | a Modal account (see below) | 1–3 clips in `inputs/voice/drama3_refs/` |

4. **Make a video:** inside the folder, run `claude` and type

   ```
   /run-pipeline https://www.youtube.com/watch?v=... --math        # pure math
   /run-pipeline inputs/my_recording.mp4 --technical               # science, finance, CS, engineering
   /run-pipeline https://www.youtube.com/watch?v=... --folio       # humanities
   ```

Sources: a YouTube URL, a video or audio file, a PDF or Markdown book chapter, or a PPTX deck
(`.claude/skills/run-pipeline/references/sources.md`). Output:
`pipeline/<source>/Video-N/final_video.mp4` with subtitles beside it.

**Transcription (only for audio/video files or YouTube videos without captions):** add
`ELEVENLABS_API_KEY` with *Speech to Text* enabled for Scribe (~US$0.22/hour, pay as you go);
without it, local files fall back to Whisper (`pip install openai-whisper`).

## Free narration: Drama 3 on Modal

[Fish Audio Drama 3](https://huggingface.co/fishaudio/drama-3) clones a voice zero-shot from a few
clips. `scripts/drama3_modal.py` runs it on a Modal H100 and force-aligns the words on the same GPU,
so you get word timestamps as from a hosted provider. Modal's free **US$30/month** credit covers
**~500 ten-minute videos** when batched (`/run-pipeline` voices a whole lecture per session):

| Cost (Oct 2026) | |
|---|---|
| Session start-up (~3–4 min) | ~US$0.25 per batch |
| Narration | ~US$0.02 per 10 min |
| 10-min video, alone / in a batch of ~8 | ~US$0.27 / ~US$0.05 |
| Hosted provider, for comparison | ~US$0.35 |

**Setup, once:**

1. `pip install modal && modal setup`
2. `python scripts/drama3_modal.py download` (weights, ~40 GB, to a Modal Volume)
3. Add 1–3 clips to `inputs/voice/drama3_refs/`: `ref_1.wav` + `ref_1.txt` (exact transcript),
   ≤ 60 s of clean speech each. Say any rare names in a clip, or they may vary.
4. `TTS_PROVIDER=drama3` in `.env`. Optional: `DRAMA3_TAGS=1` lets a subagent add inline stress
   (`<emphasis>`, `<slow>`, `[pause]`) without changing the words.

Licence: [Fish Audio Community License](https://huggingface.co/fishaudio/drama-3). Free for
commercial use under US$500k annual revenue, with a "Built with Fish Audio" credit. Clone only
voices you have consent to use. Check current prices at [modal.com/pricing](https://modal.com/pricing).

## On-screen presenter (optional)

A **lip-synced presenter from one photo** (you, a consenting colleague, or a drawn character):
large beside the visuals in the opening hook, then in a corner window (bottom right in Manim
videos; top right in folio, on a square of the book's paper). A subagent *directs*
it, giving sentences an expression with a teaching job, and frames keep its zones clear.
Example: [Single Variable Calculus 1.1 in Japanese](https://www.youtube.com/watch?v=lp75FjCqTZ0),
presented by the anime tutor ルミ.

Runs on Modal: the hook on [LongCat-Video-Avatar 1.5](https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5)
(MIT), the corner window on [SoulX-FlashTalk-14B](https://huggingface.co/Soul-AILab/SoulX-FlashTalk-14B)
(Apache-2.0) — about US$5 per 10-minute video. Off unless you pass `--presenter`.

1. `pip install modal 'rembg[cpu]' && modal setup`, then
   `python scripts/longcat_avatar_modal.py download` (~57 GB) and
   `python scripts/flashtalk_modal.py download` (~54 GB).
2. `python scripts/presenter.py setup my_photo.jpg` writes `inputs/presenter/default/`. Use a sharp,
   well-lit, front-facing photo, waist up. Describe the person in one sentence
   in `presenter.json` → `"look"`.

| Cost per 10-min video (Oct 2026) | |
|---|---|
| Hook + window | ~US$16 (the free credit covers about two) |
| Hook only (`--presenter-hook-only`) | ~US$2.40 |

Clips regenerate only when their narration or direction changes.

## Humanities (folio) setup

| Need | For |
|---|---|
| `OPENAI_API_KEY` | plates, portraits, objects (`gpt-image-2.5-sunburst`, no fallback) |
| `GOOGLE_CLOUD_API_KEY` | grounded maps (Gemini); optional motion plates (Gemini Omni Flash) |
| Node.js 18+, `cd remotion && npm ci` | rendering with [Remotion](https://www.remotion.dev) |

Images cost ~US$10–12 per 20-minute episode. Motion plates (`scripts/folio_motion.py`) are off by
default (~US$1.10 per 10 s). To split a book into episodes, write a manifest like
`docs/examples/plato_republic_episodes.json`.

## More

- `CLAUDE.md`: modes, everyday fixes (a frame, a sentence, subtitles), script reference.
- `.claude/skills/run-pipeline/`: the playbooks the supervisor skill follows.

## License

MIT, see [LICENSE](LICENSE). Bundled fonts: SIL Open Font License / Apache 2.0
(`remotion/public/fonts/licenses/`).
