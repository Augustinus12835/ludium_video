#!/usr/bin/env python3
"""Open Book Publishers PDF → one markdown file per book section (no OCR).

Built for Sean McAleer, *Plato's 'Republic': An Introduction* (OBP 2020,
doi 10.11647/OBP.0229, CC BY 4.0), the worked example for humanities book sources
(docs/examples/plato_republic_episodes.json).
OBP's PDFs are InDesign exports with a clean text layer, so the structure comes
straight from the typesetting, the way ptx_to_markdown.py / cnxml_to_markdown.py
read their books' own sources:

    CalifornianFB 24 pt        chapter title        → `## Fathers and Sons (Book I)` on the lead-in
    CalifornianFB 18 pt        chapter subtitle     → title tail + the Republic book(s)
    CalifornianFB 14/16 pt     section heading      → `## Polemarchus Wants You to Wait (1.327a–328c)`
    Pagella 10 pt              body; a first-line indent starts a paragraph
    Pagella 9 pt, indented     displayed block (standard-form argument P1/P2/C,
                               long quotation)      → `> ` lines
    Pagella 9 pt at the top    running header       → dropped
    Pagella 8 pt               footnotes            → notes/ch_NN.md (not the dossier)
    Pagella 7 pt               chapter-opener licence footer → dropped
    spans < 8 pt inside body   footnote reference marks → dropped

Output in <out>/:
    sec_<CC>_<SS>.md   one `## heading` + prose (no H1); CC = chapter (00 = Introduction, 15 = Afterword),
                       SS = section (00 = the chapter's lead-in before its first heading)
    sections.json      index: id, chapter, chapter title, heading, Stephanus range, words
    notes/ch_<CC>.md   the chapter's footnotes, numbered as printed

End-of-line hyphens are resolved against the book's own vocabulary: a break is
closed up ("argu-/ment" → "argument") when the joined word occurs unhyphenated
elsewhere in the book, and kept ("stand-/alone") otherwise.

    venv/bin/python scripts/obp_pdf_to_markdown.py \
        inputs/plato_republic_mcaleer/mcaleer_republic.pdf -o inputs/plato_republic_mcaleer
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

HEAD_FONT = "CalifornianFB"
# Back matter that ends the prose (List of Illustrations, Bibliography, Index).
BACK_MATTER = re.compile(r"^(List of Illustrations|Bibliography|Index)\b")
STEPHANUS = re.compile(r"\(([^()]*\d+[a-e][^()]*)\)\s*$")


def _line_records(page):
    """Merge a page's spans into visual lines: (y, x, size, font, text, spans)."""
    rows = {}
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            spans = [s for s in l["spans"] if s["text"]]
            if not spans:
                continue
            y = round(l["bbox"][1])
            # lines sharing a baseline (a "P1" label and its premise) are one row
            key = next((k for k in rows if abs(k - y) <= 2), y)
            rows.setdefault(key, []).append((l["bbox"][0], spans))
    out = []
    for y in sorted(rows):
        parts = sorted(rows[y], key=lambda p: p[0])
        spans = [s for _, ss in parts for s in ss]
        main = max(spans, key=lambda s: len(s["text"].strip()))
        out.append({"y": y, "x": parts[0][0], "size": round(main["size"], 1),
                    "font": main["font"], "spans": spans, "parts": parts})
    return out


def _body_text(rec) -> str:
    """Line text with footnote reference marks (tiny superscripts) removed."""
    body = rec["size"]
    txt = ""
    for s in rec["spans"]:
        if s["size"] < body - 1.5 and re.fullmatch(r"\s*\d+\s*", s["text"]):
            continue
        txt += s["text"]
    return txt


def _display_text(rec) -> str:
    """A displayed row: 'P1' label + premise → 'P1: premise'."""
    parts = [" ".join(s["text"] for s in ss).strip() for _, ss in rec["parts"]]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and re.fullmatch(r"(P\d+\*?|C\d*\*?|\(\w+\)|\d+\.)", parts[0]):
        return parts[0] + ": " + " ".join(parts[1:])
    return " ".join(parts)


