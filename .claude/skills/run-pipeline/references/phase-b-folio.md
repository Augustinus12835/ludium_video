# Phase B — folio ("a book of plates", humanities)

Folio is the humanities mode (`--folio`): history, philosophy, literature, religion — subjects
taught through argument and evidence rather than derivation. It is **narration-first**: the
script is written, reviewed and voiced before anything visual exists, and then a **freely
authored Remotion composition** is laid over the word-timed narration, scene by scene:
engraved plates, portrait cards, ink-cutout objects, grounded maps and "then-states", with
type, strikes, tallies, routes and lineage lines arriving on the spoken words, and
**word-synchronous karaoke captions burned in**. No templates: `remotion/src/folio.tsx` is a
primitive library, a director writes briefs, scene authors compose.

Pieces: `scripts/folio.py` (place · assets · refinish · grid · audit · prompt · still · lint ·
render · compile · sheet · contact · status), `scripts/build_narration_timeline.py`,
`remotion/src/folio.tsx`, `templates/folio_director_prompt.md`,
`templates/folio_scene_prompt.md`.

**Paid services beyond ElevenLabs.** Images: OpenAI `gpt-image` for plates, portraits, objects
and then-states (`OPENAI_API_KEY`; gpt-image-2.5, API id `gpt-image-2.5-sunburst`, is required —
there is no fallback model, because a plate drawn by another model drifts from the house style)
and Gemini 3 Pro Image with Google Search grounding for maps (`GOOGLE_CLOUD_API_KEY`). Budget
roughly US$10–12 of images per 20-minute episode (≈120–200 assets: 4K plates ≈$0.10,
portraits/objects ≈$0.04, 4K maps ≈$0.24).

**Setup once:** Node.js 18+, then `cd remotion && npm ci`.

## Stage 1 — narration (one scripting agent per video)

`content.txt` is a research DOSSIER, not a text to re-voice: the prompts number its paragraphs
`[P1]…[Pn]`, and the film carries ~75% of its length (clamp(dossier words × 0.75 / 150, 15, 25)
minutes). The agent owns the argument, the structure and every sentence; the dossier supplies
the evidence. A script written straight from its source re-voices the lecture (one early
episode shared 20% of its 10-grams with its dossier, an identical run of 89 words, and reached
its real thesis at frame 35 of 47) — hence two stages.

1. **1a. Argument map.** Render `render_step_prompt.py argument --video-dir <dir> --mode folio`
   to your scratchpad, follow it, write `Video-N/argument.json`: thesis (a claim, ≤ 30 words),
   question, cold open, 3–6 movements (claim · evidence with P-ids tagged attested / inferred /
   disputed · turn · word budget), `cut` (every unused paragraph, with a reason),
   `best_of_source`, `debates_named`, ≤ 3 `keep_verbatim` quotes, ending. Check it with
   `venv/bin/python scripts/audit_script.py <dir> --argument`, report to the orchestrator, and
   hold for review round 0.
