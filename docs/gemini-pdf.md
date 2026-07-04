# gemini-pdf — PDF → clean Markdown

`gemini-pdf` converts PDFs (especially academic papers) into clean Markdown with reconstructed **LaTeX math**, section hierarchy, tables, theorems, and bibliography. It runs a two-stage pipeline — `markitdown` extracts raw text, then **Antigravity CLI (`agy`)** reformats it — auto-chunks long papers around the model's output limit, scores the result, and can fall back to **Mathpix** for scanned/OCR-heavy PDFs.

> The skill folder is named `gemini-pdf` for backward compatibility with the `/gemini-pdf` command; internally it drives Antigravity (`agy`), Google's successor to the original Gemini CLI.

## Prerequisites

You need Antigravity CLI plus a couple of local tools:

```bash
# 1. Antigravity CLI (the reformatter) — see docs/SETUP.md for the full walkthrough + the curl|bash explainer
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy            # run once to sign in with your Google account, then set the model (see below)

# 2. Text extraction + PDF tooling
pip install markitdown          # default text extractor
brew install poppler            # provides pdftotext (alt extractor) and pdfinfo (page count)

# 3. Only if you use the Mathpix fallback:
pip install requests            # required by the Mathpix path
#   and set MATHPIX_APP_ID / MATHPIX_APP_KEY in your environment
```

If you already followed **[docs/SETUP.md](SETUP.md)** for the `delegate` skill, you have `agy` + Claude Code installed already — you only need `markitdown` and `poppler` here.

### Model setup (one time)

`agy` has no `--model` flag; it reads the model from `~/.gemini/antigravity-cli/settings.json`. For math-heavy papers, set it to **"Gemini 3.1 Pro (High)"** — either interactively via `/model` inside `agy`, or by editing that file. (Model names change; verify the current options in `agy`.)

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

# Basic — writes paper.md next to paper.pdf
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf

# Choose output path / extractor / disable chunking
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf out.md --extractor pdftotext
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --no-chunk

# Scanned PDF (no text layer) — use Mathpix OCR
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" scan.pdf --force-mathpix
```

In practice you just ask Claude Code ("convert this paper to markdown") and it runs the script. See **[../skills/gemini-pdf/SKILL.md](../skills/gemini-pdf/SKILL.md)** for every flag, environment variable, the chunking strategy, and the quality-scoring rubric.

## Optional: reference library sync

The skill can additionally file each converted paper into a personal "reference library" (`pdf/`, `md/`, and a `reference.bib`). **This is off by default** and is only useful if you keep such a library.

To enable it, point `GEMINI_PDF_REFERENCE_DIR` at an existing directory:

```bash
export GEMINI_PDF_REFERENCE_DIR="$HOME/research/reference"   # your own path
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf --bib-key smith-2020-aer
```

If `GEMINI_PDF_REFERENCE_DIR` is unset (or points to a directory that doesn't exist), the sync step is skipped entirely and nothing is written outside your chosen output path. There is **no default path** — the feature never touches your filesystem unless you opt in.

## Troubleshooting

| Problem | Fix |
|---|---|
| `agy not found` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash`; ensure `~/.local/bin` is on your `PATH` |
| `Authentication required` from `agy` | run `agy` once interactively and complete the Google sign-in |
| `markitdown not found` | `pip install markitdown` |
| `ModuleNotFoundError: requests` (Mathpix path) | `pip install requests` |
| Math still garbled | confirm the model is `Gemini 3.1 Pro (High)`; try `--extractor pdftotext` |
| Output truncated on a long paper | it should auto-chunk; lower `GEMINI_PDF_CHUNK_THRESHOLD` |
| `<!-- CHUNK N FAILED -->` in output | a chunk timed out; raise `GEMINI_PDF_TIMEOUT` and re-run |
| Scanned PDF, empty/garbled output | `--force-mathpix` (needs Mathpix credentials + `requests`) |

More in **[../skills/gemini-pdf/SKILL.md](../skills/gemini-pdf/SKILL.md)**.

---

Model names, flags, and prices change; treat the values here as dated snapshots and verify the current ones for your tools.
