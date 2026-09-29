#!/usr/bin/env python3
"""
Script generation prompts (math, technical and folio modes).

script.json (structured data) + script.md (human-readable) are authored by a
Claude Code subagent: render the mode's prompt with
`render_step_prompt.py script --video-dir DIR --mode <math|technical|folio>`, save
the subagent's JSON to script.json, and regenerate script.md via
script_parser.save_script. This module holds the prompt templates and the
source/duration-hint builders that render_step_prompt.py imports.

Pipeline position:
  segments.json + Video-N/content.txt
      → [folio only: argument prompt → argument.json, reviewed (round 0)]
      → script prompt (this module, via render_step_prompt.py) → subagent
      → script.json (source of truth, structured data)
      → script.md (derived from JSON, for human review)
"""

import sys
import json
import re
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.tts_rules import (
    ERA_MARKER_RULES,
    TECHNICAL_NARRATION_TTS_RULES,
    MATH_NARRATION_TTS_RULES,
)


# =============================================================================
# HUMANITIES NARRATION — folio
# =============================================================================
# A humanities script is written from a RESEARCH DOSSIER (content.txt, paragraphs
# numbered [P1]…[Pn]), not re-voiced from it, in two stages:
#   1. `render_step_prompt.py argument` → Video-N/argument.json: thesis, 3–6
#      movements with claim / evidence (P-ids, attested|inferred|disputed) / turn,
#      an explicit cut list, best_of_source, keep_verbatim. A clean-context
#      reviewer checks it against the dossier (review round 0).
#   2. `render_step_prompt.py script --mode folio` injects the approved map and asks
#      for narration at ~75% of the dossier's length, each frame tagged with its
#      `movement` and `sources`.
# audit_script.py then gates the prose mechanically (dossier-verbatim BLOCK,
# overlap / ratio / unsourced-figure / filler / cadence / movement-shape CHECKs).
# Written straight from its source, a script re-voices the lecture: one early
# episode shared 20% of its 10-grams with its dossier (an identical run of 89
# words) and reached its real thesis at frame 35 of 47.


HUMANITIES_SCRIPT_MODES = ("folio",)
DOSSIER_SELECTION_RATIO = 0.75     # narration words ≈ 0.75 × dossier words
NARRATION_WPM = 150.0              # 2.5 words per second
HUMANITIES_MIN_MINUTES = 15.0      # long-form floor (folio)
HUMANITIES_MAX_MINUTES = 25.0
ARGUMENT_FILENAME = "argument.json"


def split_dossier_paragraphs(content: str) -> list:
    """[(pid | None, text)] in dossier order. Blank-line-separated blocks get IDs
    P1…Pn; a leading '#' line is a heading (pid None) and is never numbered.
    The single numbering used by the argument prompt, the script prompt and
    audit_script.py — change it here or the P-ids stop agreeing."""
    out, n = [], 0
    for block in re.split(r"\n\s*\n", content or ""):
        lines = block.strip().split("\n")
        while lines and lines[0].lstrip().startswith("#"):
            out.append((None, lines.pop(0).strip()))
        rest = "\n".join(lines).strip()
        if rest:
            n += 1
            out.append((f"P{n}", rest))
    return out


def dossier_paragraph_map(content: str) -> Dict:
    """{'P1': text, …} — headings excluded."""
    return {pid: text for pid, text in split_dossier_paragraphs(content) if pid}


def humanities_targets(content: str, mode: str = "folio") -> tuple:
    """(target_minutes, target_words) for a humanities script.

    folio (long-form, ~20-min videos):
        minutes = clamp(dossier_words × 0.75 / 150, 15, 25)
    `mode` is kept for callers.
    """
    words = len((content or "").split())
    raw = words * DOSSIER_SELECTION_RATIO / NARRATION_WPM
    minutes = max(HUMANITIES_MIN_MINUTES, min(HUMANITIES_MAX_MINUTES, raw))
    return minutes, int(round(minutes * NARRATION_WPM))


def build_dossier_block(segment_meta: Dict, content: str) -> str:
    """The humanities stand-in for build_source_block: the same content, framed
    as background research with citable paragraph IDs instead of 'the lecture
    material this video must teach'."""
    paras = split_dossier_paragraphs(content)
    n = sum(1 for pid, _ in paras if pid)
    body = "\n\n".join(f"[{pid}] {text}" if pid else text for pid, text in paras)
    takeaways = segment_meta.get("key_takeaways") or []
    notes = ""
    if isinstance(takeaways, list) and takeaways:
        notes = ("Segmenter's notes (a hint about what this part covers, not a checklist):\n"
                 + "\n".join(f"- {t}" for t in takeaways) + "\n")
    return (
        "RESEARCH DOSSIER\n\n"
        f"Working title (a label from the segmentation step, not your title): "
        f"{segment_meta.get('title', 'Untitled')}\n"
        f"Theme: {segment_meta.get('core_concept', '') or '(none given)'}\n"
        f"{notes}"
        f"Length: {len(content.split()):,} words in {n} numbered paragraphs.\n\n"
        "The text below is background material assembled from lectures or a book. Treat it as "
        "a dossier of established facts, evidence and scholarly positions — not as a text to "
        "teach, follow, or compress. Its order is the author's order, not yours. Its asides, "
        "examples and transitions are the author's, not yours; keep one only if it does work "
        "in your argument. Paragraphs are numbered [P1]…[Pn] so you can cite them; a line starting "
        "with \"#\" is a heading, not content.\n\n"
        f"{body}"
    )


# --- Stage 1: the argument map ------------------------------------------------

ARGUMENT_SYSTEM = (
    "You are a documentary writer and historian planning a spoken script for an educational "
    "documentary film. You work from a research dossier, the way a documentary writer works "
    "from an academic consultant's notes: you own the argument, the structure and the "
    "selection. This step decides the argument; the prose is written later, to your plan. "
    "Output ONLY valid JSON."
)

