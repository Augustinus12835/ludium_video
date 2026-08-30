# Failure-class playbooks

Always read the log tail before picking a fix; resume rather than restart (disk state is
authoritative); 3 attempts per failing step, then surface to the user. Never delete files
unless clearly corrupt output (truncated mp4, zero-byte audio) — check size + mtime first.

## Manim render failure

Symptoms: `FAIL: Manim render failed`, LaTeX errors, `cannot import name`, an
`AttributeError` traceback naming `frame_<N>_manim.py`.

Read the frame's `.py` and the error tail; diagnose with `templates/manim_system_prompt.md`
(rules 36–66 are the silent-defect catalogue). Recurring culprits: unbalanced `{}` in
`MathTex`; a `t2c` key inside ANY macro brace — `\frac`, `\int_{}`, `^{}`, `\text{}`, even a
bare `\mathrm{Var}(…)` (`Missing } inserted`); `\cancel`/`\ding`/other non-amsmath macros
(delete the call — a "fallback" reassignment after it is dead code); bare `^`/`\sin` in a
`Tex()` note, or `$` inside `MathTex`; the literal word `textcomp` anywhere in the file
(turns the preamble injection OFF → bare "error converting to dvi" on every quote glyph,
renders fine under manual manim); `⋯` U+22EF (hard kill; `×` U+00D7 is the SILENT one — use
`\times`/`\cdots`); `DEGREE` → `DEGREES`; `code.background_mobject` → `code.background`;
`Code(background=None)` raises; `font_size=` on axis-label getters (use
`MathTex(...).scale(0.7)`); `BackgroundRectangle(opacity=…)` → `fill_opacity=`;
`self.add(bg) or FadeIn(lbl)` (passes the Scene to `play()`); `add_step` overflow.
`installation does not support converting .dvi to SVG` from a hand-rolled parallel batch is
a LIE — two jobs shared a `--media_dir` and raced the Tex cache; give each its own. A render
at LOW CPU that never finishes is the zero-length `Line(p, p)` Cairo hang (rule 28), not a
slow render — no timeout fixes it. Make a targeted `Edit` — don't rewrite the file unless it's structurally rotten. Re-render the
single frame:

```bash
venv/bin/python -c "
from scripts.generate_math_animation import render_manim_scene
from pathlib import Path
p = Path('pipeline/<L>/Video-<N>/frames/frame_<F>_manim.py')
ok, msg = render_manim_scene(p.read_text(), str(p.parent / 'frame_<F>.mp4'), <duration>)
print(('OK ' if ok else 'FAIL: ') + msg[-1500:])"
```

