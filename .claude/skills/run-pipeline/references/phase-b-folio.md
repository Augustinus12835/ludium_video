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
and then-states (`OPENAI_API_KEY`; model `gpt-image-2.5-sunburst` unless `FOLIO_GPT_MODEL` names another, e.g. `gpt-image-2`)
and Gemini 3 Pro Image with Google Search grounding for maps (`GOOGLE_CLOUD_API_KEY`). Budget
roughly US$5–7 of images per 20-minute episode (≈120–200 assets at ≈$0.04, maps ≈$0.13).
`FOLIO_DEFAULT_TIER=lite` moves every non-map asset to Gemini flash-lite (then only the Google
key is needed).

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
Every image is normalised to the house duotone; plates/maps are trimmed of platemarks; objects
become RGBA ink cutouts (a then-state shares its base's crop). Post-processing changes never
need new API calls: `folio.py refinish`. OpenAI rate-limits images per minute by account tier
and a call takes ~35–45 s; at 20 images/min `--jobs 12` runs at the ceiling (429s back off
automatically) — lower `--jobs` on a lower tier. Maps stay on grounded Gemini Pro. When forcing
a full regeneration, generate in dependency order: portraits, then bases, then then-states
(`--only` lists).

**Asset QA (clean-context vision subagent, ~60 assets each):** `folio.py contact` tiles the
assets; check lettering in pictures (only map `places` may be lettered), anachronism against
`world` (tolerate MINOR background anachronism unless it is wholly out of period or ruins the
scene; foreground subjects must be right), wrong likeness (a cast portrait must match its
`look`), anatomy, a then-state that changed its framing, a map with wrong geography. Fix by
editing the asset's `prompt` (positive wording — a negative paints its token) and
`folio.py assets … --only <id> --force`. Two rounds max.

**Stubborn assets climb the model ladder:** `lite` (gemini-3.1-flash-lite-image) → `flash`
(gemini-3.1-flash-image) → `gpt`. Reroll ONCE on the current model with a corrected prompt; if
the defect survives, `folio.py assets … --only <id> --escalate` moves it one rung up (records
`"model"` in folio.json) and regenerates. Framed plates climb automatically (one full-bleed
retry per rung). Image EDITS (`of` then-states) cannot reliably remove a figure or change a
count — for those, drop `of` and generate a fresh image. Maps never escalate (grounding is
Pro-only); fix their `places`/prompt instead. A manual `trim: [l, t, r, b]` (fractions of the
raw) crops a stubborn paper margin.

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

## Stage 6 — compile, subtitles

```bash
venv/bin/python scripts/folio.py compile pipeline/<L>/Video-N      # segment-wise, + narration
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