_ARGUMENT_TASK = """WHAT THE MAP DECIDES
- "thesis": one claim, in plain words, at most thirty words. A claim someone could dispute ("Sparta's famous discipline answered a fear of its own helots more than any love of war"), not a topic ("Spartan society").
- "question": the question the film answers — the one a viewer asks after the cold open.
- "cold_open" (about thirty seconds, ~75 words): a concrete image, number or tension FROM THE DOSSIER, the tension it sets up, and the sentence that turns it toward the thesis. Never a summary of what is coming.
- "movements": three to six, in the order the ARGUMENT needs — the dossier's order only where that is also the argument's order. Each movement has the question it answers, its claim in one sentence, the evidence that supports the claim, the turn it ends on (what the viewer now understands that they did not a minute earlier), and a word budget. A movement runs ninety seconds to six minutes (~225–900 words).
- "evidence" items carry the paragraph IDs they come from and how the dossier presents them: "attested" (a source says it), "inferred" (historians reason to it), or "disputed" (the dossier reports disagreement).
- "cut": every paragraph you do not use, each with a one-line reason ("repeats P7", "lecturer's aside", "background the thesis does not need"). Never cut a cause, a reversal, a key date, or the lecturer's best insight.
- "best_of_source": the three to five ideas this material is really about — the lecturer's sharpest insights, the things a specialist would be sorry to see dropped. Each is placed in a movement.
- "debates_named": the scholarly disagreements the film names as disagreements — only ones the dossier itself presents as disputed.
- "keep_verbatim": at most three phrasings from the dossier worth quoting word for word (a translated line, an ancient author's sentence, a memorable formulation). They will be spoken inside quotation marks; everything else is written fresh.
- "ending": the resonance the thesis leaves — an image, consequence or open question the whole film has earned. Not a summary.

RULES
- Every evidence item cites real paragraph IDs, and that paragraph must actually say it. "common" is allowed only for uncontroversial common knowledge the dossier lacks (a well-known date, where a city lies) — never for a quotation, a named scholar, a statistic, or a contested claim.
- Never attribute a view to "scholars" or to a named historian unless the dossier does.
- Every paragraph ID appears at least once: in some evidence list (cold open and ending included) or in "cut".
- Word budgets, cold open included, sum to roughly the target below.
- The map is a plan, not a draft: about 600–900 words of JSON.

OUTPUT (bare JSON, no markdown fences):
{
  "thesis": "...",
  "question": "...",
  "cold_open": {"image": "...", "tension": "...", "turn_to_thesis": "...", "sources": ["P3"], "word_budget": 75},
  "movements": [
    {"number": 1, "title": "...", "question_answered": "...", "claim": "...",
     "evidence": [{"point": "...", "source": ["P12", "P13"], "kind": "attested"}],
     "turn": "...", "word_budget": 500}
  ],
  "debates_named": [{"debate": "...", "source": ["P20"]}],
  "cut": [{"id": "P7", "reason": "..."}],
  "keep_verbatim": [{"text": "...", "source": "P15"}],
  "best_of_source": [{"idea": "...", "source": ["P9"], "movement": 2}],
  "ending": {"resonance": "...", "sources": ["P34"]},
  "word_budget_total": 2400
}"""

_MODE_FILM = {
    "folio": "a calm narrator over a book of illustrated plates",
}


def build_argument_prompt(segment_meta: Dict, content: str, mode: str = "folio") -> str:
    """Stage-1 user prompt: plan the argument (argument.json), no prose."""
    minutes, target_words = humanities_targets(content, mode)
    words = len(content.split())
    return (
        f"You are planning the argument of a documentary-style educational film: "
        f"{_MODE_FILM.get(mode, _MODE_FILM['folio'])}. It runs about {int(round(minutes))} "
        f"minutes, ~{target_words:,} spoken words at 150 words a minute — "
        f"{100 * target_words / max(words, 1):.0f}% of the dossier's {words:,} words, so "
        "selection is part of the job. This step produces the ARGUMENT MAP only, no narration. "
        "A reviewer checks the map against the dossier before any prose is written, and the "
        "narration is then written to the approved map.\n\n"
        + build_dossier_block(segment_meta, content)
        + "\n\n---\n\n"
        + _ARGUMENT_TASK.replace('"word_budget_total": 2400',
                                 f'"word_budget_total": {target_words}')
        + f"\n\nTARGET: ~{target_words:,} words in all.\n"
    )


def load_argument_map(video_dir) -> tuple:
    """(argument dict | None, error string). Missing file → (None, '')."""
    path = Path(video_dir) / ARGUMENT_FILENAME
    if not path.exists():
        return None, ""
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.lower().startswith("json") else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"{path} is not valid JSON ({e})"
    if not isinstance(data, dict):
        return None, f"{path} is not a JSON object"
    return data, ""


def _pids(value) -> list:
    if isinstance(value, str):
        return [value]
    return [v for v in (value or []) if isinstance(v, str)]


