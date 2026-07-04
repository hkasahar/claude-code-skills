---
name: gemini-pdf
description: Convert PDF files to clean Markdown using Antigravity CLI (`agy`). Best for academic papers with math, tables, and structured content. Two-stage pipeline (markitdown → agy) produces LaTeX math, proper headings, and formatted tables. Auto-chunks long papers to handle the model's output token limit. Zero marginal cost via a Google subscription. Triggers include "convert PDF to markdown with Antigravity", "agy PDF conversion", "PDF to markdown with math", and "convert paper to markdown".
---

# PDF-to-Markdown Conversion (via Antigravity CLI)

> **Note**: The skill folder is named `gemini-pdf` for backward compatibility with the `/gemini-pdf` slash command. Internally it now drives Antigravity CLI (`agy`), Google's successor to the original Gemini CLI.

## When to Use

- Academic papers with mathematical notation (reconstructs LaTeX)
- Papers with complex tables, theorems, proofs
- When markitdown alone produces garbled math or broken structure
- Zero-cost conversion via Google One AI Premium (no API keys)

## When NOT to Use

- **Simple text-only PDFs** → `markitdown` alone is sufficient
- **Need exact LaTeX source** → ask the author or use Mathpix
- **Very large documents (>1.5M tokens)** → split into chapters first

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
# Basic: outputs paper.md alongside paper.pdf
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf

# Specify output path
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf /tmp/output.md

# Use pdftotext instead of markitdown for extraction
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf output.md --extractor pdftotext

# Disable auto-chunking (force single-pass even for long papers)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-chunk

# Force Mathpix conversion (skip Antigravity entirely)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --force-mathpix

# Disable Mathpix fallback (`agy` output only, regardless of quality)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-fallback

# Custom quality threshold (default: 60)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --quality-threshold 40
```

## Architecture

Two-stage pipeline that avoids shell ARG_MAX limits:

```
markitdown paper.pdf | agy --print-timeout 600s --print "FORMAT_PROMPT" > output.md
```

1. **Stage 1 — Text extraction**: `markitdown` (default) or `pdftotext` extracts raw text from PDF
2. **Stage 2 — Intelligent formatting**: Text is piped via stdin to Antigravity CLI (`agy --print`), which reformats it into clean Markdown with proper LaTeX math, section hierarchy, tables, and bibliography

### Model configuration

Antigravity CLI v1.0+ has **no `--model` flag**. The model is configured in `~/.gemini/antigravity-cli/settings.json` (key: `"model"`). For math-heavy PDFs, set the model to **"Gemini 3.1 Pro (High)"** — either interactively via the `/model` command inside `agy`, or by editing the settings file directly:

```jsonc
// ~/.gemini/antigravity-cli/settings.json
{
  "model": "Gemini 3.1 Pro (High)",
  ...
}
```

For drafts or lighter loads, `"Gemini 3.5 Flash (High)"` is faster but less accurate on dense math. The `GEMINI_PDF_MODEL` env var is retained in the script for documentation/logging but is **not** passed to `agy` — change the settings file to actually switch models.

The formatting prompt (`scripts/prompt_template.txt`) handles:
- Heading hierarchy reconstruction
- LaTeX math from garbled Unicode
- OCR artifact correction (0↔O, 1↔l, rn↔m, Greek↔Latin)
- Theorem/proof formatting
- Table reconstruction
- Bibliography normalization

### Auto-Chunking (for long papers)

Papers exceeding `GEMINI_PDF_CHUNK_THRESHOLD` chars (default 50K, ~15 pages) are automatically split to work around the model's ~14K output token limit per response:

```
markitdown paper.pdf → extracted text
  ↓
  if text < 50K chars → single-pass (unchanged)
  ↓
  if text ≥ 50K chars → split at section boundaries → group into ≤100K char chunks
  ↓
  chunk 1 → agy (full prompt)         → output part 1
  chunk 2 → agy (continuation prompt) → output part 2
  chunk N → agy (continuation prompt) → output part N
  ↓
  concatenate → final output.md
```

**Splitting strategy** (three-tier):
1. **Primary**: split at `^[0-9]+ [A-Z]` lines (major section headers)
2. **Secondary**: if a single section > 100K chars, sub-split at `^[0-9]+\.[0-9]+ [A-Z]` (subsections)
3. **Tertiary**: if still oversized, split at paragraph boundaries (blank lines)

Chunks 2+ use `scripts/prompt_template_continuation.txt` which omits title/author instructions to prevent duplicate preambles.

If a chunk fails (timeout or error), a `<!-- CHUNK N FAILED -->` placeholder is inserted and processing continues with remaining chunks.

### Quality Verification

After Antigravity (`agy`) produces output, `scripts/quality_check.py` scores it on a 0-100 scale across five dimensions:

| Dimension | Weight | Checks |
|-----------|--------|--------|
| Content completeness | 30% | Output chars vs page count (~2K-12K expected chars/page) |
| Math quality | 25% | Balanced `$`/`$$` delimiters, well-formed LaTeX commands |
| Structural integrity | 20% | Title, sections, bibliography, tables, theorem markers |
| OCR artifact detection | 15% | Unicode replacement chars, garbled consonant runs |
| Failed chunks | 10% | `<!-- CHUNK N FAILED -->` markers |

**Adaptive weighting**: For non-mathematical PDFs (no equations detected), math weight redistributes to other dimensions.

### Automatic Mathpix Fallback

When quality score falls below threshold (default 60), the pipeline automatically falls back to Mathpix if credentials are available:

```
agy output → quality_check.py → PASS (≥ threshold): done
                              → FAIL: check MATHPIX credentials
                                → Missing: warn, keep agy output
                                → Present: Mathpix convert
                                  → quality_check.py → PASS: use Mathpix
                                                     → FAIL: use Mathpix anyway (best available)
