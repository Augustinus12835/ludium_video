# Source resolution

How each source-material type enters the pipeline. Each path is idempotent — skip any step
whose output already exists. Sources that arrive as text (book chapters, slide decks) skip
transcription and cleaning; Phase A starts at segment.

## YouTube URL

Pass the URL straight to the pipeline — it fetches captions when available, otherwise
downloads the audio with yt-dlp and transcribes with ElevenLabs Scribe:

```bash
venv/bin/python scripts/pipeline.py run "https://www.youtube.com/watch?v=..." --from transcribe --to transcribe --no-review [--math|--technical]
```

The folder name is auto-generated from the video title; continue Phase A at clean.

## Local video / audio recording

Drop the file in `inputs/` named after the pipeline folder (e.g.
`inputs/Calculus_1_Lecture_07.mp4` — exact or partial name match works), then run the same
transcribe slice with the folder name; `pipeline.py` finds the file and calls
`transcribe_lecture.py` (Scribe by default). Or transcribe explicitly first:

```bash
venv/bin/python scripts/transcribe_lecture.py inputs/<recording>.mp4
```

Continue Phase A at clean.

## Book chapter (PDF / Markdown / AsciiDoc)

**PDF first needs a Markdown conversion — do it with a subagent, not a text extractor.**
Plain text extraction mangles equations, subscripts, and Greek letters. Spawn one subagent
per chapter that Reads the PDF (vision) and transcribes it to Markdown with all math as
LaTeX (`$...$` / `$$...$$`), headings preserved, no commentary — written to
`inputs/book/chapterNN.md`. This is the one write to `inputs/` the skill allows. `.md` /
`.adoc` / `.txt` chapters skip this.

Then scaffold the cleaning step (deterministic extraction + prompt emission — no API):

```bash
venv/bin/python scripts/clean_book_chapter.py inputs/book/chapter02.md --pipeline BOOK [--chapter N] [--profile physics] [--attribution "..."] > <scratch>/book_clean_meta.json
```

It writes `source_extracted.txt` + `clean_prompt.txt` into `pipeline/<dir>/` and prints meta
JSON (`pipeline_dir`, `content_path`, `clean_prompt`, `content_header`,
`content_title_line`, `extracted_chars`, `chunking_needed`). One subagent Reads the
`clean_prompt` file, follows it, and writes the cleaned Markdown body to `content_path` —
prepending the `content_header` comment, a blank line, the `content_title_line`, and a blank
line; body only, no fences. If `chunking_needed` (>45k chars), split `source_extracted.txt`
at `## ` headings into ~25k chunks, one subagent each, and concatenate. Run the Phase A
coverage gate, then proceed to segment.

Multi-chapter units: a manifest JSON (`{"units": [{"unit", "title", "profile", "mode",
"sources": [{"chapter" | "file", "sections", "exclude"?, "drop_end_matter"?}]}]}`) composes
sections across chapters — `clean_book_chapter.py --manifest <path> --book-dir inputs/book
--unit <name>`. A source names its markdown by `chapter` number (`chapter05.md`) or by
explicit `file` basename (`"appendixC"`); `sections` are top-level `N.M` numbers (`"all"`
for the whole file); `exclude` drops named SUBsections (`"5.3.6"`) from the slice; end
matter (Further Reading / Practice Questions …) is cut automatically unless
`"drop_end_matter": false`. A `sections not found` error means the manifest lists a section
the markdown doesn't have — fix the manifest and re-run; that's the intended validation
gate.

## PPTX slide deck

Slide decks are usually copyrighted, so the cleaning prompt rewrites substantially in its
own words — check the rights on your source deck before publishing anything produced from
it.

```bash
venv/bin/python scripts/clean_slides_pptx.py inputs/slides/Course_Ch05.pptx --pipeline <PREFIX> --emit-prompt > <scratch>/pptx_clean_meta.json
```

Writes `slides_extracted.txt`, `source_info.json`, `clean_prompt.txt` into the pipeline dir
and prints meta (`content_path`, `clean_prompt`, `content_header`, `content_title_line`,
`extracted_chars`, `chunking_needed`). One subagent Reads the `clean_prompt` file, follows
it, and writes the cleaned Markdown body to `content_path` — same header/title-line/body
convention and chunking rule as the book flow. It flags any source-originated numeric error
with a parenthetical, keeping the slide's value (publisher decks do contain real errors).
Chapter comes from the filename, title from the first slide (`--chapter`/`--title`
override).