2. **1b. Narration.** Once the map is approved, render `render_step_prompt.py script
   --video-dir <dir> --mode folio` (it injects `argument.json`; confirm `argument_map=…
   injected` in its `notes`), follow it, write `Video-N/script.json`, regenerate `script.md`.
   Each frame is one idea of 60–120 words and carries `movement` (0 = cold open) and `sources`
   (P-ids, plus `"common"` for anything the dossier lacks); the script has a top-level `thesis`.
   Write to the map — don't restore cut material, don't walk the dossier in its own order.
   Flowing documentary narration, TTS-safe (numbers spelled out). Author your own cold open.
   Before reporting, run `venv/bin/python scripts/audit_script.py <dir>` and clear every
   `dossier-verbatim` BLOCK (rewrite, or quote a `keep_verbatim` phrasing inside quotation
   marks); answer each CHECK or leave it for the reviewer with a reason. `visual.reference`
   fields are placeholders — the real visual track is `folio.json` (Stage 2).

   **Correct source errors silently.** Fix each in the narration and report it to the
   orchestrator, which propagates it to `content.txt` + `content_cleaned.txt`. Don't tell the
   viewer ("the source says X … it did not"). At most ~3 on-air corrections, only where the
   wrong version is genuinely in circulation, attributed to that circulation ("the figure
   usually given is…"). Report to the orchestrator once the script is written, then hold.
3. **Review (orchestrator-run)** — round 0 on `argument.json` before any prose, then rounds 1–2
   on the narration, each by a clean-context reviewer with the dossier in hand (rubric below).
   The agent applies the relayed fixes; the reviewer never supplies prose. Two rounds max per
   stage.
4. **TTS** — `venv/bin/python scripts/pipeline.py video <L>/Video-N --folio --no-review` voices
   the approved narration after the narration gate, then halts at the visual track (expected).
   If the narration gate halts, fix the flagged text per references/recovery.md and re-run.
5. **Timeline** — `venv/bin/python scripts/build_narration_timeline.py pipeline/<L>/Video-N`
   → `narration_timeline.json`/`.txt` (word-level). Report total duration and hold.

### Humanities review rubric

Two reviews, each by a clean-context reviewer with `content.txt` in hand. The reviewer reports
locations and reasons and **never writes replacement prose** — reviewer-supplied wording is
unverified and has introduced falsehoods.

**Round 0 — the argument map, before any prose.** Hand the reviewer `argument.json` and the
`audit_script.py --argument` output (movement count, unknown or unplaced paragraph IDs,
`keep_verbatim` really verbatim, word budgets). Block on:
- a thesis that is a topic, not a claim someone could dispute;
- movements that don't answer the question, or run in the dossier's order where the argument
  needs another;
- a substantive cut — a cause, a reversal, a key date, the author's best insight; a
  `best_of_source` that misses what the material is really about, or an idea left unplaced;
- an evidence item whose paragraph does not say what it is cited for, a wrong
  attested / inferred / disputed label, or `common` used for anything but uncontroversial
  knowledge;
- a `debates_named` entry the dossier does not present as a debate.

Also give the reviewer the scope boundary against neighbouring videos (what they teach), so a
foreshadowing line never becomes a lesson that belongs elsewhere.

**Rounds 1–2 — the narration.** Hand the reviewer the `audit_script.py --siblings` output,
`argument.json` and `script.json`. Rubric, in this order:
1. **Structure** — the thesis is stated by ~30 s; each movement opens with its claim and ends
   on a turn; the ending resolves the thesis (resonance, not summary).
2. **Argument** — the evidence supports each claim; attested / inferred / disputed are
   distinguished in words; debates named once where they matter; interpretations stay marked
   as the author's; no hedge tics.
3. **Overlap and selection** — every `dossier-verbatim` BLOCK is gone; overlap and ratio
   CHECKs are explained or fixed. Nothing in the approved map is dropped; nothing outside the
   dossier is added without a `common` tag.
4. **Accuracy** — every frame tagged `common` is fact-checked; every `unsourced-figure` flag is
   resolved; no invented scholar, quotation, date or number; quotations from a copyrighted
   translation stay short and come only from the dossier.
5. **Voice** — sentence cadence, lecture filler, rhetorical questions, mannered prose (SKILL.md
   "Narration style").
6. **TTS** — the audit covers the mechanics; flag names the voice may stumble on.

## Stage 2 — director (one subagent per video)

```bash
venv/bin/python scripts/folio.py prompt direct pipeline/<L>/Video-N --out <scratch>/direct.md
```
The subagent follows it and writes `Video-N/folio.json` (chrome, world, cast, assets, scenes
with verbatim anchors + briefs), then `folio.py place` (fix anchors) and `folio.py audit` (fix
every ERROR; image rate ≥ 6/min, ≥ 85 % of scenes with an image, 3–5 scenes/min, night scenes
2–3). Give the director the scope boundary against neighbouring episodes and every claim the
script review cut or marked disputed: it writes them to `off_screen`, which is printed atop
every scene author's prompt and blocked by `folio.py lint` (without it, scene authors add the
next episode's events or a cut claim as "helpful" labels). In a series, the first episode
establishes `world`, `cast` and `chrome.running`; later episodes carry them forward and mark
recurring portraits `"shared": true` (series library `pipeline/_folio_library/<PREFIX>/`, keyed
by the folder prefix before the first `_` — the SAME portrait image in every episode).

## Stage 3 — assets + asset QA

```bash
venv/bin/python scripts/folio.py assets pipeline/<L>/Video-N --jobs 12
```
**Big canvases + camera.** `folio.json` carries `"canvas": "4k"` (the director writes it;
`folio.py audit` warns when it is missing): every plate is generated at 3840×2160 on
gpt-image-2.5 and every grounded map at Gemini Pro imageSize 4K, then kept at 3840×2160
(then-states inherit their base's canvas). `<Plate camera={[…]}>` then tours the picture — a
building part by part, shot / reverse-shot between two speakers, a reveal, a tableau read
figure by figure — instead of labels and leader lines (scene prompt "the camera"). Detail
holds to ~2.5× zoom; `folio.py lint` warns past what a plate's canvas holds.
**Camera rhythm:** a move must change the shot (zoom ≥ 1.3× or pan ≥ 25% of the frame) on a
real shift of narrative focus, and the camera rests after it lands — lint ERRORS on nudges, a
move starting before the last one lands, and < 2 s rests (warns < 3 s). Faster subject
switches are hard cuts (`dur: 0`) or no move. Every move is one straight move — the mid-pan
pull-back is off (`arc: true` opts in), and a wide stop held < 4 s between two tighter ones is
a detour (lint ERROR). Coordinates
(overlays, camera stops, `grid`) stay in 1920×1080 image space. A folio.json without
`canvas` keeps 1920×1080 plates; `"canvas"` on one asset overrides.

Every image is normalised to the house duotone; plates/maps are trimmed of platemarks; objects
become RGBA ink cutouts (a then-state shares its base's crop). Post-processing changes never
need new API calls: `folio.py refinish`. OpenAI rate-limits images per minute by account tier
and a call takes ~35–45 s; at 20 images/min `--jobs 12` runs at the ceiling (429s back off
automatically) — lower `--jobs` on a lower tier. Maps stay on grounded Gemini Pro. When forcing
a full regeneration, generate in dependency order: portraits, then bases, then then-states
(`--only` lists).

**Likeness sheets.** `folio.json` carries `"likeness": "sheet"` (the director writes it; `audit` warns
when it is missing): a plate's cast reference is a head-only crop of the portrait beside its mirror
image (`raw/<id>.likeness.png`), not the whole bust, because whole busts got copied pose-for-pose. A
one-sided mark (a scar, a lost eye — in the `look`, or `"asymmetric": true` on the cast member) keeps a
single unmirrored head. Episodes made before it keep the bust (cache keys unchanged); set
`"likeness": "sheet"` on one asset when rerolling it for a copied pose, and stage the gaze and hands
in its prompt.

**Asset QA (clean-context vision subagent, ~60 assets each):** `folio.py contact` tiles the
assets; check lettering in pictures (only map `places` may be lettered), anachronism against
`world` (tolerate MINOR background anachronism unless it is wholly out of period or ruins the
scene; foreground subjects must be right), wrong likeness (a cast portrait must match its
`look`), **a pose copied from the portrait** (a figure keeping its bust's head turn and stare
instead of looking at whom the scene says), anatomy (arms and wrists that bend the wrong way), a then-state that changed its framing, a map with wrong geography. Fix by
editing the asset's `prompt` (positive wording — a negative paints its token) and
`folio.py assets … --only <id> --force`. Two rounds max.

**Stubborn assets:** reroll with a corrected prompt (`--only <id> --force`); plates, portraits
and objects always stay on gpt-image-2.5. A plate drawn with a paper frame retries once
automatically with a full-bleed nudge. Image EDITS (`of` then-states) cannot reliably remove a figure or change a
count — for those, drop `of` and generate a fresh image. Maps are grounded Gemini Pro; fix
their `places`/prompt instead. **Map redraw (opt-in):** grounded Gemini can keep lettering place
names on a map meant to be unlettered. `"redraw": true` on that map (or `"map_redraw": true` in
folio.json for every unlettered map) adds a second pass: the grounded map is redrawn on
gpt-image-2.5 with no writing, geography traced from it; the original stays at
`raw/<id>.grounded.png` and is reused on re-runs, so switching the pass on for a map already
made costs one gpt-image call, not a new Gemini one. A manual `trim: [l, t, r, b]` (fractions of the
raw) crops a stubborn paper margin.

## Stage 3b — presenter (only when `pipeline/<L>/presenter.json` exists)

Any time after the word timeline: direct the presenter and start its clips
(references/presenter.md "Per video"). They generate on Modal while assets and scenes are made.
The scene prompt then carries the presenter zones: during the hook keep each element and the plate's
subject at x < 1160; after it, keep the bottom-right corner (x > 1500, y > 660) clear.
`folio.py still` outlines the live zone in red, and captions move left automatically.

## Stage 4 — scene authors (parallel subagents)

Split the scenes into contiguous ranges of ~10–15 (one agent per range, 4–8 agents per video).
Break ranges only where the next scene enters by `dissolve`, so a `cut` continuation stays
inside one agent.

```bash
venv/bin/python scripts/folio.py prompt scenes pipeline/<L>/Video-N --scenes 12-24 --out <scratch>/scenes_12_24.md
```
Each agent follows the prompt's loop: LOOK at its assets (`folio.py grid` for anything it will
annotate) → write `frames/scene_NN.tsx` → `folio.py still --scene NN --auto` → Read every
still → fix → repeat until clean → `folio.py lint`. Stills are cheap (~1 s each) — the loop is
the quality mechanism; an author that never reads its stills is not done. Authors never run
`folio.py render` — the orchestrator renders once all ranges report (see Pitfalls).

## Stage 5 — render + review

```bash
systemd-run --user --scope -p MemoryMax=12G -p MemorySwapMax=0 \
  venv/bin/python scripts/folio.py render pipeline/<L>/Video-N --jobs 3
venv/bin/python scripts/folio.py sheet pipeline/<L>/Video-N          # folio_build/sheet_NN.jpg
```
(`systemd-run` caps memory on Linux; drop the prefix elsewhere.) Render speed ≈ 2.7× real time
on a 24-core box, so a 20-minute video is ~8 minutes. `render` skips scenes whose TSX, span,
captions, assets and library are unchanged (a `.mp4.key` beside each clip), so
fix-and-re-render only touches what changed.

**Frame review (clean-context vision subagent per ~30 scenes):** read the sheets (3 frames per
scene) and flag: an element in the caption band, text on a busy picture, an overlay off its
target, unreadable type, overcrowding, a static stretch, a fact on screen the narration does
not support, an anachronism. Return `[{scene, severity, issue, fix}]`; route fixes back to the
scene's author (SendMessage) or a fresh author with the scene prompt, tell authors to verify
with stills only, then run ONE render yourself after they all report.

## Stage 5b — motion plates (optional, off by default)

**Skip this stage unless you want motion in this episode.** Gemini Omni Flash is still
unstable and costs ~$1.10 per 10-second clip; a folio episode is finished at the stills pass +
compile, with the camera (`<Plate camera>`) doing the moving.

A few scenes become moving engraved plates: a Three.js blocking proxy (layout, camera, timing)
is restyled by **Gemini Omni Flash** into the folio look, then plays where the `Plate` was
(`<Motion>` in `remotion/src/folio.tsx`, asset kind `motion`). One CLI,
`scripts/folio_motion.py` (cues · proxy · ref · gen · extend · fit · sheet); kit in
`scripts/motion_proxy/` (`lib.js`: people with walk cycles, triremes, water, splash, theatre
pieces; `cd scripts/motion_proxy && npm ci`; headless Chrome at `$CHROME` or
`/usr/bin/google-chrome`); per-video work in `<video>/motion/`.

**What to animate.** WIDE shots whose motion is generic and continuous and whose detail stays
obscure: a whole army or fleet moving, crowds, rowing, climbing, work seen from afar, and
camera moves through a place (a theatre crane worked very well). The motion runs on in the
background under the narration. NEVER a close-up subject or a specific, delicate or
choreographed action: building a wall up close, storming a fort, ramming and towing ships,
overrunning a post or a sneak attack all failed in testing (impossible heads, morphing hands,
invented cuts). Comparisons, definitions, concepts, portraits and maps stay still. Clips are
made FOR the episode from its own plate; reuse one where a plate repeats or a later scene
continues the shot.

Per scene NN (the clip runs 10 s; `fit` stretches it ≤1.25×, ≥0.9×, to the scene):

```bash
V=pipeline/<L>/Video-1
venv/bin/python scripts/folio_motion.py cues  $V NN          # word cues in clip time
#   write $V/motion/scene_NN.js (Three.js, imports '../lib.js'): WIDE, many small figures,
#   gentle camera; continuity over beats; designed wide even when the plate is a close-up
venv/bin/python scripts/folio_motion.py proxy $V NN --test 0,3,6,9.5   # Read motion/scene_NN.test.jpg; ≤2 iterations
venv/bin/python scripts/folio_motion.py proxy $V NN
#   optional motion/scene_NN.ref.txt (period details, "seen from far away, small figures")
venv/bin/python scripts/folio_motion.py ref   $V NN --at 5   # gpt-image-2.5 redraws a proxy frame in the plate's style
#   write motion/scene_NN.prompt.txt (template below)
venv/bin/python scripts/folio_motion.py gen   $V NN          # Omni edit: proxy + ref + plate, ~$1.10, ~65 s
venv/bin/python scripts/folio_motion.py sheet $V NN          # Read it; re-gen ONCE if it cuts, morphs or ignores the blocking
venv/bin/python scripts/folio_motion.py extend $V NN         # only scenes > ~12.5 s: continue to scene/1.25 (~$0.10/s)
venv/bin/python scripts/folio_motion.py fit   $V NN [--crop W:H:X:Y] [--plate ID]
```

Prompt template (motion/scene_NN.prompt.txt): "Restyle this video completely as a living
sepia steel-engraving in exactly the style of the reference images: <IMAGE_REF_0> is this
shot redrawn, <IMAGE_REF_1> is the film's plate … Keep the camera move, the composition,
every figure's position and the timing exactly as in the input video; use it only as a guide
for layout and motion and do not show its blocky shapes. One continuous unbroken shot; wide,
distant view, small figures, no close-ups." Then the actors and the motion in order, then
"No text, lettering, signature, title or caption anywhere in the picture; no new people, no
cuts. No dialogue, no music."

Then swap the scene's `<Plate src="X" …>` for `<Motion src="m_X" …>` (keep `wash`, `until`,
`box`, children; `zoom` for a cue-driven push-in; `style` passes through), back the old TSX up
to `motion/scene_NN.tsx.pre_motion`, `folio.py lint`, `render --only …`, check stills, compile.
Parallelise with one subagent per 4–6 scenes: they run cues → gen → sheet and report the take
and crop. Only the orchestrator runs `fit` (it writes folio.json) and edits TSX.

Motion pitfalls:
- **Omni often frames the clip as a printed page**, with a cream margin and sometimes a fake
  engraved signature or caption at bottom right. A negative prompt does not stop it. Look at a
  late frame's edges and pass `fit --crop W:H:X:Y` (keep 16:9; crop the lettering off from the
  top). `ffmpeg delogo` smears visibly; don't use it.