(`<duration>` from `ffprobe` on the frame's mp3.) Then resume — the animate step skips
frames that already have their mp4.

`subprocess.TimeoutExpired` on a 4K render = a dense frame exceeded the ceiling (default
1800s). Resume with `MANIM_RENDER_TIMEOUT=2700` (or higher); never lower the resolution or
trim content. Dense worked-example and physics frames hit this most.

## Stale or missing renders (existence ≠ freshness)

`compile_video.py` concatenates whatever `frames/frame_N.mp4` exist and never checks them
against the sources; `pipeline.py … --from animate` treats an EXISTING `final_video.mp4` as
done and prints VIDEO COMPLETE. So a producer that edits a source and dies before
re-rendering, or re-renders after an audit and resumes `--from animate`, ships the OLD frame.
Guard before trusting any compiled video, both directions:

```bash
for src in Video-N/frames/*_manim.py; do
  n=$(basename "$src" _manim.py); mp4="Video-N/frames/$n.mp4"
  [ "$mp4" -nt "$src" ] || echo "STALE: $n source newer than mp4"
  [ "Video-N/final_video.mp4" -nt "$mp4" ] || echo "UNBAKED: $n mp4 newer than final_video"
done
```

Fix = re-render the frame, then `compile_video.py` + `generate_subtitles.py --force`
explicitly. Two caveats: the animate step reads every source up front, so a patch applied
mid-run renders from the OLD text with a NEWER mtime (guard passes — verify the rendered
output by measurement); and ten mp4s sharing one mtime after a cosmetic batch rewrite is a
false alarm (settle with a re-render + pixel diff). A killed mp4 encode leaves a file that
ffprobes as `moov atom not found` — or, worse, as a plausible SHORTER duration; verify each
compile against the expected length (sum of `audio/frame_*.mp3`), never "does ffprobe
return something". Compile is ~75–90 s/video, so a serial loop over 8 videos exceeds the
Bash tool's 10-min cap (`timeout` silently clamps at 600000 ms) — background it.

## Background jobs and liveness (orchestrator + producers)

- Never write `nohup … &` INSIDE a `run_in_background` Bash call: the harness already
  detaches; the extra `&` makes a grandchild it does not track, the wrapper exits "done"
  immediately (exit 0 = the launcher, not the job), and the next launch races the still-live
  one — two renders once wrote the same mp4s. One or the other, never both.
- `pgrep -f` the exact command before relaunching any long render or compile.
- `python script.py > log 2>&1` BUFFERS stdout — a log that stays empty looks identical to a
  hang. Watch filesystem state (`ls frames/*.mp4 | wc -l`, the result JSON) or run with
  `python -u`.
- A Monitor whose liveness check is `pgrep -f "<pattern>"` matches its OWN command line if
  the pattern appears in the script — it stays true forever. Check the artifact instead
  (`[ -f final_video.mp4 ]`) or use a pattern that cannot appear in your script.

## TTS narration check (halts the tts step before audio)

`✗ TTS narration check failed` names each frame, its spoken source, and offending tokens by
category. The gate only detects — false positives are expected and land here for triage.
The canonical category list and conversions live in `scripts/utils/narration_check.py` and
the prompt rules (`scripts/utils/tts_rules.py`). Summary:

| Category | Convert to |
|---|---|
| `numeral` | spelled out ("nineteen eighty-three") |
| `greek` / `math_symbol` | the name ("alpha", "square root of", "times", "degrees") |
| `hex` / `opaque` | spaced characters ("0 x D E A D…") |
| `differential` | spaced ("d x") |
| `greek_compound` | hyphen-bind the Greek letter to its variable: "delta X" → "delta-X", "lambda t" → "lambda-T", "two pi R" → "two pi-R" (spaced, the voice drops dead air between the tokens). Skips the article, a following differential ("d theta d t") and Python's `lambda X, colon` |
| `acronym` | spaced letters ("U T X O"; allowlisted ones aren't flagged). Includes mixed-case initialisms, where a lowercase letter inside the token does NOT make it a word — "VaR" is voiced "var", so write "value at risk"; also "CVaR", "DoS" — and plural/possessive forms ("UTXOs" → "U T X Os"). |
| `code_token` | spoken prose ("my func", "is equal to") |
| `variable_a` | uppercase the variable: "A-one", "A times t", "slope A" (lowercase `a` reads as the article; only `a` collides) |
| `sentence_a` | rephrase so "A" isn't the first word ("Matrix A times…") |

Preferred notation forms (correct, never flagged): `Ax`→"A-X", `A_x`→"A-X",
`sigma_n`→"sigma-N", `a_1`→"A-one", `Â`→"A-hat", `\bar{x}`→"X-bar". Subscripts are
hyphen-bound with NO spoken "sub" (2026-08-25) — legacy "A-sub-X" narration is not
wrong, just verbose; don't rewrite an already-voiced video for it.

Fix the source the report names — `script.json` narration for every frame class (only a
LEGACY pre-2026-08-30 video names `natural_narration` in `math_verification.json`; fix both
there when unsure). On-screen text keeps
its normal form; only spoken text changes. The gate fires in Stage 2 (the producer runs
tts): mechanical token conversions (the table above) may be applied directly; anything that
genuinely rephrases a sentence (`sentence_a`, pacing rewording) is spoken-text authoring —
route it through a one-shot subagent on the scripting model. Resume `--from tts`; for a
confirmed false positive only, resume once with `SKIP_NARRATION_CHECK=1`.

If an edit meaningfully lengthens a frame's audio on an animated frame, re-render that frame
after tts so the animation re-aligns (see CLAUDE.md "Fixing TTS / Narration").

**Voice / pronunciation quirks:**
- Never respell a token phonetically in narration ("it ter", "too pull") — the spoken text is
  written VERBATIM into the SRT. Reword around the token ("the iterative version"); the
  on-screen `Code()` keeps the real identifier. A stubborn proper noun that survives ~3
  `--resay` takes unchanged is a corrupted lexicon entry in the voice (a clone said "Sicily"
  for every "Thessal-" stem): fix with a **pronunciation-dictionary alias**
  (`setup_pronunciation_dict.py`), which leaves the SRT spelling correct, then resay.
- Post-hoc sizing of a re-TTS job from gate flags overstates it ~10×: on finished audio,
  numerals, acronyms and code tokens are fine to the ear; only HEX literals actually mangle
  (and they drop digits, intermittently). Transcribe the worst frames first.
- `export ELEVENLABS_VOICE_ID=…` does NOTHING (`load_dotenv(override=True)` wins); the
  `--voice-id` CLI flag is applied after and wins — confirm in the `Voice ID:` log line.


## API overload / transient network

ElevenLabs 429 / overloaded: wait 60s, resume; again → 5 min; third time → surface the
status to the user. Network blips (`ConnectionResetError`, `ReadTimeout`): retry
immediately. ElevenLabs `quota_exceeded` is a hard wall — surface it.

## Producer looks dead (orchestrator)

**File mtime is not a liveness signal.** A producer reading contact sheets and full-res
stills writes nothing for a long stretch — one can go ~30 minutes without touching a file
mid-audit and still be alive. Spawning a "recovery" agent against a live producer risks two
agents editing the same frames.

Before concluding a producer died, check in this order:

1. `ps -eo pid,etimes,pcpu,args | grep "manim render"` — an active render at high CPU means
   it's working. (A render at *low* CPU that never finishes is usually a zero-length-`Line`
   Cairo hang in the frame source — kill it and fix the frame, it's not a dead producer.)
2. Is the last-written artifact a *terminal* one for its step? `audit/` fully populated with
   no fixes yet = it is reading, not dead.
3. `SendMessage` it. Delivery is queued "at its next tool round", so a reply proves life;
   silence proves nothing on its own.

Only if it stopped with a **task-notification carrying a failure status** is it genuinely
gone. Two producers have been mis-declared dead: one had simply ended its turn with a report
(its state was on disk; a fresh agent resumed cleanly), the other's report arrived
CORRUPTED — the `<result>` carried a `<task-notification>` from a progress Monitor the
producer had armed itself, status `completed` — and it was ALIVE: it went on re-rendering
frames and compiling while the rescue producer worked the same directory, and two agents
compiled one `final_video.mp4`. A report that is not the producer's own prose is not a death
notice. Prefer giving a long-running producer the WHOLE job in one brief; where QA must sit
in the middle, write the QA output to a scratchpad file and hand it to whichever agent is
demonstrably alive; treat mp4 MTIME as the arbiter over any stale report text. Then take over from disk state rather than restarting the video — the pipeline
resumes, so rendered frames survive. If you already spawned a recovery agent and the
original turns out alive, `TaskStop` the recovery agent and verify it wrote nothing
(`find <dir> -newermt '-N minutes'`) before continuing.

## Other failures

| Symptom | Fix |
|---|---|
| ffmpeg size mismatch / "Stream specifier matches no streams" at compile | A frame mp4 is corrupt — re-render that frame, resume. |
| `FileNotFoundError: …/audio/frame_N.mp3` | TTS skipped a frame — re-run `generate_tts_elevenlabs.py <dir>/script.md`, resume. |
| Subtitle OOM / crash | Non-blocking — skip with `--from <next-step>`, tell the user subtitles need a rerun. |
| Subagent returns malformed JSON for a JSON step | Re-spawn once with the parse error appended; twice → inspect and surface. |
| "All complete" but no `final_video.mp4` | Run `compile_video.py` directly. |
| `moov atom not found` / short duration on a compiled mp4 | Encode was killed mid-write — recompile and verify duration against the audio sum. |
| Producer imports fail with `module 'struct' has no attribute…` | A generic temp name in the shared scratchpad root shadows a stdlib module — see phase-b.md (per-agent subdirectory). |