def validate_argument_map(arg: Dict, content: str, mode: str = "folio") -> list:
    """Mechanical checks on an argument map — the arithmetic half of review round 0.
    Returns a list of problem strings (empty = clean)."""
    problems = []
    paras = dossier_paragraph_map(content)
    known = set(paras)
    thesis = (arg.get("thesis") or "").strip()
    if not thesis:
        problems.append("no thesis")
    elif len(thesis.split()) > 30:
        problems.append(f"thesis is {len(thesis.split())} words (≤ 30)")
    movements = arg.get("movements") or []
    if not 3 <= len(movements) <= 6:
        problems.append(f"{len(movements)} movements (need 3–6)")
    used = set()
    unknown = set()

    def take(ids):
        for p in _pids(ids):
            if p == "common":
                continue
            (used if p in known else unknown).add(p)

    take((arg.get("cold_open") or {}).get("sources"))
    take((arg.get("ending") or {}).get("sources"))
    for m in movements:
        for ev in m.get("evidence") or []:
            take(ev.get("source"))
            if ev.get("kind") not in ("attested", "inferred", "disputed"):
                problems.append(f"movement {m.get('number')}: evidence kind "
                                f"{ev.get('kind')!r} (attested|inferred|disputed)")
                break
        wb = m.get("word_budget")
        if isinstance(wb, (int, float)) and not 225 <= wb <= 900:
            problems.append(f"movement {m.get('number')}: word_budget {wb} "
                            "(a movement runs 90 s–6 min, ~225–900 words)")
    for d in arg.get("debates_named") or []:
        take(d.get("source"))
    for b in arg.get("best_of_source") or []:
        take(b.get("source"))
    cut = set()
    for c in arg.get("cut") or []:
        pid = c.get("id") if isinstance(c, dict) else c
        if isinstance(pid, str):
            (cut if pid in known else unknown).add(pid)
    if unknown:
        problems.append("unknown paragraph IDs: " + ", ".join(sorted(unknown, key=_pid_key)))
    unplaced = sorted(known - used - cut, key=_pid_key)
    if unplaced:
        problems.append(f"{len(unplaced)} paragraph(s) neither used nor cut: "
                        + ", ".join(unplaced[:20]) + (" …" if len(unplaced) > 20 else ""))
    both = sorted(used & cut, key=_pid_key)
    if both:
        problems.append("cited as evidence AND cut: " + ", ".join(both))
    kv = arg.get("keep_verbatim") or []
    if len(kv) > 3:
        problems.append(f"{len(kv)} keep_verbatim phrasings (≤ 3)")
    squash = lambda s: " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())
    dossier_sq = squash(content)
    for k in kv:
        text = k.get("text", "") if isinstance(k, dict) else str(k)
        if text and squash(text) not in dossier_sq:
            problems.append(f"keep_verbatim not verbatim in the dossier: {text[:60]!r}")
    bos = arg.get("best_of_source") or []
    if not 3 <= len(bos) <= 5:
        problems.append(f"{len(bos)} best_of_source ideas (need 3–5)")
    _, target = humanities_targets(content, mode)
    total = sum(m.get("word_budget") or 0 for m in movements
                if isinstance(m.get("word_budget"), (int, float)))
    total += (arg.get("cold_open") or {}).get("word_budget") or 0
    if total and not 0.8 * target <= total <= 1.2 * target:
        problems.append(f"word budgets sum to {total} (target ~{target}, ±20%)")
    return problems


def _pid_key(p: str):
    m = re.match(r"P(\d+)$", p)
    return (0, int(m.group(1))) if m else (1, p)


# --- Stage 2: narration -------------------------------------------------------

DOCUMENTARY_SCRIPT_SYSTEM = (
    "You are a documentary writer and historian producing a spoken script for an educational "
    "documentary film. You work from a research dossier, the way a documentary writer works "
    "from an academic consultant's notes: you own the argument, the structure and every "
    "sentence. You do not paraphrase the dossier; you use what it establishes. "
    "Output ONLY valid JSON."
)

_STAGE2_HEADER = {
    "folio": ("You are writing the narration for a documentary-style film: a calm narrator "
              "over illustrations. The images are chosen later from your words; write only "
              "what is spoken."),
}

_FRAME_RULE = {
    "folio": ("One idea per paragraph. A frame is one paragraph of 60–120 words carrying one "
              "idea; when the idea changes, the frame changes. Target {frame_count_hint}."),
}

_CRAFT = """WHAT YOU ARE MAKING. A structured, academically focused narration of about {target_minutes} minutes — ~{target_words} spoken words, {pct}% of the dossier's {dossier_words} — with:
- One thesis, stated in plain words by the end of the cold open (about thirty seconds, ~75 words) and answered by the ending. The cold open is a concrete image, number or tension from the dossier, never a summary of what is coming.
- Three to six movements, as in the argument map. Each opens with its claim in one sentence, supports it with specific evidence, and ends on a turn: something the viewer now understands that they did not a minute earlier. Signpost with content, not meta-talk: "The second reason lies in the schoolroom" is a signpost; "Now let's turn to" is not.
- {frame_rule} A frame never straddles two movements.
- Concrete evidence: names, dates, places, numbers, what a text or inscription actually says. Prefer the specific instance to the general statement.
- Scholarly precision. Distinguish, in words, what the sources say, what historians infer, and what is disputed: "Aristotle reports…", "the inference is…", "scholars divide over…". Name a debate as a debate, once, where it matters; do not hedge every sentence. Never attribute a view to "scholars" that the dossier does not attribute; never invent a source, a quotation, a date, or a number. A fact not in the dossier is admissible only if it is uncontroversial common knowledge, and you must tag that frame's sources as "common".
- Selection. Aim for ~{target_words} words. Cut what does not serve the thesis; the argument map's cut list is already agreed. Do not restore cut material for completeness, and never pad toward the length — a tighter film beats a padded one.
- Silent correction. The dossier can be wrong. Where you are certain of an error of fact, narrate the correct fact without comment and report it to the orchestrator; never tell the viewer a source erred ("the source says…", "often given as…").

VOICE (heard once, at 2.5 words per second):
- Sentences average 10–14 words; almost none over 20; vary length deliberately.
- Present tense for events and texts where it adds immediacy; past tense for chronology. Active voice.
- No lecture filler ("as we'll see", "now,", "so,", "of course", "it is worth noting", "remember", "in this video"), no rhetorical questions, no sign-offs.
- No "it's not X, it's Y" contrastives in any variant ("not just X, but Y") — say what the thing is. No pet abstractions ("framing", "machinery", "load-bearing").
- Say what you mean: when a literal phrase exists, use it. An analogy that does real work is fine; name it as an analogy. State each point once.
- Quote the dossier only where keep_verbatim allows, inside quotation marks, at most three times; otherwise no run of eight or more words identical to the dossier.
- "We" only for something narrator and viewer do together; "you" only when the viewer must act. Contractions sparingly."""

