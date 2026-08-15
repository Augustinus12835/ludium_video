#!/usr/bin/env python3
"""
Clean open-source book chapters into content_cleaned.txt for the pipeline.

Transforms book chapters (AsciiDoc, Markdown, plain text) into the pipeline's
content_cleaned.txt. Everything this script does is deterministic — it parses
the markup, strips it down to clean source text, scaffolds the pipeline
directory, and renders the exact cleaning prompt. The cleaning itself (the LLM
rewrite) is authored by a Claude Code subagent, NOT an API call: an
orchestrating agent runs this script, spawns a subagent that reads the emitted
clean_prompt.txt, and writes the subagent's output (prefixed with the header
lines from the printed meta JSON) to content_cleaned.txt.

Workflow:
    inputs/book/chapter02.md → clean_book_chapter.py --pipeline BOOK
        → pipeline/BOOK_Ch02_<Title>/{source_extracted.txt, clean_prompt.txt}
        → subagent reads clean_prompt.txt → orchestrator writes content_cleaned.txt
        → pipeline.py run BOOK_Ch02_<Title> --technical

Usage:
    # Scaffold the cleaning step for one chapter (prompt emission is the only mode)
    python scripts/clean_book_chapter.py inputs/book/chapter02.md --pipeline BOOK

    # Override the chapter number / use the equation-preserving profile
    python scripts/clean_book_chapter.py inputs/book/chapter02.md --pipeline BOOK \
        --chapter 2 --profile physics

    # Compose one unit from several chapters/sections of a book (unit manifest)
    python scripts/clean_book_chapter.py --manifest inputs/book/units.json \
        --book-dir inputs/book --unit BOOK_U01_Kinematics
"""

import re
import sys
import json
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# Phase 1: Markup preprocessing (deterministic, before the cleaning subagent)
# ---------------------------------------------------------------------------

def _handle_code(code: str) -> str:
    """Format a code block: keep short ones, summarize long ones."""
    code = code.strip()
    lines = code.split('\n')
    if len(lines) <= 10:
        return f"CODE:\n{code}"
    first_lines = '\n'.join(lines[:5])
    return f"[Code example: {len(lines)} lines]\n{first_lines}\n[... {len(lines) - 5} more lines]"


def detect_format(path: Path) -> str:
    """Detect file format from extension."""
    ext = path.suffix.lower()
    if ext in (".adoc", ".asciidoc"):
        return "asciidoc"
    if ext in (".md", ".markdown"):
        return "markdown"
    return "plaintext"


