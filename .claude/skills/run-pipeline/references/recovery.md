# Failure-class playbooks

Read the log tail before picking a fix, and resume rather than restart — disk state is
authoritative. Three attempts per failing step, then surface to the user. Delete a file only
when it is clearly corrupt output (truncated mp4, zero-byte audio); check size and mtime first.

## Manim render failure

Symptoms: `FAIL: Manim render failed`, LaTeX errors, `cannot import name`, an
`AttributeError` traceback naming `frame_<N>_manim.py`.

Read the frame's `.py` and the error tail; diagnose with `templates/manim_system_prompt.md`
(rules 36–80 are the silent-defect catalogue). Recurring culprits:

- unbalanced `{}` in `MathTex`;
- a `t2c` key inside any macro brace — `\frac`, `\int_{}`, `^{}`, `\text{}`, even a bare
  `\mathrm{Var}(…)` (`Missing } inserted`);
- `\cancel` / `\ding` / other non-amsmath macros — delete the call (a "fallback" reassignment
  after it is dead code);
- bare `^` / `\sin` in a `Tex()` note, or `$` inside `MathTex`;
- the literal word `textcomp` anywhere in the file — it turns the preamble injection off, so
  every quote glyph fails with a bare "error converting to dvi" (manual manim renders fine);
- `⋯` U+22EF is a hard kill; `×` U+00D7 fails silently — use `\times` / `\cdots`;
- `DEGREE` → `DEGREES`; `code.background_mobject` → `code.background`;
  `Code(background=None)` raises;
- `font_size=` on axis-label getters (use `MathTex(...).scale(0.7)`);
- `BackgroundRectangle(opacity=…)` → `fill_opacity=`;
- `self.add(bg) or FadeIn(lbl)` passes the Scene to `play()`;
- `add_step` overflow.

`installation does not support converting .dvi to SVG` from a hand-rolled parallel batch is
misleading: two jobs shared a `--media_dir` and raced the Tex cache — give each its own. A
render at low CPU that never finishes is the zero-length `Line(p, p)` Cairo hang (rule 28),
not a slow render; no timeout fixes it.

Make a targeted `Edit` (rewrite only a structurally rotten file) and re-render the single frame:

```bash
venv/bin/python -c "
from scripts.generate_math_animation import render_manim_scene
from pathlib import Path
p = Path('pipeline/<L>/Video-<N>/frames/frame_<F>_manim.py')
ok, msg = render_manim_scene(p.read_text(), str(p.parent / 'frame_<F>.mp4'), <duration>)
print(('OK ' if ok else 'FAIL: ') + msg[-1500:])"
```