_SPOKEN_RULES_DOC = """SPOKEN-TEXT RULES (TTS — the narration is read aloud by a voice model):
   - "narration" = ONLY the spoken words. No stage directions, no visual notes.
   - Spell EVERY number and date out in English words. NO digits at all.
     "490 BC" → "four hundred ninety"; "20,000 soldiers" → "twenty thousand soldiers";
     "5th century" → "fifth century"; "1/3" → "one third".
""" + ERA_MARKER_RULES + """
   - No symbols (%, &, $, °, etc.) — write the word. Expand abbreviations ("vs." → "versus", "e.g." → "for example").
   - No Unicode Greek letters — write the English word. Spell out or naturalize anything that would be mis-read; the spoken line must be pure pronounceable English."""

_TITLE_META = """TITLE AND METADATA:
- "title": an original title that states the thesis — not the working title.
- "thesis": the approved thesis, verbatim from the argument map.
- "metadata.key_concepts": 2–4 short phrases naming the essential ideas (thumbnails, video metadata); "metadata.requires_math": false.
- "metadata.frame_count" = the number of frames; frame "number"s run 0, 1, 2, … with no gaps or repeats. Each frame's "word_count" = its narration's words; "metadata.word_count" = their sum; "timing" follows at 2.5 words per second, gapless.
- COUNT ACCURATELY: any count the narration states ("three causes") equals the items it gives.
- Per frame, "movement" = 0 for the cold open, then the argument map's movement number (1…N), never decreasing; "sources" = the dossier paragraph IDs the frame draws on (["P3", "P4"]), plus "common" whenever it states anything that is not in the dossier."""

_OUTPUT = {
    "folio": """OUTPUT (bare JSON, no markdown fences):
{
  "title": "...",
  "thesis": "...",
  "metadata": {"total_duration": "M:SS", "frame_count": N, "word_count": NNN, "target_wps": 2.5,
               "key_concepts": ["...", "..."], "requires_math": false},
  "frames": [
    {"number": 0, "movement": 0, "sources": ["P3", "P4"],
     "timing": {"start": "0:00", "end": "0:31", "start_seconds": 0, "end_seconds": 31},
     "word_count": 78,
     "narration": "...",
     "visual": {"type": "scene", "reference": "one-sentence placeholder"}}
  ]
}""",
}

ARGUMENT_MISSING_BLOCK = """ARGUMENT MAP: none approved yet (no Video-N/argument.json). The two-stage process renders `render_step_prompt.py argument` first, has the map reviewed against the dossier, and writes to it. If you are writing without one, build the map yourself before any prose — thesis, three to six movements each with a claim, evidence (paragraph IDs) and a turn, and a cut list — and write to it."""


def format_argument_block(arg: Dict) -> str:
    return ("APPROVED ARGUMENT MAP (write to this; do not restructure it — its thesis, "
            "movements, their order, its evidence and its cut list were reviewed against the "
            "dossier):\n" + json.dumps(arg, indent=1, ensure_ascii=False))


def humanities_frame_count_hint(mode: str, minutes: float, target_words: int) -> str:
    return (f"about {max(8, target_words // 110)}-{max(12, target_words // 75)} frames for a "
            f"~{int(round(minutes))} minute video")


def build_humanities_script_prompt(mode: str, segment_meta: Dict, content: str,
                                   argument_block: str) -> str:
    """Stage-2 user prompt for folio."""
    minutes, target_words = humanities_targets(content, mode)
    words = len(content.split())
    _, frame_count_hint = compute_duration_and_frame_hints(
        "", content, humanities_mode=mode)
    frame_rule = _FRAME_RULE[mode].format(frame_count_hint=frame_count_hint)
    craft = _CRAFT.format(target_minutes=int(round(minutes)), target_words=f"{target_words:,}",
                          pct=f"{100 * target_words / max(words, 1):.0f}",
                          dossier_words=f"{words:,}", frame_rule=frame_rule)
    parts = [_STAGE2_HEADER[mode], build_dossier_block(segment_meta, content), "---",
             argument_block, "---", craft, _SPOKEN_RULES_DOC, _TITLE_META, _OUTPUT[mode]]
    return "\n\n".join(parts) + "\n"