def extract(pdf: Path):
    doc = fitz.open(pdf)
    chapters = []            # [{num, title, subtitle, blocks: [(kind, text)], notes: [str]}]
    cur = None
    started = False
    for pno in range(len(doc)):
        recs = _line_records(doc[pno])
        big = [r for r in recs if r["font"].startswith(HEAD_FONT) and r["size"] >= 23]
        if big:
            title = " ".join(_body_text(r).strip() for r in big).strip()
            if BACK_MATTER.match(title):
                break
            if title.startswith("Introduction"):
                started = True
            if not started:
                continue
            m = re.match(r"^(\d+)\.\s*(.+)$", title)
            num = int(m.group(1)) if m else (0 if title.startswith("Introduction") else 15)
            cur = {"num": num, "title": m.group(2).strip() if m else title,
                   "subtitle": "", "blocks": [], "notes": []}
            chapters.append(cur)
        if not started or cur is None:
            continue

        body_x = Counter(round(r["x"]) for r in recs if r["size"] == 10.0)
        left = min((x for x, n in body_x.items() if n >= 3), default=None)
        prev_kind = None
        heading_buf = []
        for r in recs:
            size, font, y = r["size"], r["font"], r["y"]
            if font.startswith(HEAD_FONT):
                if r["size"] >= 23:
                    continue
                if 17 <= size < 23:
                    cur["subtitle"] = (cur["subtitle"] + " " + _body_text(r).strip()).strip()
                    continue
                heading_buf.append(_body_text(r).strip())
                prev_kind = "heading"
                continue
            if heading_buf:
                cur["blocks"].append(("heading", " ".join(heading_buf)))
                heading_buf = []
            if size <= 7.5:              # licence footer on chapter openers
                continue
            if size == 9.0 and y < 60:   # running header
                continue
            if 7.5 < size < 8.5:         # footnote text
                txt = " ".join(" ".join(s["text"] for s in ss).strip() for _, ss in r["parts"]).strip()
                if re.fullmatch(r"\d+", txt.split()[0] if txt.split() else "") and len(r["parts"]) >= 2:
                    cur["notes"].append(txt)
                elif cur["notes"]:
                    cur["notes"][-1] += "\n" + txt
                continue
            if size < 9.5 or (left is not None and r["x"] > left + 12 and size < 10):
                txt = _display_text(r)
                if prev_kind == "display" and not re.match(r"^(P\d+\*?|C\d*\*?):", txt) \
                        and cur["blocks"] and cur["blocks"][-1][0] == "display":
                    cur["blocks"][-1] = ("display", cur["blocks"][-1][1] + "\n" + txt)
                else:
                    cur["blocks"].append(("display", txt))
                prev_kind = "display"
                continue
            txt = _body_text(r)
            indented = left is not None and r["x"] > left + 8
            # a flush line continues the open paragraph — across a page turn too
            # (prev_kind is None at the top of a page)
            if (indented or prev_kind in ("heading", "display")
                    or not cur["blocks"] or cur["blocks"][-1][0] != "para"):
                cur["blocks"].append(("para", txt))
            else:
                cur["blocks"][-1] = ("para", cur["blocks"][-1][1] + "\n" + txt)
            prev_kind = "para"
        if heading_buf:
            cur["blocks"].append(("heading", " ".join(heading_buf)))
    return chapters


def _vocab(chapters) -> set:
    words = Counter()
    for ch in chapters:
        for _, t in ch["blocks"]:
            words.update(w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]+", t))
    return {w for w, n in words.items()}


