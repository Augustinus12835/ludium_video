#!/usr/bin/env python3
"""
Script generation prompts (math and technical modes).

script.json (structured data) + script.md (human-readable) are authored by a
Claude Code subagent: render the mode's prompt with
`render_step_prompt.py script --video-dir DIR --mode <math|technical>`, save
the subagent's JSON to script.json, and regenerate script.md via
script_parser.save_script. This module holds the prompt templates and the
source/duration-hint builders that render_step_prompt.py imports.

Pipeline position:
  segments.json + Video-N/content.txt
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
    TECHNICAL_NARRATION_TTS_RULES,
    MATH_NARRATION_TTS_RULES,
)


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

8. **Cross-Frame Math Context:**
   - Each frame includes a "math_context" field: a 1-3 sentence summary of mathematical
     results/state carried forward from ALL prior frames to this one.
   - Frame 0: omit or empty string (no prior context).
   - Focus on results and key expressions, not narration recap.
   - Example: "We defined f(x) = x³ − 3x + 1, found f'(x) = 3x² − 3, and set f'(x) = 0
     to get critical points at x = ±1."

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
      }},
      "math_context": "We introduced f(x) = x^2 and motivated the concept of instantaneous rate of change."
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
      }},
      "math_context": "We motivated the tangent line as the limit of secant lines through P at x = 1."
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
   - **Layout A** (Full Whiteboard): Pure derivation or step-by-step procedure.
     Use for: derivations, simplifications, solving equations, proofs, calculations.
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

8. **Cross-Frame Context:**
   - Each frame includes a "math_context" field: a 1-3 sentence summary of
     results/state carried forward from ALL prior frames to this one.
   - Frame 0: omit or empty string (no prior context).
   - Focus on results and key definitions, not narration recap.
   - Example: "We defined NPV as the sum of discounted cash flows, established r=10%,
     and computed NPV=377.41 for the base case."

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
      }},
      "math_context": "We introduced the concept of time value of money."
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
      }},
      "math_context": "We established PV = CF/(1+r)^t and computed PV=680.58 for a single cash flow at r=8%."
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


def compute_duration_and_frame_hints(duration_estimate: str, content: str = "") -> tuple:
    """Compute duration/frame-count hints from the segment's duration estimate
    (e.g. "15 minutes", "6-8 minutes"), falling back to the source content's
    word count at narration pace.

    Returns (duration_hint, frame_count_hint) as strings for prompt injection.
    """
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
        "--video-dir pipeline/<L>/Video-N --mode <math|technical>\n"
        "then save the subagent's JSON to script.json and regenerate script.md "
        "(script_parser.save_script) — see .claude/skills/run-pipeline/SKILL.md."
    )


if __name__ == "__main__":
    main()
