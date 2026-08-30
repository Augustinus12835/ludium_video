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
venv/bin/python scripts/clean_book_chapter.py inputs/book/chapter02.md --pipeline BOOK [--chapter N] [--profile physics] [--attribution "..."] > /tmp/book_clean_meta.json
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
venv/bin/python scripts/clean_slides_pptx.py inputs/slides/Course_Ch05.pptx --pipeline <PREFIX> --emit-prompt > /tmp/pptx_clean_meta.json
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
