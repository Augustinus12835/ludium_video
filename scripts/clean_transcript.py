#!/usr/bin/env python3
"""
Transcript Cleaning - Extract educational content only.
No timestamps, no structure - just clean educational prose.

The cleaning itself is authored by an Opus subagent (API route removed
2026-07-25). This module keeps CLEANING_PROMPT / extract_full_text /
chunk_text for render_step_prompt.py and the /run-pipeline skill.
"""

import sys


CLEANING_PROMPT = """Extract the educational content from this lecture transcript.

REMOVE completely:
- Course administration (syllabus, grading, assignments, deadlines, office hours)
- Housekeeping ("can you hear me", "let me share my screen", "we'll take a break")
- Filler words and verbal tics (um, uh, like, you know, basically, essentially, right?)
- Redundant explanations (keep the clearest version only)
- Student questions about logistics
- Off-topic personal anecdotes

KEEP and express clearly:
- Concept definitions and explanations
- Key terminology
- Examples that illustrate concepts
- Calculations and problem-solving steps
- Important insights and takeaways
- Real-world applications

OUTPUT:
Clean, flowing prose organized by topic.
Write as if explaining to an engaged student.
Preserve all technical accuracy.

---

LECTURE TRANSCRIPT:
{transcript}

---

EDUCATIONAL CONTENT:"""


def extract_full_text(transcript: dict) -> str:
    """Extract all text from transcript - either from segments or full text."""
    # Try segments first
    segments = transcript.get("segments", [])
    if segments:
        return " ".join(seg.get("text", "").strip() for seg in segments)
    # Fall back to full text
    return transcript.get("text", "")


def chunk_text(text: str, max_chars: int = 25000) -> list:
    """Split text into chunks for processing."""
    words = text.split()
    chunks = []
    current = []
    current_len = 0

    for word in words:
        if current_len + len(word) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += len(word) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def main():
    sys.exit(
        "The clean step is authored by an Opus subagent (API route removed 2026-07-25).\n"
        "Render its prompt: venv/bin/python scripts/render_step_prompt.py clean "
        "--transcript <pipeline_dir>/transcript.json\n"
        "then write the subagent's output to <pipeline_dir>/content_cleaned.txt "
        "— see .claude/skills/run-pipeline/SKILL.md."
    )


if __name__ == "__main__":
    main()