Then segment in `--technical` mode (standard concept segmentation) and run the standard
Phase B chain.

## Humanities sources (folio mode)

Folio takes the same inputs as math and technical runs. Two examples:

- **A recorded lecture course** — the user's own lectures or a published course (e.g. Open
  Yale Courses). Run the YouTube or recording path above with `--folio`:
  transcribe → clean (the standard clean prompt) → coverage gate → segment. Record the source
  (course, lecturer, licence if any) in `pipeline/<L>/source_info.json` so it travels with the
  video.
- **A book** (e.g. an Open Book Publishers title). Divide the book into ~20-minute **episodes** with a manifest, the same machinery as the
  multi-chapter units above.

Either way `content_cleaned.txt` is a research **dossier**, not a script: the folio script
narrates ~75% of it, written from an argument map (references/phase-b-folio.md). Size each
video at ~4,000 cleaned words (3,000–5,000): `segment_concepts.target_video_count` computes
the count, and `render_step_prompt.py segment --folio` / `segment_concepts.py <L>
--single-video --folio` use it (`pipeline.py run <L> --folio` does this automatically).

### Book → episodes (worked example: Plato's *Republic*)

`docs/examples/plato_republic_episodes.json` divides Sean McAleer, *Plato's 'Republic': An
Introduction* (Open Book Publishers 2020, CC BY 4.0) into 26 episodes of 3.2k–5k words, cut at
the book's own section seams in the Republic's order.

1. **Convert the book to one Markdown file per section.** Publisher PDFs with a real text
   layer need no OCR: `scripts/obp_pdf_to_markdown.py` reads the typesetting (headings by
   font, indented displays such as P1/P2/C argument reconstructions, footnotes split out,
   running headers dropped, line-end hyphens resolved against the book's own vocabulary):
   ```bash
   venv/bin/python scripts/obp_pdf_to_markdown.py inputs/plato_republic_mcaleer/<book>.pdf -o inputs/plato_republic_mcaleer
   ```
   → `sec_<CC>_<SS>.md` (one `## heading` + prose each), `sections.json` (heading, Stephanus
   range, word count per section — the table you plan episodes from) and `notes/ch_CC.md`. The
   font roles are the OBP house layout; for another publisher, dump a page's spans with
   PyMuPDF and adjust the font/size rules in `extract()`. Spot-check fidelity (e.g. sample
   5-word runs of the PDF's body text and confirm each appears in the Markdown). Scanned books
   without a text layer take the vision-subagent PDF path above instead.
2. **Plan the episodes** in a manifest: each unit lists its section files
   (`{"file": "sec_01_02", "sections": "all", "drop_end_matter": false}`), a title, and
   `"profile": "commentary"`. Keep each at 3,000–5,000 words; cut at genuine seams; leave out
   book apparatus (chapter previews, reading lists). A top-level `source` block is written to
   `source_info.json`; a top-level `attribution` heads `content_cleaned.txt`.
3. **Compose + emit the clean prompt** per episode:
   ```bash
   venv/bin/python scripts/clean_book_chapter.py --manifest docs/examples/plato_republic_episodes.json \
       --book-dir inputs/plato_republic_mcaleer --unit Republic_E01_Two_Questions_And_A_Walk_To_The_Piraeus
   ```
   The `commentary` profile is a LIGHT clean: apparatus and cross-chapter references resolved
   into content, every quotation kept verbatim with its reference, every standard-form
   argument kept as a `> ` block, the author's interpretive "I" kept as attributed stance
   ("McAleer argues"). Expect 90–100% of the composed length. Join as in the book flow, run
   the coverage gate, then `segment_concepts.py pipeline/<unit> --single-video --folio` and
   the folio Phase B.
4. **Licences.** CC BY needs attribution in every published description. Quotations the book
   itself reproduces from a copyrighted translation are not covered by the book's licence —
   keep them short on screen and in narration, and never supply more of the translation from
   memory.