def preprocess_asciidoc(text: str) -> str:
    """Strip AsciiDoc markup, producing clean plain text."""
    # Strip index markers: ((("term", "subterm")))
    text = re.sub(r'\(\(\(.*?\)\)\)', '', text)

    # Strip anchor IDs: [[anchor-id]]
    text = re.sub(r'^\[\[.*?\]\]\s*$', '', text, flags=re.MULTILINE)

    # Strip role/option attributes: [role="..."] [options="..."] etc.
    # But preserve [source,...] and [TIP]/[NOTE]/etc. for later handling
    text = re.sub(r'^\[(?!source|TIP|NOTE|WARNING|IMPORTANT|CAUTION)[a-z].*?\]\s*$', '', text, flags=re.MULTILINE)

    # Convert headers: == Title → # Title (use [ \t] not \s to avoid matching newlines)
    def convert_header(m):
        level = len(m.group(1)) - 1  # == is h1, === is h2, etc.
        return "#" * level + " " + m.group(2)
    text = re.sub(r'^(={2,6})[ \t]+(.+)$', convert_header, text, flags=re.MULTILINE)

    # Handle code blocks: [source,lang]\n----\n...\n----
    text = re.sub(
        r'^\[source[^\]]*\]\s*\n----\n(.*?)^----',
        lambda m: _handle_code(m.group(1)),
        text,
        flags=re.MULTILINE | re.DOTALL,
    )

    # Handle generic delimited blocks (----, ====, ....)
    # Admonitions: [TIP]\n====\n...\n====
    def handle_admonition(m):
        adm_type = m.group(1)
        content = m.group(2).strip()
        return f"{adm_type}: {content}"

    text = re.sub(
        r'^\[(TIP|NOTE|WARNING|IMPORTANT|CAUTION)\]\s*\n====\n(.*?)^====',
        handle_admonition,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )

    # Strip remaining delimited blocks (====, ....) but keep content
    text = re.sub(r'^(====|\.\.\.\.)\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^----\s*$', '', text, flags=re.MULTILINE)

    # Strip image tags: image::path[alt] → [Figure: alt text]
    text = re.sub(r'image::.*?\[([^\]]*)\]', lambda m: f"[Figure: {m.group(1)}]" if m.group(1) else '', text)

    # Strip cross-references: <<anchor,display text>> → display text; <<anchor>> → remove
    text = re.sub(r'<<[^,>]+,\s*([^>]+)>>', r'\1', text)
    text = re.sub(r'<<[^>]+>>', '', text)

    # Strip inline markup
    text = re.sub(r'\+([^+]+)\+', r'\1', text)       # +literal+
    text = re.sub(r'`([^`]+)`', r'\1', text)          # `monospace`
    text = re.sub(r'\b_([^_]+)_\b', r'\1', text)      # _italic_
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)    # **bold**
    text = re.sub(r'\*([^*]+)\*', r'\1', text)         # *bold*

    # Strip pass-through macros: pass:[content]
    text = re.sub(r'pass:\w*\[([^\]]*)\]', r'\1', text)

    # Strip passthrough blocks: +++...+++ (may contain HTML)
    def handle_passthrough(m):
        content = m.group(1)
        # Strip HTML tags, keep text
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'  +', ' ', content)
        return content.strip()
    text = re.sub(r'^\+\+\+\s*\n(.*?)^\+\+\+', handle_passthrough, text, flags=re.MULTILINE | re.DOTALL)

    # Strip footnote macros: footnote:[text]
    text = re.sub(r'footnote:\[([^\]]*)\]', '', text)

    # Strip AsciiDoc comments: // comment
    text = re.sub(r'^//.*$', '', text, flags=re.MULTILINE)

    # Strip block titles: .Title (line starting with single dot + text)
    text = re.sub(r'^\.\w.*$', '', text, flags=re.MULTILINE)

    # Clean up excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def preprocess_markdown(text: str) -> str:
    """Strip Markdown-specific markup, producing clean plain text."""
    # Strip images: ![alt](url) → [Figure: alt]
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', lambda m: f"[Figure: {m.group(1)}]" if m.group(1) else '', text)

    # Strip links but keep text: [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # Handle code fences
    text = re.sub(
        r'^```\w*\n(.*?)^```',
        lambda m: _handle_code(m.group(1)),
        text,
        flags=re.MULTILINE | re.DOTALL,
    )

    # Strip inline markup
    text = re.sub(r'`([^`]+)`', r'\1', text)          # `code`
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)    # **bold**
    text = re.sub(r'\*([^*]+)\*', r'\1', text)         # *italic*
    text = re.sub(r'__([^_]+)__', r'\1', text)         # __bold__
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)  # _italic_

    # Strip HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Clean up excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def preprocess(text: str, fmt: str) -> str:
    """Run format-specific preprocessing."""
    if fmt == "asciidoc":
        return preprocess_asciidoc(text)
    if fmt == "markdown":
        return preprocess_markdown(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Phase 2: cleaning prompts — rendered via clean_prompt.txt to a Claude Code
# subagent, which returns the cleaned content for the orchestrator to write to
# content_cleaned.txt. (No API call is made by this script.)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an educational content editor. Your job is to transform book chapter \
text into clean, narration-friendly educational prose suitable for video \
production. The source material is from open-source (CC-BY-SA) textbooks."""

BOOK_CLEANING_PROMPT = """\
Rewrite this book chapter text into clean, narration-friendly educational prose \
for a video script pipeline. The content will be spoken by a narrator and \
visualized with animations.

## REMOVE completely:
- **Cross-chapter references**: "as we saw in Chapter 1", "we'll cover in \
Chapter 5", "refer to [figure/table]"
- **Exercises and review questions**: end-of-chapter problems, "try this" sections
- **Chapter summaries**: bullet-point recaps that just list what was covered
- **Bibliographic references**: footnotes, citations, "[1]", bibliography entries
- **Publisher/edition content**: preface references, edition notes, acknowledgments
- **Overly detailed code walkthroughs**: step-by-step line-by-line code \
explanations — condense to key concepts and what the code accomplishes
- **Raw code blocks**: remove CODE: blocks but keep the conceptual explanation \
of what the code does
- **Figure/table references**: "[Figure: ...]" placeholders — describe the \
concept the figure illustrates instead

## TRANSFORM:
- Dense technical prose → narration-friendly pace (shorter sentences, ~2.5 \
words per second target for spoken delivery)
- Passive voice → active voice where natural
- "The reader should note..." → direct statement
- Long lists → prose with key items highlighted
- Tables → prose descriptions or key comparisons
- "Consider the following example" → just give the example directly

## KEEP and preserve accurately:
- All technical concepts, definitions, and terminology
- Architecture descriptions and system designs
- Step-by-step procedures and algorithms (in prose form)
- Real-world examples and use cases
- Numerical examples and calculations
- Key relationships between concepts
- Security considerations and trade-offs

## REWRITE guidelines:
- Write as flowing educational prose organized by topic
- Use your own sentence structure — do not mirror the original phrasing
- Combine fragmented ideas into coherent paragraphs
- Be comprehensive but concise — keep all teaching content, cut all fluff
- Use active voice and clear, direct language
- Maintain the logical flow of the argument
- Preserve technical accuracy completely

---

CHAPTER TEXT:
{text}

---

CLEANED EDUCATIONAL CONTENT:"""


# --- Physics / STEM profile -------------------------------------------------
# The default (general) prompt DELETES equations, code and figure placeholders.
# For a math-heavy textbook that feeds Manim + SymPy downstream we must do the
# opposite: keep every equation, every worked-example step, and turn figure
# placeholders into visual descriptions the Technical-mode visual frames use.

PHYSICS_SYSTEM_PROMPT = """\
You are an educational content editor preparing a physics textbook chapter for \
an automated video pipeline that renders equations with Manim and verifies them \
with SymPy. The source is MIT OpenCourseWare (CC-BY-NC-SA). Preserve all \
physics and mathematics exactly; never simplify away a derivation."""

PHYSICS_CLEANING_PROMPT = """\
Rewrite this physics textbook section into clean, narration-friendly educational \
prose for a video script pipeline. A narrator will speak it; equations and \
diagrams are animated. Accuracy of the physics and math is paramount.

## REMOVE completely:
- Cross-references to other chapters/sections ("as we saw in Chapter 3", \
"see Section 8.2", "refer to Figure 4.4 above")
- End-of-section problems, exercises, and review questions
- Bibliographic citations, footnotes, "[1]" markers
- Running headers/footers, page numbers, license lines, leftover OCR artifacts \
(e.g. "<!-- PAGE n ... -->" comments)

## KEEP and preserve EXACTLY (do not drop, do not simplify):
- **Every equation.** Keep it in LaTeX. Inline as $...$, displayed as $$...$$. \
Do not paraphrase an equation into words — keep the symbolic form. You may drop \
the parenthetical equation numbers like (4.3.2).
- **Every worked example**, with its number and title (e.g. "Example 4.4: \
Accelerating Car") and ALL of its solution steps and intermediate algebra. \
These become the step-by-step math frames — losing a step breaks them.
- All definitions, physical laws, derivations, and the logical order of a derivation
- All physical quantities, SI units (keep "m/s", "kg·m/s²" etc.), constants, \
numerical values, vector/scalar distinctions, signs and subscripts

## TRANSFORM figures (do NOT delete them):
- A "[Figure: ... — caption]" placeholder describes a diagram the video must \
re-draw. Convert it into one or two sentences of *visual description* in context \
(what is shown, what is labeled, what the axes/arrows mean), so the animator can \
recreate it. Keep it adjacent to the prose that refers to it.

## REWRITE guidelines:
- Dense prose → narration-friendly pace (shorter sentences) WITHOUT removing content
- Passive → active voice where natural; use your own sentence structure
- Keep the derivation's logical flow intact; do not reorder steps
- Be comprehensive — this is the single source of truth for the whole video

---

SECTION TEXT:
{text}

---

CLEANED EDUCATIONAL CONTENT:"""


# Appended to clean_prompt.txt after the rendered system+user prompt so the
# subagent returns exactly the file body the orchestrator needs.
CLEAN_PROMPT_TRAILER = """\
(Write ONLY the cleaned markdown body — no preamble, no code fences around the \
output, no commentary. Do not include the source-attribution comment or the \
chapter-title line; the orchestrator prepends those from the meta JSON when it \
writes content_cleaned.txt.)"""


def _light_markdown_preprocess(text: str) -> str:
    """Minimal cleanup that PRESERVES LaTeX/math (unlike preprocess_markdown).

    Used for the physics profile, where the vision-OCR markdown is already clean
    and aggressive inline-markup stripping would corrupt equations.
    """
    # Drop our own OCR fallback HTML comments' marker noise but keep the text.
    text = re.sub(r'<!--\s*PAGE \d+:.*?-->', '', text, flags=re.DOTALL)
    # Convert markdown images to figure placeholders (rare in OCR output).
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)',
                  lambda m: f"[Figure: {m.group(1)}]" if m.group(1) else '', text)
    # Collapse excessive blank lines.
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# Match a top-level section heading like "## 8.3 Velocity" (exactly N.M, not N.M.K)
_SECTION_HEADING = re.compile(r'^#{1,4}\s+(\d+\.\d+)(?!\.\d)\b.*$', re.MULTILINE)


def slice_sections(md_text: str, sections: list[str], verbose: bool = False) -> str:
    """Extract the requested top-level sections (and everything nested under them
    — subsections AND worked examples physically inside them) from chapter md.

    Boundaries are top-level "## N.M" headings; a requested section runs until the
    next top-level section heading (or chapter/EOF). This is robust to the messy
    sub-heading/example numbering because examples live *inside* a section's span.
    """
    # Index every top-level section heading: number -> (start, end_of_section).
    # Skip table-of-contents entries (dotted leaders like "..... 12").
    heads = [m for m in _SECTION_HEADING.finditer(md_text)
             if not re.search(r'\.{4,}\s*\d+\s*$', m.group(0))]
    if not heads:
        if verbose:
            print(f"      WARNING: no '## N.M' headings found; returning whole text")
        return md_text
    spans: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(heads):
        num = m.group(1)
        start = m.start()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md_text)
        # First occurrence wins (TOC pages may repeat a heading with no body).
        if num not in spans or (end - start) > (spans[num][1] - spans[num][0]):
            spans[num] = (start, end)

    out = []
    missing = []
    for sec in sections:
        if sec in spans:
            s, e = spans[sec]
            out.append(md_text[s:e].strip())
        else:
            missing.append(sec)
    if missing:
        raise ValueError(
            f"sections not found in chapter markdown: {missing} "
            f"(available: {sorted(spans)})"
        )
    if verbose:
        print(f"      sliced sections {sections}: {sum(len(o) for o in out):,} chars")
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# Phase 3: Output — scaffold the pipeline dir + emit the cleaning prompt
# ---------------------------------------------------------------------------

def extract_chapter_title(text: str) -> tuple[int | None, str | None]:
    """Extract chapter number and title from content.

    Looks for patterns like:
    - '# How the System Works'
    - '== How the System Works'
    - 'Chapter 2: How the System Works'
    """
    # Try # header first (post-preprocessing)
    m = re.match(r'^#\s+(.+)', text.strip(), re.MULTILINE)
    if m:
        title = m.group(1).strip()
        # Try to extract number from title like "Chapter 2: ..." or just "2. ..."
        num_m = re.match(r'(?:Chapter\s+)?(\d+)[.:]\s*(.*)', title, re.IGNORECASE)
        if num_m:
            return int(num_m.group(1)), num_m.group(2).strip() or title
        return None, title

    # Try == header (raw AsciiDoc, pre-preprocessing)
    m = re.match(r'^==\s+(.+)', text.strip(), re.MULTILINE)
    if m:
        title = m.group(1).strip()
        num_m = re.match(r'(?:Chapter\s+)?(\d+)[.:]\s*(.*)', title, re.IGNORECASE)
        if num_m:
            return int(num_m.group(1)), num_m.group(2).strip() or title
        return None, title

    return None, None


def extract_chapter_num_from_filename(path: Path) -> int | None:
    """Try to get chapter number from filename like ch02_overview.adoc."""
    m = re.search(r'(\d+)', path.stem)
    return int(m.group(1)) if m else None


def chapter_title_to_dir_name(prefix: str, num: int, title: str | None) -> str:
    """Convert chapter number and title to a pipeline directory name.

    Example: ("BOOK", 2, "How the System Works") -> "BOOK_Ch02_How_The_System_Works"
    """
    dir_name = f"{prefix}_Ch{num:02d}"
    if title:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", title.strip()).strip("_")
        slug = "_".join(w.capitalize() for w in slug.split("_"))
        dir_name += f"_{slug}"
    return dir_name


def _profile_prompts(profile: str) -> tuple[str, str]:
    """Return (system_prompt, cleaning_prompt) for the cleaning profile."""
    if profile == "physics":
        return PHYSICS_SYSTEM_PROMPT, PHYSICS_CLEANING_PROMPT
    return SYSTEM_PROMPT, BOOK_CLEANING_PROMPT


def _write_prompt_files(pdir: Path, preprocessed: str, system: str, user: str) -> None:
    """Write the preprocessed source + full cleaning prompt into the pipeline dir."""
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "source_extracted.txt").write_text(preprocessed, encoding="utf-8")
    (pdir / "clean_prompt.txt").write_text(
        f"{system}\n\n{user}\n\n{CLEAN_PROMPT_TRAILER}", encoding="utf-8")


def _meta(pdir: Path, preprocessed: str, title: str | None, attribution: str,
          profile: str, **extra) -> dict:
    """Build the compact meta JSON the orchestrator consumes.

    The orchestrator writes content_cleaned.txt as:
        <content_header> + blank line + [<content_title_line> + blank line if the
        subagent's output doesn't already start with '#'] + subagent output.
    """
    meta = {
        "pipeline_dir": str(pdir),
        "content_path": str(pdir / "content_cleaned.txt"),
        "clean_prompt": str(pdir / "clean_prompt.txt"),
        "source_extracted": str(pdir / "source_extracted.txt"),
        **extra,
        "title": title,
        "profile": profile,
        "content_header": f"<!-- Source: {attribution} -->",
        "content_title_line": f"# {title}" if title else None,
        "extracted_chars": len(preprocessed),
        "chunking_needed": len(preprocessed) > 45000,
    }
    return meta


def emit_prompt_for_chapter(input_path: Path, args) -> None:
    """Scaffold the pipeline dir and render the cleaning prompt for a subagent.

    Writes source_extracted.txt (the deterministically preprocessed chapter) and
    clean_prompt.txt (the full system+user prompt) into pipeline/<dir>/, then
    prints a COMPACT meta JSON. The orchestrator spawns one subagent told to read
    clean_prompt.txt and return only the cleaned content, then writes that (with
    the header lines) to content_cleaned.txt.
    """
    raw = input_path.read_text(encoding="utf-8")
    if not raw.strip():
        print(json.dumps({"error": f"no text in {input_path}"}))
        sys.exit(1)

    fmt = detect_format(input_path)
    if args.profile == "physics":
        # Light pass that preserves LaTeX; aggressive stripping corrupts math.
        preprocessed = _light_markdown_preprocess(raw)
    else:
        preprocessed = preprocess(raw, fmt)

    if args.verbose:
        reduction = (1 - len(preprocessed) / len(raw)) * 100 if raw else 0
        print(f"      preprocessing ({fmt}, profile={args.profile}): "
              f"{len(raw):,} → {len(preprocessed):,} chars ({reduction:.0f}% reduction)")

    # Extract title from raw text (before preprocessing strips headers)
    num, title = extract_chapter_title(raw)
    if args.chapter is not None:
        num = args.chapter
    if num is None:
        num = extract_chapter_num_from_filename(input_path)
    if title is None:
        _, title = extract_chapter_title(preprocessed)
    if num is None:
        num = 0

    system, prompt_template = _profile_prompts(args.profile)
    user = prompt_template.format(text=preprocessed)

    pdir = Path("pipeline") / chapter_title_to_dir_name(args.pipeline, num, title)
    _write_prompt_files(pdir, preprocessed, system, user)

    meta = _meta(pdir, preprocessed, title, args.attribution, args.profile,
                 chapter=num)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Manifest mode: compose one unit from several chapters/sections of a book.
# The composition is deterministic; the cleaning is prompt emission, same as
# the single-chapter flow.
#
# Manifest format (a JSON file with one unit object, a list of them, a
# {"units": [...]} wrapper, or a directory of per-unit JSON files):
#   {"unit": "BOOK_U01_Kinematics", "title": "Kinematics in One Dimension",
#    "profile": "physics",
#    "sources": [{"chapter": 4, "sections": ["4.1", "4.2"]}, {"chapter": 5}],
#    "source": {"title": "...", "url": "...", "license": "..."}}   # optional
# "sections" omitted / "all" / "*" takes the whole chapter. A top-level
# "source" block in a single-file manifest applies course-wide and is written
# to source_info.json.
# ---------------------------------------------------------------------------

def _resolve_chapter_md(book_dir: Path, chapter: int) -> Path:
    """Find the markdown for a chapter number in the book dir."""
    for cand in (f"chapter{chapter:02d}.md", f"chapter{chapter}.md"):
        p = book_dir / cand
        if p.exists():
            return p
    raise FileNotFoundError(f"chapter {chapter} markdown not in {book_dir}")


def compose_unit_source(book_dir: Path, sources: list[dict], verbose=False) -> str:
    """Concatenate the requested chapters/sections into one raw text blob."""
    parts = []
    for src in sources:
        ch = src["chapter"]
        sections = src.get("sections", "all")
        md = _resolve_chapter_md(book_dir, ch).read_text(encoding="utf-8")
        if sections in ("all", None, "*"):
            parts.append(md)
            if verbose:
                print(f"      ch{ch}: whole chapter ({len(md):,} chars)")
        else:
            parts.append(slice_sections(md, list(sections), verbose))
    return "\n\n".join(parts)


def emit_prompt_for_unit(manifest: dict, book_dir: Path, args,
                         course_source: dict | None = None) -> None:
    """Compose a unit's source and render its cleaning prompt for a subagent.

    Writes source_extracted.txt, clean_prompt.txt (and source_info.json when the
    manifest carries a source block) into pipeline/<unit>/, then prints the meta
    JSON — same contract as emit_prompt_for_chapter.
    """
    unit = manifest["unit"]
    title = manifest.get("title")
    profile = manifest.get("profile", "physics")

    raw = compose_unit_source(book_dir, manifest["sources"], args.verbose)
    if not raw.strip():
        print(json.dumps({"error": f"unit {unit} composed to empty text"}))
        sys.exit(1)
    if args.verbose:
        print(f"      composed source: {len(raw):,} chars")

    preprocessed = (_light_markdown_preprocess(raw) if profile == "physics"
                    else preprocess(raw, "markdown"))
    system, prompt_template = _profile_prompts(profile)
    user = prompt_template.format(text=preprocessed)

    pdir = Path("pipeline") / unit
    _write_prompt_files(pdir, preprocessed, system, user)

    # Source attribution for upload tooling: a "source" block (per-unit or
    # course-wide) is written to source_info.json alongside the scaffold.
    src = manifest.get("source") or course_source
    if src:
        (pdir / "source_info.json").write_text(
            json.dumps(src, indent=2, ensure_ascii=False), encoding="utf-8")

    attribution = manifest.get("attribution") or args.attribution
    meta = _meta(pdir, preprocessed, title, attribution, profile, unit=unit)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def load_manifests(path: Path) -> list[dict]:
    """Load one or many unit manifests from a file or directory."""
    if path.is_dir():
        out = []
        for f in sorted(path.glob("*.json")):
            out.append(json.loads(f.read_text(encoding="utf-8")))
        return out
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if "units" in data:
        return data["units"]
    return [data]


def main():
    parser = argparse.ArgumentParser(
        description="Deterministically preprocess a book chapter and render the "
                    "cleaning prompt for a Claude Code subagent. This script makes "
                    "no API call — content_cleaned.txt is authored by the subagent "
                    "and written by the orchestrator (prompt emission is the only "
                    "mode; see --emit-prompt)."
    )
    parser.add_argument("input", nargs="?",
                        help="Chapter file (.adoc/.md/.txt) or a directory containing exactly one")
    parser.add_argument("--pipeline", metavar="PREFIX",
                        help="Pipeline dir prefix, e.g. BOOK → pipeline/BOOK_Ch02_<Title>/ "
                             "(required unless --manifest)")
    parser.add_argument("--chapter", type=int, metavar="NUM",
                        help="Override chapter number (otherwise extracted from filename/content)")
    parser.add_argument("--profile", choices=["general", "physics"], default="general",
                        help="Cleaning profile: 'physics' preserves equations + figure context "
                             "for math-heavy books feeding Manim/SymPy; 'general' is prose-oriented")
    parser.add_argument("--attribution", metavar="TEXT", default="open-source textbook",
                        help="Source attribution for the content_cleaned.txt header comment, "
                             "e.g. 'Book Title (CC-BY-SA 4.0)' (default: %(default)r)")
    parser.add_argument("--manifest", metavar="PATH",
                        help="Unit manifest file or directory of manifests (compose one unit "
                             "from several chapters/sections; see module source for the format)")
    parser.add_argument("--book-dir", metavar="DIR", default="inputs/book",
                        help="Directory of chapterNN.md files for --manifest mode "
                             "(default: %(default)s)")
    parser.add_argument("--unit", metavar="NAME",
                        help="With --manifest: process only this unit name (required when the "
                             "manifest holds more than one unit)")
    parser.add_argument("--emit-prompt", action="store_true",
                        help="No-op, accepted for symmetry with clean_slides_pptx.py — "
                             "prompt emission is this script's only mode")
    parser.add_argument("--verbose", action="store_true", help="Show preprocessing details")
    args = parser.parse_args()

    # --- Manifest mode: compose one unit from chapters/sections --------------
    if args.manifest:
        manifest_path = Path(args.manifest)
        # Course-level source block (for source_info.json), if present in a single file.
        course_source = None
        if manifest_path.is_file():
            try:
                _raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(_raw, dict):
                    course_source = _raw.get("source")
            except Exception:
                course_source = None
        manifests = load_manifests(manifest_path)
        if args.unit:
            manifests = [m for m in manifests if m.get("unit") == args.unit]
            if not manifests:
                print(f"No manifest with unit == {args.unit}")
                sys.exit(1)
        if len(manifests) != 1:
            print("Error: prompt emission handles one unit at a time — pick one with "
                  f"--unit (available: {[m.get('unit') for m in manifests]})")
            sys.exit(1)
        emit_prompt_for_unit(manifests[0], Path(args.book_dir), args, course_source)
        return

    # --- Single-chapter mode --------------------------------------------------
    if not args.input:
        parser.error("input is required unless --manifest is given")

    input_path = Path(args.input)
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            f for f in input_path.iterdir()
            if f.suffix.lower() in (".adoc", ".asciidoc", ".md", ".markdown", ".txt")
        )
    else:
        print(f"Error: {input_path} not found")
        sys.exit(1)

    if not files:
        print(f"No chapter files found in {input_path}")
        sys.exit(1)
    if len(files) != 1:
        print("Error: prompt emission handles one chapter at a time; run once per file:")
        for f in files:
            print(f"  python scripts/clean_book_chapter.py {f} --pipeline <PREFIX>")
        sys.exit(1)
    if not args.pipeline:
        print("Error: --pipeline PREFIX is required "
              "(it names pipeline/<PREFIX>_Ch<NN>_<Title>/)")
        sys.exit(1)

    emit_prompt_for_chapter(files[0], args)


if __name__ == "__main__":
    main()
