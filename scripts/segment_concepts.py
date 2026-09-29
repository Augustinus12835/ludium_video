#!/usr/bin/env python3
"""
Concept Segmentation - Divide cleaned content into videos.
Each video gets its content directly - no references needed.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.generate_scripts import DOSSIER_SELECTION_RATIO  # noqa: E402


SEGMENTATION_PROMPT = """Divide this educational content into concept videos.

Each video should:
- Focus on ONE core concept or closely related set of concepts
- Contain enough material for a 5-8 minute produced video
- Stand alone (viewer shouldn't need to watch other videos first)

CONTENT:
{content}

---

Create the video segments. For each video:

1. **title**: Clear, searchable YouTube-style title
2. **core_concept**: One sentence - what will viewer understand after watching?
3. **start_anchor**: The EXACT first 8-12 words of the first paragraph belonging to this
   video, copied VERBATIM from the CONTENT above — character-for-character, including
   punctuation. The content is split mechanically at these anchors (each video runs from
   its anchor to the next video's anchor), so a misquoted anchor breaks the pipeline.
   Do NOT paraphrase, do NOT fix typos, do NOT re-emit the content itself.
4. **key_takeaways**: 2-3 bullet points
5. **examples**: List any examples/calculations that should be included
6. **duration_estimate**: Estimated video length based on content density

Rules:
- Quality over quantity (4 good videos > 8 thin videos)
- Don't stretch content to fill videos
- Group naturally related concepts
- Videos must be in source order: each start_anchor must occur AFTER the previous
  video's start_anchor in the content
- Anchors must sit at natural paragraph boundaries (the start of a paragraph)
- Video 1's anchor should normally be the very beginning of the content — everything
  before the first anchor is dropped

OUTPUT as valid JSON:
{{
  "video_count": N,
  "videos": [
    {{
      "number": 1,
      "title": "...",
      "core_concept": "...",
      "start_anchor": "exact first words of this video's first paragraph",
      "key_takeaways": ["...", "..."],
      "examples": ["...", "..."],
      "duration_estimate": "X minutes"
    }}
  ],
  "notes": "... observations about segmentation decisions ..."
}}"""


# Folio (humanities) segmentation sizing: a lecture or book becomes a few LONG
# self-contained themed chapters, sized by how much *cleaned content* remains (NOT raw
# lecture length — filler-heavy lectures clean down small and stay one video). Aim
# ~20 minutes per video; running a bit over is fine, short videos are the failure
# mode — prefer a 20-min + 25-min pair over three even 15-min parts.
#
# Humanities scripts do not re-voice their content 1:1: the script is written from an
# argument map and narrates ~75% of its dossier (generate_scripts.humanities_targets).
# A ~20-min video therefore needs a ~4,000-word dossier, so every part-sizing figure
# below runs at DOSSIER_WORDS_PER_MINUTE (200 cleaned words per finished minute):
# ~4,000 target, 3,000–5,000 band.
WORDS_PER_MINUTE = 150.0   # narration pace (2.5 words/sec)
DOSSIER_WORDS_PER_MINUTE = WORDS_PER_MINUTE / DOSSIER_SELECTION_RATIO   # = 200
TARGET_MINUTES = 20.0      # ideal planned length per video
MAX_AVG_MINUTES = 25.0     # split rather than let the planned average exceed this
MIN_AVG_MINUTES = 15.0     # never split so far that the planned average drops below this


def video_count_range(content: str) -> tuple:
    """(n_min, n_target, n_max) video counts for a folio-mode lecture.

    n_min = fewest parts keeping the planned average ≤ MAX_AVG_MINUTES;
    n_max = most parts keeping it ≥ MIN_AVG_MINUTES; n_target = the count whose
    average lands nearest TARGET_MINUTES, preferring fewer, longer parts on the
    fence. The segmentation prompt receives the whole range so the model may pick
    within it at genuine thematic seams (parts may be uneven).
    """
    import math
    words = len((content or "").split())
    minutes = words / DOSSIER_WORDS_PER_MINUTE
    n_min = max(1, math.ceil(minutes / MAX_AVG_MINUTES))
    n_max = max(n_min, int(minutes // MIN_AVG_MINUTES))
    ideal = minutes / TARGET_MINUTES
    n_target = int(ideal) + (1 if ideal - int(ideal) > 0.75 else 0)
    return n_min, max(n_min, min(max(n_target, 1), n_max)), n_max


def target_video_count(content: str) -> int:
    """Default number of folio-mode videos for a lecture (see video_count_range)."""
    return video_count_range(content)[1]


FOLIO_SEGMENTATION_PROMPT = """Divide this lecture into {target} self-contained videos for an illustrated documentary series.

These are NOT short segments. Each video is a LONG-FORM documentary chapter built on ONE self-contained THEME or topic, with its own beginning, middle, and end. A viewer should be able to watch any one part as a coherent standalone story.

DURATION POLICY — this content is ~{total_words} words ≈ {total_minutes} minutes of finished video (~{wpm} words of this content per finished minute: each video's script narrates about three-quarters of its part, so a part is a research dossier, not a transcript):
- Aim each part at ROUGHLY {target_minutes} MINUTES (~{target_words} words of this content).
- Running long is fine (up to ~{max_minutes} minutes / ~{max_words} words). Running SHORT is
  the failure mode: never plan a part under ~{min_minutes} minutes (~{min_words} words) —
  unless the total content itself forces it (content just over one video's ceiling split in
  two); then split as evenly as the nearest genuine seam allows.
- Parts need NOT be equal — split where the thematic seams genuinely are. A 20-minute +
  25-minute pair at a real seam beats three even 15-minute parts at forced ones.

CONTENT:
{content}

---

{count_rule} Split only where the topic genuinely shifts — major thematic seams, never mid-thread. For each video:

1. **title**: Clear, evocative title for this themed chapter.
2. **core_concept**: One sentence — the theme this part covers.
3. **start_anchor**: The EXACT first 8-12 words of the first paragraph belonging to this
   video, copied VERBATIM from the CONTENT above — character-for-character, including
   punctuation. The content is split mechanically at these anchors (each video runs from
   its anchor to the next video's anchor), so a misquoted anchor breaks the pipeline.
   Do NOT paraphrase, do NOT fix typos, do NOT re-emit the content itself.
4. **key_takeaways**: 2-3 bullet points.
5. **examples**: List any examples/illustrations that should be included.
6. **duration_estimate**: Estimated length based on content density.

CRITICAL RULES:
- Split ONLY at major thematic boundaries; never sever a tightly-coupled thread (a concept
  and its payoff stay in the same part). Each part is a chapter, not a clip.
- Every part must respect the DURATION POLICY above — estimate each part's share of the
  content (its anchor to the next anchor) in words and keep it inside the
  ~{min_words}-{max_words}-word band (where the total allows), aiming near ~{target_words}.
  Uneven parts at real seams are better than even parts at forced ones.
- The split is mechanical at your anchors, so every paragraph lands in EXACTLY ONE video —
  choose boundaries that form a clean narrative sequence where part N+1 picks up where part N ended.
- Anchors sit at natural paragraph boundaries, in source order; each start_anchor occurs AFTER
  the previous one. Video 1's anchor is the very beginning of the content (nothing is dropped).

OUTPUT as valid JSON:
{{
  "video_count": <the number of parts you chose>,
  "videos": [
    {{
      "number": 1,
      "title": "...",
      "core_concept": "...",
      "start_anchor": "exact first words of this video's first paragraph",
      "key_takeaways": ["...", "..."],
      "examples": ["...", "..."],
      "duration_estimate": "X minutes"
    }}
  ],
  "notes": "... why you placed the thematic boundaries where you did ..."
}}"""


def build_folio_segment_prompt(content: str, forced_target: int = 0) -> str:
    """Render the folio segmentation prompt: duration policy plus the
    content-derived part-count range. forced_target overrides the default count
    (the range widens to include it) — the model may still pick within the range
    when the thematic structure genuinely favors it."""
    n_min, n_target, n_max = video_count_range(content)
    if forced_target:
        n_target = forced_target
        n_min, n_max = min(n_min, forced_target), max(n_max, forced_target)
    if n_min == n_max:
        count_rule = (
            f"Split the lecture into EXACTLY {n_target} parts — "
            "the content length fixes the count.")
    else:
        count_rule = (
            f"Split the lecture into {n_target} parts by default; any count from "
            f"{n_min} to {n_max} is acceptable when the thematic structure genuinely "
            "favors it — prefer FEWER, longer parts when in doubt.")
    words = len(content.split())
    return FOLIO_SEGMENTATION_PROMPT.format(
        content=content,
        target=n_target,
        count_rule=count_rule,
        total_words=f"{words:,}",
        total_minutes=int(round(words / DOSSIER_WORDS_PER_MINUTE)),
        wpm=int(DOSSIER_WORDS_PER_MINUTE),
        target_minutes=int(TARGET_MINUTES),
        target_words=f"{int(TARGET_MINUTES * DOSSIER_WORDS_PER_MINUTE):,}",
        max_minutes=int(MAX_AVG_MINUTES),
        max_words=f"{int(MAX_AVG_MINUTES * DOSSIER_WORDS_PER_MINUTE):,}",
        min_minutes=int(MIN_AVG_MINUTES),
        min_words=f"{int(MIN_AVG_MINUTES * DOSSIER_WORDS_PER_MINUTE):,}",
    )


def _normalize_for_match(text: str) -> str:
    """Normalize quotes/dashes that models commonly 'fix' when quoting."""
    table = str.maketrans({
        "‘": "'", "’": "'",
        "“": '"', "”": '"',
        "–": "-", "—": "-",
    })
    return text.translate(table)


def find_anchor(content: str, anchor: str) -> int:
    """Locate an anchor in content. Exact match first, then a whitespace- and
    punctuation-tolerant regex. Returns -1 if not found."""
    import re
    idx = content.find(anchor)
    if idx != -1:
        return idx

    # Tolerant match: flexible whitespace between words, normalized quotes/dashes
    norm_content = _normalize_for_match(content)
    words = _normalize_for_match(anchor).split()
    if not words:
        return -1
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, norm_content)
    return m.start() if m else -1


def _absorb_trailing_headings(content: str, cut: int, floor: int) -> int:
    """Move a slice boundary back over Markdown heading lines that would
    otherwise be stranded at the tail of the previous slice.

    Anchors are body paragraphs, so a boundary falling right after a
    `## Section Title` leaves that heading orphaned on the previous video
    (a title with no body) while the next video gets the body with no title.
    That mis-assignment has caused real duplicated content, so pull any
    run of trailing headings forward into the slice they belong to.
    Never moves back past `floor` (the previous video's anchor).
    """
    while cut > floor:
        head = content[:cut]
        stripped = head.rstrip()
        line_start = stripped.rfind("\n") + 1
        last_line = stripped[line_start:]
        if not last_line.startswith("#") or line_start < floor:
            break
        cut = line_start
    return cut


def split_content_by_anchors(content: str, videos: list) -> dict:
    """
    Split the cleaned content mechanically at the model's start_anchors.

    The model emits only short verbatim anchors (instead of re-emitting the
    entire transcript into segments.json, which cost thousands of output
    tokens and routinely broke JSON parsing). Each video's content runs from
    its anchor to the next video's anchor; the last video runs to the end.

    Returns {video_number: content_slice}. Raises ValueError when an anchor
    is missing, unmatched, or out of order — the caller retries with the
    error fed back to the model.
    """
    positions = []
    for video in videos:
        num = video.get("number")
        anchor = (video.get("start_anchor") or "").strip()
        if not anchor:
            raise ValueError(f"Video {num}: missing start_anchor")
        idx = find_anchor(content, anchor)
        if idx == -1:
            raise ValueError(f"Video {num}: start_anchor not found in content: {anchor[:80]!r}")
        positions.append((num, idx))

    for (n1, i1), (n2, i2) in zip(positions, positions[1:]):
        if i2 <= i1:
            raise ValueError(
                f"Video {n2}: start_anchor occurs at or before Video {n1}'s anchor "
                f"— videos must be in source order")

    # Pull section headings that sit just before a boundary into the slice
    # whose body they title (see _absorb_trailing_headings).
    cuts = [idx for _, idx in positions]
    for k in range(1, len(cuts)):
        cuts[k] = _absorb_trailing_headings(content, cuts[k], cuts[k - 1] + 1)

    slices = {}
    for k, (num, _) in enumerate(positions):
        start = cuts[k]
        end = cuts[k + 1] if k + 1 < len(cuts) else len(content)
        slices[num] = content[start:end].strip()
    return slices


def materialize_segments(pipeline_dir, segments, content):
    """API-free tail of segmentation: slice content at the anchors, write
    segments.json + each Video-N/content.txt. Raises ValueError when an anchor
    doesn't match (caller re-prompts with the error appended)."""
    slices = split_content_by_anchors(content, segments.get("videos", []))
    for video in segments.get("videos", []):
        video["content"] = slices.get(video.get("number"), "")
        if not video.get("duration_estimate"):
            mins = max(1, round(len(video["content"].split()) / 2.5 / 60))
            video["duration_estimate"] = f"{mins} minutes"

    segments_path = Path(pipeline_dir) / "segments.json"
    with open(segments_path, "w") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)

    for video in segments.get("videos", []):
        num = video.get("number", 0)
        video_dir = Path(pipeline_dir) / f"Video-{num}"
        video_dir.mkdir(parents=True, exist_ok=True)
        with open(video_dir / "content.txt", "w") as f:
            f.write(f"# {video.get('title', 'Untitled')}\n\n")
            f.write(f"**Core Concept:** {video.get('core_concept', '')}\n\n")
            f.write("---\n\n")
            f.write(video.get("content", ""))
            f.write("\n\n---\n\n")
            f.write("**Key Takeaways:**\n")
            for takeaway in video.get("key_takeaways", []):
                f.write(f"- {takeaway}\n")
        print(f"  Video {num}: {video.get('title', 'Untitled')}")
        print(f"           Duration: {video.get('duration_estimate', 'N/A')}")
    print(f"\nSaved: {segments_path}")


def scaffold_single_video(pipeline_dir, folio: bool = False):
    """Deterministic single-video lecture: one Video-1 holding the full
    cleaned content, duration_estimate from word count @2.5wps (REQUIRED —
    it scales the script's duration/frame hints). With folio=True the estimate
    assumes the script narrates ~75% of its dossier (informational: humanities
    script hints are computed from the dossier by humanities_targets)."""
    import re as _re
    L = Path(pipeline_dir)
    content = (L / "content_cleaned.txt").read_text(encoding="utf-8")
    m = _re.match(r"^#\s+(.+)$", content, _re.MULTILINE)
    title = m.group(1).strip() if m else L.name
    d = L / "Video-1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "content.txt").write_text(content, encoding="utf-8")
    ratio = DOSSIER_SELECTION_RATIO if folio else 1.0
    mins = max(1, round(len(content.split()) * ratio / 2.5 / 60))
    (L / "segments.json").write_text(json.dumps({"video_count": 1, "videos": [
        {"number": 1, "title": title, "core_concept": title, "key_takeaways": [],
         "examples": [], "duration_estimate": f"{mins} minutes"}]},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"single-video lecture: {title} ({len(content.split()):,} words, ~{mins} min)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python segment_concepts.py <pipeline_dir> --single-video [--folio]")
        print("       python segment_concepts.py <pipeline_dir> --apply RESPONSE.json")
        print()
        print("Divides cleaned content into concept videos.")
        print("Requires: content_cleaned.txt")
        print()
        print("Modes (the segmentation itself is authored by a Claude Code subagent):")
        print("  --single-video   scaffold Video-1 with the full content (no LLM)")
        print("  --folio          humanities sizing (a script narrates ~75% of its dossier)")
        print("  --apply FILE     materialize a saved segmentation response JSON:")
        print("                   split at its start_anchors, write segments.json +")
        print("                   Video-N/content.txt (no LLM; non-zero exit + the")
        print("                   anchor error on stderr if an anchor doesn't match)")
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    pipeline_dir = sys.argv[1]
    is_folio = "--folio" in sys.argv
    forced_target = 0
    if "--target" in sys.argv:
        forced_target = int(sys.argv[sys.argv.index("--target") + 1])

    if "--single-video" in sys.argv:
        scaffold_single_video(pipeline_dir, folio=is_folio)
        return
    if "--apply" in sys.argv:
        response_path = Path(sys.argv[sys.argv.index("--apply") + 1])
        content = (Path(pipeline_dir) / "content_cleaned.txt").read_text(encoding="utf-8")
        raw = response_path.read_text(encoding="utf-8")
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        segments = json.loads(raw)
        try:
            materialize_segments(pipeline_dir, segments, content)
        except ValueError as e:
            print(f"Anchor split failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Bare invocation: the segmentation plan is authored by a Claude Code subagent.
    render_flags = ""
    if is_folio:
        render_flags = " --folio" + (f" --target {forced_target}" if forced_target else "")
    sys.exit(
        "The segment step is authored by a Claude Code subagent.\n"
        "Render its prompt: venv/bin/python scripts/render_step_prompt.py segment "
        f"--content {pipeline_dir}/content_cleaned.txt{render_flags}\n"
        f"then apply the response: python scripts/segment_concepts.py {pipeline_dir} "
        "--apply RESPONSE.json — see .claude/skills/run-pipeline/SKILL.md."
    )


if __name__ == "__main__":
    main()