```

Requires `MATHPIX_APP_ID` and `MATHPIX_APP_KEY` environment variables. Without them, the agy output is kept with a warning. Use `--no-fallback` to disable, or `--force-mathpix` to skip Antigravity entirely.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_PDF_MODEL` | `(read from agy settings.json)` | **Informational only.** `agy` has no `--model` flag; configure model in `~/.gemini/antigravity-cli/settings.json`. |
| `GEMINI_PDF_TIMEOUT` | `600` (10 min) | Timeout in seconds (per chunk in chunked mode); passed to `agy --print-timeout` as `${TIMEOUT}s` |
| `GEMINI_PDF_CHUNK_THRESHOLD` | `50000` | Auto-chunk documents exceeding this many chars |
| `GEMINI_PDF_CHUNK_MAX` | `100000` | Maximum chars per chunk |
| `GEMINI_PDF_REFERENCE_DIR` | (unset — opt-in) | **Optional.** Central reference library root (`pdf/`, `md/`, `reference.bib`). The reference-sync step is skipped entirely unless this is set to an existing directory. See [docs/gemini-pdf.md](../../docs/gemini-pdf.md#optional-reference-library-sync). |
| `GEMINI_PDF_QUALITY_THRESHOLD` | `60` | Quality score threshold (0-100) for Mathpix fallback |
| `GEMINI_PDF_MATHPIX_FALLBACK` | `true` | Enable/disable automatic Mathpix fallback |
| `MATHPIX_APP_ID` | (none) | Mathpix API app ID (for fallback) |
| `MATHPIX_APP_KEY` | (none) | Mathpix API app key (for fallback) |

## Flags

| Flag | Description |
|------|-------------|
| `--extractor markitdown\|pdftotext` | Choose text extraction backend |
| `--no-chunk` | Disable auto-chunking (force single-pass) |
| `--bib-key KEY` | Bibtex-style key for central reference copy (e.g., `andrews-1999-ecma`) |
| `--no-fallback` | Disable Mathpix fallback; keep `agy` output regardless of quality |
| `--force-mathpix` | Skip Antigravity entirely; use Mathpix for conversion |
| `--quality-threshold N` | Override quality threshold (default 60) |

### Central Reference Sync

**This feature is opt-in and off by default.** It runs only when `GEMINI_PDF_REFERENCE_DIR` points to an existing directory (see [docs/gemini-pdf.md](../../docs/gemini-pdf.md#optional-reference-library-sync)); otherwise it is skipped entirely and nothing is written outside your output path.

When `--bib-key KEY` is provided (and the library is configured), the script syncs to the central reference library:
- Copies PDF → `reference/pdf/{KEY}.pdf`
- Copies MD → `reference/md/{KEY}.md`
- Adds BibTeX entry to `reference/reference.bib` (metadata from JSON sidecar if available)
- All operations skip-if-exists (no overwrites, no duplicates)
- Backs up `reference.bib` → `reference.bib.bak` before first modification

Without `--bib-key`, only the MD file is copied (using its filename).

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `agy not found` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |
| `Authentication required` from `agy` | Run `agy` interactively once and complete the OAuth flow; the script can then run unattended |
| `markitdown not found` | `pip install markitdown` |
| `ModuleNotFoundError: requests` (Mathpix path) | `pip install requests` |
| Timeout on large papers | Increase: `GEMINI_PDF_TIMEOUT=1200 bash ...` (passed as `${T}s` to `--print-timeout`) |
| Math still garbled | (1) confirm model is `Gemini 3.1 Pro (High)` in `~/.gemini/antigravity-cli/settings.json`; (2) try `--extractor pdftotext` |
| Empty output | Check `.err` file next to output for `agy` errors |
| Document too large | Split PDF into chapters, convert separately |
| Output truncated (long paper) | Should auto-chunk now; verify threshold: `GEMINI_PDF_CHUNK_THRESHOLD=30000` |
| `<!-- CHUNK N FAILED -->` in output | A chunk timed out or errored; increase `GEMINI_PDF_TIMEOUT` and re-run |
| Too many chunks (slow) | Increase `GEMINI_PDF_CHUNK_MAX=150000` to reduce chunk count |
| Low quality score | Try `--force-mathpix` or lower `--quality-threshold 40` |
| Mathpix fallback not triggering | Set `MATHPIX_APP_ID` and `MATHPIX_APP_KEY` env vars |
| Scanned PDF (no text layer) | Use `--force-mathpix` (Mathpix handles OCR natively) |
| Wrong model used | `agy` reads model from `~/.gemini/antigravity-cli/settings.json`; `GEMINI_PDF_MODEL` env var is informational only |
