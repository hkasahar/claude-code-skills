---
name: gemini-pdf
description: Convert PDF files to clean Markdown using Antigravity CLI (`agy`). Best for academic papers with math, tables, and structured content. The native pipeline feeds page-range chunk PDFs directly to Gemini's vision pathway (agy reads the PDF itself — no lossy text extraction), with an automatic quality-gated fallback to a Mathpix hybrid and Mathpix standalone. Zero marginal cost via a Google subscription. Triggers include "convert PDF to markdown with Antigravity", "agy PDF conversion", "PDF to markdown with math", and "convert paper to markdown".
---

# PDF-to-Markdown Conversion (via Antigravity CLI)

> **Note**: The skill folder is named `gemini-pdf` for backward compatibility with the `/gemini-pdf` slash command. Internally it drives Antigravity CLI (`agy`), Google's successor to the original Gemini CLI.

## When to Use

- Academic papers with mathematical notation (LaTeX reconstructed from the rendered page)
- Papers with complex tables, theorems, proofs, or two-column layouts
- Scanned PDFs (the native vision pathway OCRs them; the Mathpix fallback also handles them)
- Zero-cost conversion via a Google One AI subscription (no API keys for the primary path)

## When NOT to Use

- **Need exact LaTeX source** → ask the author or use Mathpix directly
- **Enormous documents (>500 pages)** → split into chapters first (hard page ceiling)

## Locating the script

Examples below call the script through `$GEMINI_PDF`. Set it once per shell so they work whether the skill was installed as a plugin or copied manually:

```bash
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  GEMINI_PDF="$CLAUDE_PLUGIN_ROOT/skills/gemini-pdf"   # installed as a Claude Code plugin
else
  GEMINI_PDF="$HOME/.claude/skills/gemini-pdf"          # installed by manual copy
fi
```

(The script self-resolves its own helper scripts, so it runs correctly from any location.)

## Quick Usage

```bash
# Basic: native pipeline, outputs paper.md alongside paper.pdf
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf

# Specify output path
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf /tmp/output.md

# Choose a pipeline explicitly
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --pipeline text
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --hybrid
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --force-mathpix

# Disable auto-chunking (single agy pass over the whole PDF)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-chunk

# Disable the quality-gated fallback chain
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-fallback

# Custom quality threshold (default: 60)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --quality-threshold 40
```

## Architecture

### Native pipeline (default)

`agy` reads the PDF **directly** through Gemini's vision/document pathway — math layout, column order, and table geometry are seen as rendered, not guessed back from lossy text extraction:

```
pdfinfo → page count P
P ≤ 10 → single chunk (whole PDF)
P > 10 → split_pdf_pages.py → chunk PDFs of ≤ 10 pages (1-indexed inclusive ranges)
for each chunk SEQUENTIALLY (global agy lock):
    isolated temp dir containing only that chunk PDF
    agy --model "$MODEL" --add-dir <dir> --print-timeout <T>s -p "<prompt>"
    validate (nonce end-sentinel + ratio guards) → retry → bisect on repeated failure
concatenate → quality gates → sanitize → output.md
```

- The first chunk's prompt produces YAML front matter (`title/authors/year/journal/doi`) **plus** the `# Title` heading; continuation chunks follow a seam protocol (transcribe from the first character of the page, resume exactly after the previous chunk's tail — provided as read-only context — never skip, never repeat).
- Failed/truncated chunks are retried once, then recursively bisected down to single pages before a `<!-- CHUNK N FAILED -->` placeholder is accepted.
- Prompts instruct the model to flag unreadable passages as `<!-- UNCERTAIN: ... -->` rather than guess — a flagged gap always beats fluent fabrication.

### Concurrency constraint (hard, probe-verified)

Two concurrent `agy --print` processes **hang** (0 bytes output, both ignore their own `--print-timeout`; verified live 2026-07-07 on agy 1.0.16, plain and with `--new-project`). Consequently:

- All agy calls run strictly sequentially under a global lock (`$TMPDIR/gemini-pdf-agy.lock`); concurrent script invocations serialize automatically.
- Every agy call is wrapped in an external watchdog (`TIMEOUT+60s`) that kills the **whole process group** and sweeps orphans — agy's own timeout cannot be trusted under contention.
- `agy --sandbox` also hangs the native read (probe-verified) and is not used.

### Fallback chain (auto, quality-gated)

```
native ──eligible & ≥threshold──→ done
   │ low
   ▼
hybrid: mathpix --mode extract → agy structural cleanup (math preserved verbatim)
   │ low
   ▼
mathpix standalone → best eligible attempt wins
```

Winner selection is two-stage: **eligibility gates** first (no failed chunks, fidelity floors, completeness floor), then comparison on **literalness-neutral** dimensions only (math quality, structure, OCR, failed chunks) — recall-vs-source dimensions would unfairly reward the most literal attempt. A later stage must beat an earlier one by ≥5 points to displace it. Losing attempts are kept as `output.attempt-<stage>.md`.

Degraded environments: agy missing → Mathpix standalone (the text pipeline needs agy too); poppler missing → text pipeline with markitdown; pypdf missing → poppler page splitter.

### Model configuration

`agy` ≥ 1.0.5 has a working `--model` flag; the script passes `GEMINI_PDF_MODEL` (default **"Gemini 3.1 Pro (High)"**) on every call and falls back to the `~/.gemini/antigravity-cli/settings.json` model only if the installed agy lacks the flag. List models with `agy models`.

### Quality verification

`scripts/quality_check.py` scores 0-100 with per-dimension breakdown:

| Dimension | Weight (with source text) | Checks |
|-----------|--------------------------|--------|
| Content completeness | 20% | raw output vs source-text ratio (band 0.5–2.0) |
| Math quality | 20% | balanced `$`/`$$` (currency-aware), well-formed LaTeX |
| Structural integrity | 15% | title, sections, bibliography, tables, theorem markers |
| OCR artifacts | 10% | replacement chars, garbled runs, ligatures |
| Failed chunks | 10% | any failure hard-caps the overall score at ≤50 |
| Numeric fidelity | 15% | recall of source numerals (boilerplate/page-number filtered) |
| Trigram recall | 10% | recall of source prose trigrams (normalized) |

Plus: `comparison_score` (literalness-neutral, for cross-attempt selection), `eligible` + reasons, UNCERTAIN-marker count (small penalty), and front-matter detection. Corrupted PDF text layers (some 1990s journal PDFs encode `.` as `+` or `(` as `Ž`) are detected automatically and the source-dependent dimensions are skipped — recall against a garbled reference would punish a *correct* conversion. Without source text, a legacy five-dimension weighting applies. Non-mathematical documents redistribute the math weight adaptively.

### Sanitization

Converted Markdown originates from an untrusted PDF; `scripts/sanitize_md.py` strips `<script>`/`<iframe>`/`<object>` blocks, HTML event-handler attributes, and `javascript:` URLs before any downstream use, reporting every removal.

### Optional reference-library sync