(`<duration>` from `ffprobe` on the frame's mp3.) Then resume — animate skips frames whose
mp4 is newer than their source.

`subprocess.TimeoutExpired` on a 4K render means a dense frame exceeded the ceiling (default
1800 s). Resume with `MANIM_RENDER_TIMEOUT=2700` or higher; don't lower the resolution or trim
content. Dense worked-example and physics frames hit this most.

## Stale or missing renders

`compile_video.py` concatenates whatever `frames/frame_N.mp4` exist without checking them
against their sources, and `pipeline.py … --from animate` treats an existing
`final_video.mp4` as done. A producer that edits a source and stops before re-rendering, or
re-renders after an audit and resumes `--from animate`, ships the old frame. Check both
directions before trusting a compiled video:

```bash
for src in Video-N/frames/*_manim.py; do
  n=$(basename "$src" _manim.py); mp4="Video-N/frames/$n.mp4"
  [ "$mp4" -nt "$src" ] || echo "STALE: $n source newer than mp4"
  [ "Video-N/final_video.mp4" -nt "$mp4" ] || echo "UNBAKED: $n mp4 newer than final_video"
done
```

Fix: re-render the frame, then run `compile_video.py` and `generate_subtitles.py --force`
explicitly. Caveats:

- The animate step reads every source up front, so a patch applied mid-run renders the old
  text with a newer mtime and passes the check — verify the rendered output by measurement.
- Many mp4s sharing one mtime after a cosmetic batch rewrite is usually a false alarm; settle
  it with a re-render and pixel diff.
- A killed encode leaves a file that ffprobes as `moov atom not found`, or as a plausible
  shorter duration. Verify each compile against the expected length (sum of
  `audio/frame_*.mp3`), not merely that ffprobe returns something.
- Compile takes ~75–90 s per video; a serial loop over 8 videos exceeds the Bash tool's
  10-minute cap, so run it in the background.

## Background jobs and liveness

- Use `run_in_background` or `nohup … &`, not both: the harness already detaches, and the
  extra `&` creates an untracked grandchild, so the wrapper reports "done" immediately and the
  next launch races the still-live job.
- `pgrep -f` the exact command before relaunching any long render or compile.
- `python script.py > log 2>&1` buffers stdout, so an empty log looks like a hang. Watch
  filesystem state (`ls frames/*.mp4 | wc -l`, the result JSON) or run `python -u`.
- `pgrep -f "<pattern>"` matches its own shell when the pattern appears in the command line,
  so a Monitor liveness check built on it stays true forever, and a count can report live
  processes when there are none. Check the artifact instead (`[ -f final_video.mp4 ]`), or read
  `/proc/*/cmdline` and skip the reader.
- The same self-match makes `pkill -f "<pattern>"` kill the calling shell. Kill by a PID
  captured at launch, or scope the pattern to a path only your process owns
  (`pkill -f <your-scratch-dir>/`).
- Don't pattern-kill Manim (`pkill -f "manim render"`): sibling producers share the machine,
  and a pattern kill takes out every other video's renders, which surface much later as missing
  or stale mp4s. Check `pgrep -af manim` (allowing for self-match) to see whose renders are
  live first.

## TTS narration check (halts the tts step before audio)

`✗ TTS narration check failed` names each frame, its spoken source, and the offending tokens
by category. The gate only detects, and false positives are expected. The canonical list lives
in `scripts/utils/narration_check.py` and `scripts/utils/tts_rules.py`. Summary:

| Category | Convert to |
|---|---|
| `numeral` | spelled out ("nineteen eighty-three") |
| `greek` / `math_symbol` | the name ("alpha", "square root of", "times", "degrees") |
| `hex` / `opaque` | spaced characters ("0 x D E A D…") |
| `differential` | spaced ("d x") |
| `greek_compound` | hyphen-bind the Greek letter to its variable: "delta X" → "delta-X", "lambda t" → "lambda-T", "two pi R" → "two pi-R" (spaced, the voice drops dead air between the tokens). Skips the article, a following differential ("d theta d t") and Python's `lambda X, colon` |
| `acronym` | spaced letters ("U T X O"; allowlisted ones aren't flagged). Includes mixed-case initialisms — "VaR" is voiced "var", so write "value at risk"; also "CVaR", "DoS" — and plural/possessive forms ("UTXOs" → "U T X Os") |
| `code_token` | spoken prose ("my func", "is equal to") |
| `variable_a` | uppercase the variable: "A-one", "A times t", "slope A" (lowercase `a` reads as the article; only `a` collides) |
| `sentence_a` | rephrase so "A" isn't the first word ("Matrix A times…") |

Preferred notation forms (never flagged): `Ax`→"A-X", `A_x`→"A-X", `sigma_n`→"sigma-N",
`a_1`→"A-one", `Â`→"A-hat", `\bar{x}`→"X-bar". Subscripts are hyphen-bound with no spoken
"sub"; older "A-sub-X" narration isn't wrong, so don't rewrite an already-voiced video for it.

Fix the source the report names — `script.json` narration for every frame class. (Only a
legacy pre-2026-08-30 video names `natural_narration` in `math_verification.json`; fix both
when unsure.) On-screen text keeps its normal form; only spoken text changes. The gate fires in
Stage 2 (the producer runs tts) and the producer fixes it — token conversions and genuine
rephrasings (`sentence_a`, pacing rewording) alike — keeping every `On "…"` cue phrase
verbatim. Resume `--from tts`; for a confirmed false positive only, resume once with
`SKIP_NARRATION_CHECK=1`.

If an edit meaningfully lengthens an animated frame's audio, re-render that frame after tts so
the animation re-aligns (CLAUDE.md "Fixing TTS / Narration").

**Voice and pronunciation quirks:**
- Don't respell a token phonetically in narration ("it ter", "too pull"): spoken text is
  written verbatim into the SRT. Reword around it ("the iterative version"); the on-screen
  `Code()` keeps the real identifier.
- A proper noun that survives ~3 `--resay` takes unchanged is a corrupted lexicon entry in the
  voice. Fix it with a pronunciation-dictionary alias (`setup_pronunciation_dict.py`), which
  keeps the SRT spelling correct, then resay.
- Sizing a re-TTS job from gate flags on finished audio overstates it ~10×: numerals, acronyms
  and code tokens sound fine; only hex literals actually mangle (dropping digits
  intermittently). Transcribe the worst frames first.
- `export ELEVENLABS_VOICE_ID=…` has no effect (`load_dotenv(override=True)` wins); the
  `--voice-id` flag is applied after and wins — confirm in the `Voice ID:` log line.

## API overload / transient network

ElevenLabs 429 / overloaded: wait 60 s and resume; again → 5 min; a third time → surface the
status to the user. Network blips (`ConnectionResetError`, `ReadTimeout`): retry immediately.
ElevenLabs `quota_exceeded` is a hard wall — surface it.

## Session rate limit (orchestrator)

`You've hit your session limit · resets <time>` (429) terminates every subagent in the session
at once, mid-step, and nothing can be spawned until the reset. Around nine concurrent agents is
the practical ceiling; prefer finishing videos over starting them, and relaunch in batches of
~4, furthest-along first.

Artifacts on disk survive (every agent writes before reporting). What is lost is any finding
held only in your context — a review reported to you but not yet applied by its author. When a
kill notification arrives:

1. **Audit the disk** — zero-byte files, JSON validity, and every rendered frame's mp4 newer
   than its source.
2. **Persist every unapplied finding** to `pipeline/<L>/Video-N/REVIEW_ROUND<N>.md` before
   anything else. A review you relayed counts as unapplied unless the target's `script.json`
   mtime is later than when you sent it.
3. **Update `RESUME.md`** with per-video state, what is already paid for (TTS especially —
   don't re-run it), and each video's next step.

Keep `RESUME.md` current while things are healthy, so a restart is a lookup rather than a
reconstruction.

## A subagent stopped, or looks dead (orchestrator)

A subagent that ends its turn with a text report is reporting, not necessarily finished and
not dead — agents on long multi-part tasks sometimes end a turn with a progress summary that
announces the next step instead of taking it. When a report arrives:

1. **Check its artifacts on disk** against its stage's checklist (script written and reviewed;
   frames rendered; `final_video.mp4` probing to the right duration; audit done).
2. **If items remain open and it named no blocker**, `SendMessage` it naming them — e.g.
   "Frames 7 and 9 still have no mp4 and the video isn't compiled. Continue with them; if one
   is blocked, say what blocks it." Stop after two or three such continuations on the same
   task, then take over from disk state or surface to the user.
3. **If it started something still running** (a background render, a Monitor), wait for that
   to finish before judging the task.

Before concluding an agent died:

- **File mtime is not liveness.** An agent reading contact sheets and full-res stills can go
  half an hour without writing a file.
- `ps -eo pid,etimes,pcpu,args | grep "manim render"` — an active render at high CPU means it
  is working (low CPU that never finishes is the Cairo hang above).
- An `audit/` directory fully populated with no fixes applied yet means it is reading.
- `SendMessage` it: delivery is queued for its next tool round, so a reply proves life;
  silence alone proves nothing.
- Only a task notification carrying a failure status (e.g. a session limit) means it is gone.
  A notification whose text is not the agent's own prose — for instance a `<task-notification>`
  from a Monitor the agent armed itself — is not a death notice.

Spawning a recovery agent against a live producer puts two agents on the same frames and the
same `final_video.mp4`. If you did, and the original turns out alive, `TaskStop` the recovery
agent and confirm it wrote nothing:

```bash
find pipeline/<L>/Video-N -newermt "$(date -d '-N minutes' '+%F %T')"   # some finds reject relative -newermt
```

Where QA must sit between two agents, write its output to a scratchpad file and hand it to
whichever agent is demonstrably alive; treat mp4 mtimes as the arbiter over any stale report
text. Otherwise take over from disk state — the pipeline resumes, so rendered frames survive.

## Other failures

| Symptom | Fix |
|---|---|
| ffmpeg size mismatch / "Stream specifier matches no streams" at compile | A frame mp4 is corrupt — re-render that frame, resume. |
| `FileNotFoundError: …/audio/frame_N.mp3` | TTS skipped a frame — re-run `generate_tts_elevenlabs.py <dir>/script.md`, resume. |
| Subtitle OOM / crash | Non-blocking — skip with `--from <next-step>`, tell the user subtitles need a rerun. |
| Subagent returns malformed JSON for a JSON step | Re-spawn once with the parse error appended; a second failure → inspect and surface. |
| "All complete" but no `final_video.mp4` | Run `compile_video.py` directly. |
| `moov atom not found` / short duration on a compiled mp4 | Encode was killed mid-write — recompile and verify duration against the audio sum. |
| Imports fail with `module 'struct' has no attribute…` | A generic temp filename in the shared scratchpad root shadows a stdlib module — use a per-agent subdirectory (phase-b.md). |