- **Overlays traced on the still plate** (`Draw` lines, `Tag` `to=` arrows) misalign once the
  camera moves. Re-aim them from a gridded clip frame
  (`ffmpeg … -vf crop=…,scale=960:540,drawgrid=w=96:h=54`) or drop the arrow and keep the label.
- **A `then=` sequence** (intact → burning → burnt) is usually what the clip itself shows: drop
  it. Keep a dissolve to a different plate as a separate `Plate at=… fade=…` over a
  `Motion until=…`.
- **`plate_of` takes the scene's FIRST `<Plate>`.** When a map comes first, pass `fit --plate`.
  A plate reused by another scene gets the asset id `m_<plate>_sNN`.
- **`enter: "cut"` continuity**: when the next scene continues the same plate, give it the
  clip's continuation (an `extend` beyond the scene, trimmed) so the shot runs on across the
  cut instead of jumping back to the still.
- **Extend** continues the previous interaction (`previous_interaction_id`). Never set `task`
  on that follow-up turn (HTTP 400). It returns the whole longer video, billed only for the
  new seconds.
- A short scene (< 9 s) uses only the clip's first seconds (never faster than 0.9×): put the
  action early in the proxy.
- A motion clip and a camera track don't mix: a `<Motion>` replaces the plate, so drop the
  scene's `camera` (use `<Motion zoom>` for a push-in).