MATH_SCRIPT_GENERATION_PROMPT = """You are writing a narration script for an educational math video.

TEACHING STYLE GUIDE:
{style_guide}

{source_block}

---

Create a frame-by-frame narration script as a JSON object.

REQUIREMENTS:

{planning_block}

1. **Timing:**
   - Target 2.5 words per second
   - Total duration should match the target ({duration_hint})
   - Each frame: 15-60 seconds typically

2. **Frame Count:**
   - Target {frame_count_hint} frames
   - Frame 0 = Title/Hook
   - Last Frame = Synthesis/Closing

3. **Visual Reference (animation direction — CRITICAL for math videos):**
   - Each frame includes a "visual" object with type and reference
   - Frame 0 should have type "title"
   - Other frames should have type "conceptual"
   - The reference field directs a Manim animation system. The narration is PRIMARY
     (complete and self-contained), but good visual direction dramatically improves
     the animation quality.
   - **Each frame also includes a "frame_class" field** — you already know what kind
     of frame you are writing, so declare it (this routes the frame downstream without
     a separate classification pass):
     - "math": the frame works through calculations, derivations, equation solving,
       or formula manipulation — anything with verifiable symbolic steps (these use
       a Layout prefix, below, and get SymPy verification)
     - "visual": explanatory frames with NO derivation to verify — intuition and
       motivation, geometric pictures, concept maps, the big-picture structure of a
       method, comparing ideas rather than computations. Frame 0 (title) is always
       "visual".
     Most frames in a math video are "math" — use "visual" only when the frame
     genuinely teaches through a picture or diagram, not through worked steps.

   **For math frames**, pick the best-fit Layout (use a Layout prefix):
   - **Layout A** (Full Whiteboard): Pure algebraic derivation, no graphs. Steps fill full width.
     Use for: derivations, simplifications, solving equations, proofs.
   - **Layout B** (Split Screen): Graph on LEFT, algebraic steps on RIGHT.
     Use for: plotting functions, tangent lines, finding extrema, area under curves, any frame
     that involves a graph or coordinate plane.
   - **Layout C** (Steps Above + Visual Below): Algebraic steps on top, number line / sign chart /
     flowchart / process diagram pinned at the bottom.
     Use for: sign analysis, interval testing, first/second derivative test conclusions,
     decision procedures, step-by-step methods.
   - **Layout D** (Two-Panel Comparison): Two side-by-side columns with vertical divider.
     Use for: comparing two methods, left-hand vs right-hand limits, before vs after.
   - **Layout E** (Three-Panel Comparison): Three equal columns.
     Use for: comparing three cases or approaches.

   **Format the math reference field as:**
   "Layout X: [specific description of what to show]"

   **Good math examples:**
   - "Layout A: Derive f'(x) using power rule. Steps: write f(x) = 3x^4 - 2x^2 + 1, apply
     power rule term by term, simplify to f'(x) = 12x^3 - 4x."
   - "Layout B: Graph f(x) = x^3 - 3x on [-3, 3] in left panel, mark local max at (-1, 2)
     and local min at (1, -2). Right panel: derive f'(x) = 3x^2 - 3, solve 3x^2 - 3 = 0,
     get x = ±1, evaluate f at each."
   - "Layout C: Steps above — set f'(x) = 0, solve for critical points x = -1 and x = 2.
     Bottom zone: number line from -3 to 3, mark critical points, test signs in each interval,
     label + / - / + regions."
   - "Layout D: Compare left-hand limit (x → 2⁻) in left panel vs right-hand limit (x → 2⁺)
     in right panel. Each panel shows its own substitution steps."

   **Bad math examples:**
   - "Graph of f(x) with tangent line" (too vague — which function? what domain? what else on screen?)
   - "Steps showing derivative calculation" (no layout chosen, no specifics)

   **For visual frames** (intuition, motivation, concept maps, big-picture structure),
   write a free-form visual description. Do NOT use a Layout prefix. Describe:
   - What elements to show (boxes, nodes, arrows, labels, graphs, regions)
   - Spatial arrangement (left-to-right flow, radial, hierarchical, etc.)
   - Reveal order (what appears first, what connects to what)
   - Key relationships and labels
   - Colors or emphasis for important elements

   The Manim animator will design the layout from your description — you describe
   WHAT to show, not how to arrange it in code.

   **Good visual examples:**
   - "Concept map of the derivative: center node 'f prime of a', three branches
     appearing one at a time — 'slope of the tangent line' (small curve-with-tangent
     sketch), 'instantaneous rate of change' (speedometer icon), 'limit of difference
     quotients' (the formula). Arrow from center to each branch as the narration
     introduces it."
   - "Roadmap of solving an optimization problem: four boxes left to right —
     'Translate the problem' → 'Write the objective function' → 'Reduce to one
     variable' → 'Apply the closed interval method'. Each box appears as the
     narration reaches it; highlight the current box, dim completed ones."
   - "Secant-to-tangent intuition: graph y = x^2, fix point P at x = 1, second point Q
     slides from x = 3 toward P while the secant line through them rotates into the
     tangent line. Show the secant slope label updating as Q moves; end with the
     tangent line highlighted in yellow."

   **Bad visual examples:**
   - "Diagram of the derivative concept" (too vague — no elements, no arrangement)
   - "Show why limits matter" (no concrete visual content at all)

4. **Script Style:** follow the TEACHING STYLE GUIDE above.

5. **Teaching Flow:** frames follow the Hook → Build → Deepen → Apply flow you planned
   from the source content.

6. **Worked Examples (problem-solving content):**
   - Each worked example is ONE self-contained frame — statement, diagram, solution,
     and interpretation together. NEVER split one example across frames: every frame's
     animation is generated independently, so a diagram referenced across frames will
     NOT stay consistent. All visual continuity must live inside the frame.
   - A worked-example frame may run 2-4 minutes (explicit exception to the usual
     15-60 second frame guidance). Teach at lecture pace, not textbook pace: book
     sources state solutions compactly — EXPAND them: motivate the setup, restate the
     givens, say why each move is made, and interpret the result. Never compress an
     example to fit the duration target; with several examples the video should simply
     run longer. The duration target is a completeness floor, not a ceiling.
   - Inside the frame, state the problem first, then walk straight through the
     solution. Do NOT tell the student to pause and try it themselves first — go
     directly from the problem statement into solving it.
   - **Diagram-first, label-progressively (all inside the frame).** If the problem has
     a geometric picture (vectors, positions, angles, regions), open with the COMPLETE
     diagram, every given quantity labeled (axes, points, vectors, angles, distances),
     while the narration states the problem. As the solution proceeds, each computed
     quantity's label is added to the diagram at the moment the narration computes it
     — never all at once, and never solving in pure algebra divorced from the picture.
   - The layout may transform mid-frame when useful: e.g. the diagram opens large and
     centered for the statement, then shrinks and slides into the left panel as
     derivation steps begin on the right. Describe such transitions explicitly in the
     visual reference ("after the pause, scale the diagram into the left panel and
     bring in steps on the right").

7. **Narration Content (CRITICAL):**
   - The "narration" field contains ONLY the spoken text — complete and self-standing
   - The narration must cover ALL the math content. Do NOT rely on the visual to "show"
     steps that the narrator doesn't explain verbally
   - Every calculation, substitution, and result must be spoken in the narration
   - The narration should end naturally with the final teaching point
   - Do NOT include any meta-commentary or "that's all for today" style endings
""" + MATH_NARRATION_TTS_RULES + """

OUTPUT FORMAT (respond with ONLY the JSON, no markdown code blocks):
{{
  "title": "Video Title Here",
  "metadata": {{
    "total_duration": "X:XX",
    "frame_count": N,
    "word_count": NNN,
    "target_wps": 2.5,
    "key_concepts": ["essential concept 1", "essential concept 2", "essential concept 3"],
    "requires_math": true
  }},
  "frames": [
    {{
      "number": 0,
      "timing": {{
        "start": "0:00",
        "end": "0:20",
        "start_seconds": 0,
        "end_seconds": 20
      }},
      "word_count": 50,
      "narration": "Opening narration text here. This must be complete — every concept spoken aloud.",
      "frame_class": "visual",
      "visual": {{
        "type": "title",
        "reference": "Title card with key concept preview"
      }}
    }},
    {{
      "number": 1,
      "timing": {{
        "start": "0:20",
        "end": "0:50",
        "start_seconds": 20,
        "end_seconds": 50
      }},
      "word_count": 75,
      "narration": "First teaching point narration here...",
      "frame_class": "visual",
      "visual": {{
        "type": "conceptual",
        "reference": "Secant-to-tangent intuition: graph y = x^2, fix point P at x = 1, second point Q slides toward P while the secant line rotates into the tangent line. Label the secant slope updating as Q moves; end with the tangent line highlighted."
      }}
    }},
    {{
      "number": 2,
      "timing": {{
        "start": "0:50",
        "end": "1:25",
        "start_seconds": 50,
        "end_seconds": 85
      }},
      "word_count": 88,
      "narration": "Second teaching point narration here...",
      "frame_class": "math",
      "visual": {{
        "type": "conceptual",
        "reference": "Layout B: Graph f(x) = x^2 on [-2, 3] in left panel, draw tangent line at x=1 with slope labeled '2'. Right panel: apply power rule to get f'(x) = 2x, evaluate f'(1) = 2, conclude slope = 2."
      }}
    }}
  ]
}}

Ensure word counts match timing (seconds × 2.5).
"""