def _join_lines(text: str, vocab: set) -> str:
    lines = [l.rstrip() for l in text.split("\n")]
    out = lines[0] if lines else ""
    for nxt in lines[1:]:
        nxt = nxt.strip()
        m = re.search(r"([A-Za-zÀ-ÿ]+)-$", out)
        if m and not out.endswith("—"):
            head = m.group(1)
            tail = re.match(r"[A-Za-zÀ-ÿ]+", nxt)
            joined = (head + tail.group(0)).lower() if tail else ""
            if tail and joined in vocab:
                out = out[:-1] + nxt
                continue
            out = out + nxt          # keep the real hyphen: "stand-alone"
            continue
        out = out + ("" if out.endswith(("—", "–")) else " ") + nxt
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def to_sections(chapters):
    vocab = _vocab(chapters)
    sections = []
    for ch in chapters:
        secs = [{"heading": None, "blocks": []}]
        for kind, t in ch["blocks"]:
            if kind == "heading":
                secs.append({"heading": re.sub(r"\s+", " ", t).strip(), "blocks": []})
            else:
                secs[-1]["blocks"].append((kind, t))
        if not secs[0]["blocks"]:
            secs = secs[1:]
        for i, s in enumerate(secs):
            body = []
            for kind, t in s["blocks"]:
                if kind == "para":
                    body.append(_join_lines(t, vocab))
                else:
                    rows = [_join_lines(r, vocab) for r in re.split(r"\n(?=(?:P\d+\*?|C\d*\*?): )", t)]
                    body.append("\n>\n".join("> " + r for r in rows))
            sections.append({
                "chapter": ch["num"], "chapter_title": ch["title"],
                "chapter_subtitle": ch["subtitle"], "section": i,
                "heading": s["heading"], "body": [b for b in body if b.strip()],
            })
    return sections


BOOK_TAIL = re.compile(r",?\s*(Books? [IVX]+(?:(?:–| and )[IVX]+)?)$")


def _split_title(ch) -> None:
    """The 24 pt title often breaks onto the 18 pt line ('Taming the Beast:' /
    'Socrates versus Thrasymachus, Book I'); rejoin it and peel off the Book."""
    full = re.sub(r"\s+", " ", f"{ch['title']} {ch['subtitle']}").strip()
    full = re.sub(r"\s+:", ":", full).replace("?:", "?")
    m = BOOK_TAIL.search(full)
    if m and not full[:m.start()].endswith(" in"):
        ch["title"], ch["subtitle"] = full[:m.start()].rstrip(" ,:"), m.group(1)
    else:
        ch["title"], ch["subtitle"] = full, ""


def write(chapters, sections, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "notes").mkdir(exist_ok=True)
    index = []
    for s in sections:
        sid = f"sec_{s['chapter']:02d}_{s['section']:02d}"
        # One `##` heading per file and no H1: an episode dossier composes several
        # files under its own `# <episode title>`. A chapter lead-in is headed by the
        # chapter title and the Republic book(s) it covers.
        book = f" ({s['chapter_subtitle']})" if s["chapter_subtitle"] else ""
        md = [f"## {s['heading']}" if s["heading"] else f"## {s['chapter_title']}{book}"]
        md += s["body"]
        text = "\n\n".join(md) + "\n"
        (out / f"{sid}.md").write_text(text, encoding="utf-8")
        m = STEPHANUS.search(s["heading"] or "")
        index.append({
            "id": sid, "chapter": s["chapter"], "chapter_title": s["chapter_title"],
            "book": s["chapter_subtitle"] or None,
            "heading": s["heading"] or "(chapter lead-in)",
            "stephanus": m.group(1) if m else None,
            "words": len(" ".join(s["body"]).split()),
        })
    for ch in chapters:
        if ch["notes"]:
            (out / "notes" / f"ch_{ch['num']:02d}.md").write_text(
                f"# Notes — {ch['title']}\n\n" + "\n\n".join(ch["notes"]) + "\n", encoding="utf-8")
    (out / "sections.json").write_text(json.dumps(index, indent=1, ensure_ascii=False),
                                       encoding="utf-8")
    total = sum(i["words"] for i in index)
    print(f"{len(chapters)} chapters, {len(index)} sections, {total:,} words → {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    chapters = extract(args.pdf)
    for ch in chapters:
        _split_title(ch)
    write(chapters, to_sections(chapters), args.out)


if __name__ == "__main__":
    main()