**Opt-in and off by default.** When `GEMINI_PDF_REFERENCE_DIR` points to an existing directory (layout: `pdf/`, `md/`, `reference.bib`), the final winner (only — never intermediate attempts) is synced there: with `--bib-key KEY`, PDF → `pdf/KEY.pdf`, MD → `md/KEY.md`, and a BibTeX entry is appended (metadata precedence: JSON sidecar → the MD's own YAML front matter → key-only entry). Copies are skip-if-exists; `--force-sync` overwrites the PDF/MD copies (bib entries are never replaced). Without the env var set, nothing is written outside your output path. See [docs/gemini-pdf.md](../../docs/gemini-pdf.md).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_PDF_MODEL` | `Gemini 3.1 Pro (High)` | Passed to `agy --model` on every call |
| `GEMINI_PDF_TIMEOUT` | `600` | Per-chunk timeout (s); watchdog fires at +60s |
| `GEMINI_PDF_PIPELINE` | `native` | `native` \| `text` \| `hybrid` \| `mathpix` |
| `GEMINI_PDF_PAGES_PER_CHUNK` | `10` | Native pipeline pages per chunk |
| `GEMINI_PDF_MAX_PAGES` | `500` | Hard page ceiling (split larger docs first) |
| `GEMINI_PDF_CHUNK_THRESHOLD` | `50000` | Text pipeline: chunk above this many bytes |
| `GEMINI_PDF_CHUNK_MAX` | `100000` | Text pipeline: max bytes per chunk |
| `GEMINI_PDF_QUALITY_THRESHOLD` | `60` | Quality gate for the fallback chain |
| `GEMINI_PDF_MATHPIX_FALLBACK` | `true` | Enable/disable automatic Mathpix fallback |
| `GEMINI_PDF_LOCK_DIR` | `$TMPDIR/gemini-pdf-agy.lock` | Global agy serialization lock |
| `GEMINI_PDF_REFERENCE_DIR` | (unset — opt-in) | **Optional.** Central reference library root; sync is skipped entirely unless set to an existing directory |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` | (none) | Mathpix API credentials (hybrid + fallback) |

## Flags

| Flag | Description |
|------|-------------|
| `--pipeline native\|text\|hybrid\|mathpix` | Choose the conversion pipeline |
| `--extractor pdftotext\|markitdown` | Text-pipeline extractor (implies `--pipeline text`) |
| `--no-chunk` | Single agy pass (native: whole PDF; text: no splitting) |
| `--no-fallback` | Disable the quality-gated fallback chain |
| `--force-mathpix` | Mathpix standalone (skip agy entirely) |
| `--hybrid` | Mathpix extraction + agy structural cleanup |
| `--bib-key KEY` | Sync winner to the reference library under KEY (needs `GEMINI_PDF_REFERENCE_DIR`) |
| `--force-sync` | Overwrite existing library PDF/MD copies |
| `--quality-threshold N` | Override quality gate (default 60) |

## Output files

For `output.md` the script may also write: `output.err` (agy stderr per chunk), `output.meta.json` (per-chunk ledger: page ranges, bytes, retries, status), `output.quality.json` (full quality report), and `output.attempt-<stage>.md` (losing fallback attempts, kept for manual override).

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `agy not found` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |
| `Authentication required` from `agy` | Run `agy` interactively once and complete the OAuth flow |
| Conversion very slow | Chunks run sequentially by design (parallel agy hangs — verified). ~1-4 min/chunk is normal |
| Another conversion seems stuck | Check the lock: `ls $TMPDIR/gemini-pdf-agy.lock` — stale locks from dead processes are removed automatically |
| Timeout on a dense chunk | Increase `GEMINI_PDF_TIMEOUT=1200`; the pipeline also auto-bisects failing chunks |
| `<!-- CHUNK N FAILED -->` in output | That page range failed after retries + bisection; re-run, or convert those pages separately |
| Math still garbled | Confirm the model: `GEMINI_PDF_MODEL="Gemini 3.1 Pro (High)"`; try `--hybrid` (Mathpix math OCR) |
| Scanned PDF (no text layer) | Native pipeline handles it (vision); `--force-mathpix` is the alternative |
| Low quality score | Inspect `output.quality.json`; try `--hybrid` or `--force-mathpix`; lower `--quality-threshold` |
| Mathpix fallback not triggering | Set `MATHPIX_APP_ID` and `MATHPIX_APP_KEY` |
| Empty output | Check `output.err` for agy errors |
| Wrong model used | This agy build may lack `--model` (warning is printed); set it in `~/.gemini/antigravity-cli/settings.json` |