TECHNICAL_SCRIPT_GENERATION_PROMPT = """You are writing a narration script for an educational video on a technical subject (finance, economics, computer science, engineering, etc.).

TEACHING STYLE GUIDE:
{style_guide}

{source_block}

---

Create a frame-by-frame narration script as a JSON object.

REQUIREMENTS:

{planning_block}

1. **Timing:**
   - Target 2.5 words per second
   - Total duration should match the target ({duration_hint})
   - Each frame: 15-60 seconds typically

2. **Frame Count:**
   - Target {frame_count_hint} frames
   - Frame 0 = Title/Hook
   - Last Frame = Synthesis/Closing

3. **Visual Reference (animation direction — CRITICAL for technical videos):**
   - Each frame includes a "visual" object with type and reference
   - Frame 0 should have type "title"
   - Other frames should have type "conceptual"
   - The reference field directs a Manim animation system. The narration is PRIMARY
     (complete and self-contained), but good visual direction dramatically improves
     the animation quality.
   - **Each frame also includes a "frame_class" field** — you already know what kind
     of frame you are writing, so declare it (this routes the frame downstream without
     a separate classification pass):
     - "math": the frame works through calculations, derivations, or formula
       manipulation (these use a Layout prefix, below)
     - "code": the frame walks through a code listing or traces program behavior
       line-by-line (put the actual code in a fenced ```python block in the visual)
     - "visual": everything else — processes, structures, networks, diagrams,
       timelines, conceptual explanations
       NOTE: EVERY frame's narration is spoken EXACTLY as you write it — there is no
       downstream TTS rewrite for any class (verify_math checks the math of "math"
       frames and extracts their on-screen steps; it never touches spoken text). So
       all narration must already obey every TTS rule below. A "visual" frame's
       numbers and results are not checked by anything downstream — if a frame
       states calculations or quantities it should be "math" (or "code"), not
       "visual", so its values get the SymPy verification pass.

   **For math/calculation frames**, use a Layout prefix:
   - **Layout A** (Full Whiteboard): Pure derivation or step-by-step procedure, NO diagram.
     Use for: derivations, simplifications, solving equations, proofs, calculations.
   - **Layout B** (Split Screen): Diagram or graph on LEFT, steps on RIGHT.
     Use for: any math frame that also draws something — a free-body diagram, vectors,
     a geometric figure, a timeline, a plot — beside the calculation that uses it.
     A frame with a picture beside its steps is Layout B, not Layout A.
   - **Layout D** (Two-Panel Comparison): Two side-by-side columns.
     Use for: comparing two methods, before vs after, pros vs cons, two scenarios.
   - **Layout E** (Three-Panel Comparison): Three equal columns.
     Use for: comparing three cases or approaches.

   Format: "Layout X: [specific description of what to derive/calculate]"

   **For non-math frames** (processes, structures, networks, diagrams, timelines),
   write a free-form visual description. Do NOT use a Layout prefix. Describe:
   - What elements to show (boxes, nodes, arrows, labels, icons)
   - Spatial arrangement (left-to-right flow, radial, hierarchical, etc.)
   - Reveal order (what appears first, what connects to what)
   - Key relationships and labels
   - Colors or emphasis for important elements

   The Manim animator will design the layout from your description — you just describe
   WHAT to show, not how to arrange it in code.

   **Good examples:**
   - "Layout A: Calculate NPV of cash flows. Steps: write formula NPV = sum of CF_t/(1+r)^t,
     substitute CF_1=100, CF_2=150, CF_3=200, r=0.10, evaluate each term, sum to get NPV=377.41."
   - "Build a diagram of blockchain transaction validation. Center: 'Transaction' node.
     Surround with 'Node A', 'Node B', 'Node C' validator nodes. Animate arrows from
     Transaction to each node (broadcast), then green checkmarks appearing on each (validation),
     then arrow to 'Block' (confirmation)."
   - "Show the securitization pipeline: 'Mortgages' → 'SPV' → 'Tranches (AAA/BBB/Equity)' →
     'Investors'. Each box appears left-to-right with connecting arrows. Label each arrow
     with the flow (pooling, structuring, selling). Highlight the SPV as the key intermediary."
   - "Network of market participants. Nodes: 'Buyer', 'Seller', 'Market Maker', 'Exchange'.
     Connections: Buyer→Exchange (limit order), Seller→Exchange (limit order),
     Market Maker↔Exchange (two-way quotes). Highlight bid-ask spread between Market Maker's quotes."
   - "Timeline showing Bitcoin's key milestones: 2008 whitepaper, 2009 genesis block,
     2010 first transaction, 2013 $1000, 2017 futures launch, 2021 El Salvador adoption.
     Each milestone appears progressively with a brief label."

   **Bad examples:**
   - "Diagram of NPV calculation" (too vague — no specifics)
   - "Network showing blockchain" (no node/edge details)

4. **Script Style & spoken-text (TTS) rules:** follow the TEACHING STYLE GUIDE above.
   For the spoken `narration` field, additionally:
""" + TECHNICAL_NARRATION_TTS_RULES + """

5. **Teaching Flow:** frames follow the Hook → Build → Deepen → Apply flow you planned
   from the source content.

6. **Worked Examples (problem-solving content):**
   - Each worked example is ONE self-contained frame — statement, diagram, solution,
     and interpretation together. NEVER split one example across frames: every frame's
     animation is generated independently, so a diagram referenced across frames will
     NOT stay consistent. All visual continuity must live inside the frame.
   - A worked-example frame may run 2-4 minutes (explicit exception to the usual
     15-60 second frame guidance). Teach at lecture pace, not textbook pace: book
     sources state solutions compactly — EXPAND them: motivate the setup, restate the
     givens, say why each move is made, and interpret the result. Never compress an
     example to fit the duration target; with several examples the video should simply
     run longer. The duration target is a completeness floor, not a ceiling.
   - Inside the frame, state the problem first, then walk straight through the
     solution. Do NOT tell the student to pause and try it themselves first — go
     directly from the problem statement into solving it.
   - **Diagram-first, label-progressively (all inside the frame).** If the problem has
     a geometric or structural picture (vectors, network, timeline, data layout), open
     with the COMPLETE diagram, every given quantity labeled, while the narration
     states the problem. As the solution proceeds, each computed quantity's label is
     added to the diagram at the moment the narration computes it — never all at once,
     and never solving in pure algebra divorced from the picture.
   - The layout may transform mid-frame when useful: e.g. the diagram opens large and
     centered for the statement, then shrinks and slides into the left panel as
     derivation steps begin on the right. Describe such transitions explicitly in the
     visual reference.

7. **Narration Content (CRITICAL):**
   - The "narration" field contains ONLY the spoken text — complete and self-standing
   - The narration must cover ALL content. Do NOT rely on the visual to "show"
     steps that the narrator doesn't explain verbally
   - Every calculation, definition, and relationship must be spoken in the narration
   - The narration should end naturally with the final teaching point
   - Do NOT include any meta-commentary or "that's all for today" style endings

OUTPUT FORMAT (respond with ONLY the JSON, no markdown code blocks):
{{
  "title": "Video Title Here",
  "metadata": {{
    "total_duration": "X:XX",
    "frame_count": N,
    "word_count": NNN,
    "target_wps": 2.5,
    "key_concepts": ["essential concept 1", "essential concept 2", "essential concept 3"],
    "requires_math": true
  }},
  "frames": [
    {{
      "number": 0,
      "timing": {{
        "start": "0:00",
        "end": "0:20",
        "start_seconds": 0,
        "end_seconds": 20
      }},
      "word_count": 50,
      "narration": "Opening narration text here. This must be complete — every concept spoken aloud.",
      "frame_class": "visual",
      "visual": {{
        "type": "title",
        "reference": "Title card with key concept preview"
      }}
    }},
    {{
      "number": 1,
      "timing": {{
        "start": "0:20",
        "end": "0:50",
        "start_seconds": 20,
        "end_seconds": 50
      }},
      "word_count": 75,
      "narration": "First teaching point narration here...",
      "frame_class": "math",
      "visual": {{
        "type": "conceptual",
        "reference": "Layout A: Derive the present value formula. Steps: write PV = CF/(1+r)^t, substitute CF=1000, r=0.08, t=5, evaluate to get PV=680.58."
      }}
    }},
    {{
      "number": 2,
      "timing": {{
        "start": "0:50",
        "end": "1:25",
        "start_seconds": 50,
        "end_seconds": 85
      }},
      "word_count": 88,
      "narration": "Second teaching point narration here...",
      "frame_class": "visual",
      "visual": {{
        "type": "conceptual",
        "reference": "Show the bond cash flow structure: 'Investor' box on left, 'Bond Issuer' box on right. Arrow from Investor to Issuer labeled 'Purchase Price ($950)'. Then multiple arrows from Issuer back to Investor labeled 'Coupon $40' at years 1-5. Final large arrow labeled 'Principal $1000' at maturity. Highlight that total return exceeds purchase price."
      }}
    }}
  ]
}}

Ensure word counts match timing (seconds × 2.5).
"""


