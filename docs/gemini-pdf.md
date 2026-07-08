# gemini-pdf — PDF → clean Markdown

`gemini-pdf` converts PDFs (especially academic papers) into clean Markdown with reconstructed **LaTeX math**, section hierarchy, tables, theorems, and bibliography. The default **native pipeline** feeds page-range chunk PDFs directly to Gemini's vision pathway — **Antigravity CLI (`agy`)** reads the PDF itself, so math layout, column order, and table geometry are seen as rendered rather than guessed back from lossy text extraction. Each converted chunk is validated (nonce end-sentinel, size guards), retried and bisected on failure, scored across seven quality dimensions, and can fall back automatically to a **Mathpix-hybrid** or **Mathpix-standalone** conversion.

> The skill folder is named `gemini-pdf` for backward compatibility with the `/gemini-pdf` command; internally it drives Antigravity (`agy`), Google's successor to the original Gemini CLI.

## Prerequisites

You need Antigravity CLI plus a couple of local tools:

```bash
# 1. Antigravity CLI (the converter) — see docs/SETUP.md for the full walkthrough + the curl|bash explainer
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy            # run once to sign in with your Google account

# 2. PDF tooling (required for the native pipeline)
brew install poppler            # pdfinfo (page count), pdftotext (source text for quality checks)
pip install pypdf               # page-range splitter (falls back to poppler's pdfseparate/pdfunite)

# 3. Optional
pip install markitdown          # alternative extractor for the legacy text pipeline
pip install requests            # required by the Mathpix hybrid/fallback paths
#   and set MATHPIX_APP_ID / MATHPIX_APP_KEY in your environment for Mathpix
```

If you already followed **[docs/SETUP.md](SETUP.md)** for the `delegate` skill, you have `agy` + Claude Code installed already — you only need `poppler` and `pypdf` here.

### Model setup

`agy` ≥ 1.0.5 has a working `--model` flag; the script passes **"Gemini 3.1 Pro (High)"** by default (override with `GEMINI_PDF_MODEL`). If your `agy` build lacks the flag, the model from `~/.gemini/antigravity-cli/settings.json` is used instead. List current model names with `agy models` — they change over time.

### Concurrency note (important)

Concurrent `agy --print` processes hang (verified live on agy 1.0.16). The script serializes every `agy` call under a global lock and wraps each in a watchdog that kills the whole process group on timeout. If you launch several conversions at once they will queue automatically — that is by design, not a bug.

## Install

Via the plugin marketplace (recommended):

```text
/plugin marketplace add hkasahar/claude-code-skills
/plugin install gemini-pdf@kasahara-skills
```

Or copy manually into `~/.claude/skills/gemini-pdf/` (see the repo [README](../README.md)).

## Usage

```bash
GEMINI_PDF="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/gemini-pdf}"
GEMINI_PDF="${GEMINI_PDF:-$HOME/.claude/skills/gemini-pdf}"

# Basic — native pipeline, writes paper.md next to paper.pdf
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf

# Choose output path / pipeline / disable chunking
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf out.md --pipeline text
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-chunk

# Mathpix math-OCR + agy structural cleanup (hybrid)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --hybrid

# Mathpix standalone (works without agy)
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" scan.pdf --force-mathpix
```

In practice you just ask Claude Code ("convert this paper to markdown") and it runs the script. See **[../skills/gemini-pdf/SKILL.md](../skills/gemini-pdf/SKILL.md)** for every flag, environment variable, the chunking/retry strategy, the fallback chain, and the quality-scoring rubric.

Alongside `output.md` the script may write `output.err` (per-chunk agy stderr), `output.meta.json` (per-chunk ledger), `output.quality.json` (quality report), and `output.attempt-<stage>.md` (losing fallback attempts kept for manual override).

## Optional: reference library sync

The skill can additionally file each converted paper into a personal "reference library" (`pdf/`, `md/`, and a `reference.bib`). **This is off by default** and is only useful if you keep such a library.

To enable it, point `GEMINI_PDF_REFERENCE_DIR` at an existing directory:

```bash
export GEMINI_PDF_REFERENCE_DIR="$HOME/research/reference"   # your own path
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --bib-key smith-2020-aer
```

If `GEMINI_PDF_REFERENCE_DIR` is unset (or points to a directory that doesn't exist), the sync step is skipped entirely and nothing is written outside your chosen output path. There is **no default path** — the feature never touches your filesystem unless you opt in. BibTeX metadata comes from a JSON sidecar when present, otherwise from the converted file's own YAML front matter. Copies are skip-if-exists; `--force-sync` overwrites a previously synced (worse) conversion.

## Troubleshooting

| Problem | Fix |
|---|---|
| `agy not found` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash`; ensure `~/.local/bin` is on your `PATH` |
| `Authentication required` from `agy` | run `agy` once interactively and complete the Google sign-in |
| Conversion slow / seems queued | agy calls serialize under a global lock by design (concurrent agy hangs); ~1-4 min per 10-page chunk is normal |
| `ModuleNotFoundError: requests` (Mathpix path) | `pip install requests` |
| Math still garbled | confirm the model (`GEMINI_PDF_MODEL`); try `--hybrid` for Mathpix math OCR |
| `<!-- CHUNK N FAILED -->` in output | that page range failed after retries + bisection; raise `GEMINI_PDF_TIMEOUT` and re-run |
| Scanned PDF, empty/garbled output | the native pipeline reads scans; `--force-mathpix` is the alternative |
| Low quality score | inspect `output.quality.json`; try `--hybrid` or `--force-mathpix` |

More in **[../skills/gemini-pdf/SKILL.md](../skills/gemini-pdf/SKILL.md)**.

---

Model names, flags, and prices change; treat the values here as dated snapshots and verify the current ones for your tools.