## Stage 6 — compile, subtitles

```bash
venv/bin/python scripts/folio.py compile pipeline/<L>/Video-N      # segment-wise, + narration (+ presenter)
ffprobe -v error -show_entries format=duration -of csv=p=0 pipeline/<L>/Video-N/final_video.mp4
venv/bin/python scripts/generate_subtitles.py pipeline/<L> --video N --force
```
Captions are burned in; the SRT is still generated (accessibility/search). A folio.json `tail`
(seconds of silent hold after the last word) is recorded in `tail_info.json` so the subtitle
drift guard accounts for it.

## Pitfalls

- **Hooks after an early return** crash the render with React error #310 (a component that
  returns `null` before calling `useCue`/`useSteps`). Library components are safe; custom
  scene components must call every hook first.
- **Cue phrase occurring twice in one scene** silently binds to the first — lint warns; use a
  longer phrase or `cue(p, {n: 2})`.
- **`enter: "cut"` continuity**: carried-over elements must sit at identical positions with
  `at={-1}`, and a continuing plate resumes its push (`push={[1.05, 1.08]}`); elements that
  don't carry over need `until` before the cut or they vanish on it.
- **Plate children are in image space** (1920×1080 of the asset, riding the push) — read
  coordinates off `folio.py grid`, never off a still.
- **One `folio.py render` per video at a time.** Every render bundles into the same
  `folio_build/` render dir and removes it on start, so two concurrent renders wipe each other
  mid-run ("No entry point specified", `EISDIR`, "error … rendering frame N"). `folio.py still`
  is safe in parallel. After a collision, check no render is running and re-run `render` once —
  it skips scenes whose `.mp4.key` matches.
- **Thin display type fails on a phone**: the library pins Bodoni's optical size to a sturdy
  text cut; never override `fontVariationSettings` or use weight < 500.
- `preview_video.mp4` (with `"end_at"` in folio.json) is a pilot preview: narration cut at
  `end_at`.