# Pedagogical planning instructions — folded in from the retired brief step.
# The script writer plans the teaching flow from the SOURCE CONTENT directly
# (in extended thinking), instead of a separate Claude call producing a
# video_brief.md intermediate that lossily stood in for the source.
PLANNING_BLOCK = """0. **Plan the video before writing (work this out in your thinking, not in the output):**
   - Teaching flow: what counterintuitive insight, surprising fact, or relatable scenario
     opens the video (Hook)? Which foundational concepts must be established, in what order
     (Build)? What nuance, edge cases, or common confusions need addressing (Deepen)?
     What practical example or broader significance closes it (Apply/Synthesize)?
   - Identify 2-3 misconceptions learners typically have about this topic and address them
     in the narration where they naturally arise.
   - Cover ALL the key ideas, examples, and calculations from the SOURCE CONTENT — do not
     summarize away substance. The source is the ground truth for what this video teaches.
   - Generate an ORIGINAL "title" that captures the video's central argument or thesis —
     do NOT reuse the working title verbatim.
   - In "metadata", set "key_concepts" to 2-4 short phrases naming the essential ideas
     (used for video metadata), and "requires_math" to true only if the
     video involves calculations, formulas, derivations, or quantitative analysis.
   - COUNT ACCURATELY. Whenever the narration states a number of things ("three causes",
     "four new nouns", "two cases"), it MUST equal the number you actually present and show
     on screen — never invent, pad, round, or repeat an item to hit a count; count the real
     items in the source and say that number. And "metadata.frame_count" MUST equal the
     number of objects in "frames", whose "number" values run 0, 1, 2, … with no gaps or
     repeats."""


def load_segments(pipeline_path: Path) -> Dict:
    """Load segments.json (written by segment_concepts.py). Empty dict if absent."""
    segments_path = pipeline_path / "segments.json"
    if not segments_path.exists():
        return {}
    with open(segments_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_video_source(video_dir: Path, segments: Dict) -> tuple:
    """
    Load the source material for one video: segment metadata + cleaned content.

    Returns (segment_meta, content). content comes from Video-N/content.txt,
    falling back to the segment's content field (and writing content.txt for
    downstream consumers, matching the old brief-step behaviour).
    """
    try:
        video_num = int(video_dir.name.replace("Video-", ""))
    except ValueError:
        video_num = None

    segment_meta = {}
    for video in segments.get("videos", []):
        if video.get("number") == video_num:
            segment_meta = video
            break

    content = ""
    content_path = video_dir / "content.txt"
    if content_path.exists():
        content = content_path.read_text(encoding="utf-8")
    elif segment_meta.get("content"):
        content = segment_meta["content"]
        with open(content_path, "w", encoding="utf-8") as f:
            f.write(content)

    return segment_meta, content


def build_source_block(segment_meta: Dict, content: str) -> str:
    """Format segment metadata + source content for prompt injection."""
    takeaways = segment_meta.get("key_takeaways", [])
    takeaways_str = "\n".join(f"- {t}" for t in takeaways) if isinstance(takeaways, list) else str(takeaways)

    examples = segment_meta.get("examples", [])
    examples_str = "\n".join(f"- {e}" for e in examples) if isinstance(examples, list) else str(examples)

    return f"""SOURCE MATERIAL:

Working Title: {segment_meta.get('title', 'Untitled')}
Core Concept: {segment_meta.get('core_concept', '')}
Target Duration: {segment_meta.get('duration_estimate', '6-8 minutes')}

KEY TAKEAWAYS:
{takeaways_str or '- (none specified)'}

EXAMPLES TO INCLUDE:
{examples_str or '- (none specified)'}

SOURCE CONTENT (ground truth — the lecture material this video must teach):
{content}"""


# The curated style block injected into every script prompt. This is the
# SINGLE source of truth — templates/teaching_style_guide.md is human-facing
# documentation only and is NOT read by the pipeline (an earlier version
# pretended to load it, then discarded the contents).
STYLE_KEY_POINTS = """Key Style Points:
- Concise conversational: every word earns its place; active voice; state each concept ONCE
- No rhetorical questions, no verbal cushioning ("So what this means is...")
- NO pet abstractions: "framing", "machinery", and "load-bearing" are overused across this \
channel — do not use them (unless literal); name the concrete thing instead
- NO "it's not X, it's Y" contrastives (any variant — "not just X, but Y", "X isn't Y; it's Z") \
— say what the thing IS directly
- Contractions OK but sparingly; 2.5 words per second pacing
- "We" only when doing something together; "you" only when action required

TTS PRONUNCIATION (the narration is read aloud by a voice model):
- No Unicode Greek letters in narration — write the English word ("pi", "theta", "delta"):
  "sine of pi over four", never "sine of π/4". The `visual` field / on-screen text keeps
  symbols and digits — it is not spoken."""


def load_style_guide() -> str:
    """Return the curated style block for prompt injection."""
    return STYLE_KEY_POINTS


def compute_duration_and_frame_hints(duration_estimate: str, content: str = "", humanities_mode: str = "") -> tuple:
    """Compute duration/frame-count hints from the segment's duration estimate
    (e.g. "15 minutes", "6-8 minutes"), falling back to the source content's
    word count at narration pace.

    Returns (duration_hint, frame_count_hint) as strings for prompt injection.

    ``humanities_mode`` (folio) ignores the segment's estimate: the narration is a
    ~75% SELECTION from the dossier, so minutes = clamp(dossier_words × 0.75 / 150,
    15, 25) (see humanities_targets). Sizing to the source length forced a 1:1
    rewrite of the lecture.
    """
    if humanities_mode:
        minutes, target_words = humanities_targets(content, humanities_mode)
        return (f"approximately {int(round(minutes))} minutes (~{target_words:,} words)",
                humanities_frame_count_hint(humanities_mode, minutes, target_words))
    match = re.search(r'(\d+)(?:\s*-\s*(\d+))?\s*minutes?', duration_estimate or "", re.IGNORECASE)
    if match:
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        avg_mins = (low + high) / 2
    elif content:
        # Source word count at 2.5 words/sec, produced video typically condenses
        avg_mins = max(5, min(25, len(content.split()) / 2.5 / 60))
    else:
        avg_mins = 7  # default

    if avg_mins < 8:
        duration_hint = "typically 5-7 minutes"
        frame_count_hint = "8-12 frames for a 6-8 minute video"
    elif avg_mins <= 15:
        duration_hint = f"approximately {int(avg_mins)} minutes"
        frame_count_hint = f"15-20 frames for a ~{int(avg_mins)} minute video"
    else:
        duration_hint = f"approximately {int(avg_mins)} minutes"
        frame_count_hint = f"20-30 frames for a ~{int(avg_mins)} minute video"

    return duration_hint, frame_count_hint


def main():
    sys.exit(
        "The script step is authored by a Claude Code subagent.\n"
        "Render its prompt: venv/bin/python scripts/render_step_prompt.py script "
        "--video-dir pipeline/<L>/Video-N --mode <math|technical|folio>\n"
        "then save the subagent's JSON to script.json and regenerate script.md "
        "(script_parser.save_script) — see .claude/skills/run-pipeline/SKILL.md."
    )


if __name__ == "__main__":
    main()
