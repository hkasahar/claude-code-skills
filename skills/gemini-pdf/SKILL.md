---
name: gemini-pdf
description: Convert PDFs to Markdown through Antigravity CLI with chunk fidelity checks, model audits, repair, deadlines, and capped optional Mathpix OCR.
---

# PDF to Markdown

Use this skill to transcribe PDFs with math, tables, footnotes, or multiple columns. The bundled converter uses `agy` for external model calls. It can use Mathpix when credentials are configured. A successful check is evidence about the transcription, not a guarantee of accuracy.

PDF pages and candidate text leave the machine during model/OCR dispatch. The converter prints a `CONFIDENTIALITY:` notice before its first dispatch to each provider. These notices are **warning only**: they do not request approval, block uploads, or enforce privacy. Obtain any required disclosure authorization before running it. Sidecars and retained audit details can contain document text and paths; store them accordingly.

## Dependencies

Use Python 3.10+, Bash, Perl, `jq`, and Poppler (`pdfinfo`, `pdftotext`, `pdfseparate`, `pdfunite`). Install the Python dependencies in [requirements.txt](requirements.txt) into a suitable environment. `pypdf` is the preferred splitter; Poppler supplies a fallback. `requests` is needed by the Mathpix helper. The optional `markitdown` executable supports the text extraction lane and is not bundled. Configure `agy` and its authentication separately. This package does not install or authenticate providers.

Model identifiers are configurable examples carried by the code, not claims about current availability, pricing, or accuracy. Set `GEMINI_PDF_MODEL` and `GEMINI_PDF_AUDIT_MODEL` to identifiers supported by your CLI. Defaults are `gemini-4-argon-high` and `gemini-3.8-flash-high`. The identity probe sends `/model`, without document content; provider availability still requires a live check. No zero-cost conversion is promised.

## Run

Resolve `SKILL_DIR` to this installed skill directory, then:

```bash
bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf output.md
bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf output.md --strict
bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf --no-audit --no-fallback
bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf --pipeline text
```

The default native lane splits PDFs into chunks of at most 10 pages, runs calls sequentially under a global lock, and uses a process-group watchdog. Failed/truncated conversions retry and can bisect down to individual pages. The default maximum input is 500 pages.

Each native chunk receives a deterministic fidelity gate and a model audit against its PDF pages. A failed chunk may receive a repair or fresh conversion; repeated truncation can bisect it. Remaining failures may use chunk-scoped Mathpix under the cap. The best available attempt retains explicit uncertainty/failure markers. Empty or denied audits never count as a pass. Text, hybrid, Mathpix, and `--no-audit` lanes use the legacy quality/fallback route; they do not provide native chunk audits.

The deterministic gate checks content/numeral recall, per-page coverage, missing sentences, injected DOIs, math/OCR, and uncertainty markers. Floors can be configured with `GEMINI_PDF_CHUNK_FLOORS`. With an absent or unusable PDF text layer, source-relative checks are unavailable and the report sets `det_weak=true`; model review then carries more of the fidelity assessment. Model audits can miss defects or raise false findings. Review important equations and claims against the original PDF.

## Outputs and exit status

Stdout contains only the resulting Markdown path. Logs and the final `AUDIT:` summary go to stderr. Sidecars include `.err`, `.meta.json`, `.quality.json`, and, for audited native runs, `.audit.json`. Audit details are retained for nonverified runs or when `GEMINI_PDF_KEEP_AUDIT_DIR=1`.

| Document status | Meaning | Default exit | `--strict` exit |
| --- | --- | --- | --- |
| `verified` | All chunks passed the configured gate and audit | 0 | 0 |
| `degraded` | An audit or another step was unavailable/skipped | 0 | 3 |
| `partial` | At least one retained chunk is unverified | 0 | 4 |
| `failed` | A chunk failed or the run aborted | 2 | 2 |
| `disabled` | Audit route disabled | 0 on successful conversion | 0 on successful conversion |

Usage/environment errors return 1. A failed audited run publishes `output.failed.md`, leaves no successful `output.md`, and reports its actual state. An existing destination is preserved as `output.md.pre-<run_nonce>`. A degraded or partial exit 0 does not mean every chunk verified: inspect the report or use strict mode. Audit reports use temporary-file replacement; failure recovery retains the best available artifact and records missing evidence.

## Deadline and paid OCR controls

The audit-mode default deadline is 3300 seconds, including lock waits. Each external call is clamped to the remaining budget, with 120 seconds reserved for publication. Optional steps skip when their minimum budgets are unavailable; mandatory conversion without sufficient budget fails its chunk. Skips appear in the Markdown, report, and summary. `--no-audit` enforces a deadline only when explicitly configured.

Mathpix can incur charges whenever credentials are set and a permitted lane reaches it, including automatic fallback. `--force-mathpix` and `--hybrid` also obey the cap. To disable Mathpix dispatch:

```bash
bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf --mathpix-page-cap 0
```

The default cap is 20 pages per document across a 24-hour ledger window. Reservations occur before dispatch, and failed/ambiguous uploads consume their reservation. Unreadable spend ledgers block automatic Mathpix use. Each guarded upload uses `--max-attempts 1`; calling `mathpix_extract.py` directly bypasses the converter's cap and ledger. The standalone helper defaults to three upload attempts. Use the converter when you need those spend controls.

| Setting / flag | Default / purpose |
| --- | --- |
| `GEMINI_PDF_MATHPIX_PAGE_CAP`, `--mathpix-page-cap N` | 20; 0 disables dispatch |
| `GEMINI_PDF_MATHPIX_LEDGER` | `$HOME/.claude/delegate-logs/mathpix_ledger.tsv` |
| `GEMINI_PDF_MATHPIX_CAP_WINDOW_H` | 24 hours |
| `GEMINI_PDF_MATHPIX_USD_PER_PAGE` | 0.025, a placeholder estimate; verify your rate |
| `MATHPIX_APP_ID`, `MATHPIX_APP_KEY` | Required for Mathpix; never bundled |
| `GEMINI_PDF_DEADLINE`, `--deadline S` | 3300 seconds in audit mode |
| `GEMINI_PDF_REPAIR_ROUNDS`, `--repair-rounds N` | 1 |
| `GEMINI_PDF_AUDIT_MAJOR_MAX` | 4; critical findings always fail |
| `GEMINI_PDF_STRICT=1`, `--strict` | Distinguishes degraded/partial exits |

The Mathpix client rejects redirects, verifies TLS, and disables environment proxy/netrc inheritance. Its `MATHPIX_API_BASE` override exists for explicit alternative origins and localhost contract tests; it must be trusted because credentials are sent there.

## Optional reference sync

No reference library is inferred or written by default. Opt in with an explicit existing destination:

```bash
GEMINI_PDF_REFERENCE_DIR=/path/to/reference \
  bash "$SKILL_DIR/scripts/pdf_to_markdown.sh" paper.pdf output.md --bib-key sample2026
```

`REFERENCE_DIR` is a compatible explicit alternative; `GEMINI_PDF_REFERENCE_DIR` takes precedence when nonempty. With a BibTeX key, the sync helper copies PDF/Markdown and adds metadata to `reference.bib`; without a key, it copies Markdown only when the destination `md/` exists. Existing copies are kept unless `--force-sync` is selected. Degraded and partial results can sync, so use strict mode and inspect statuses before ingestion. Failed audited results do not sync. The standalone helper requires `--central-ref`, `GEMINI_PDF_REFERENCE_DIR`, or `REFERENCE_DIR` and refuses a missing destination.

## Offline verification

```bash
bash "$SKILL_DIR/tests/run_offline_suite.sh" --unit
bash "$SKILL_DIR/tests/run_offline_suite.sh"
```

Unit tests also require the dependencies in `requirements-dev.txt` (`jsonschema` plus the runtime dependencies). Select a configured interpreter with `GEMINI_PDF_TEST_PYTHON`; its environment path is preserved. `OFFLINE_TEST_PYTHONPATH` explicitly selects existing dependency directories for read-only imports when needed; ambient `PYTHONPATH` is never inherited.

The launcher copies the package to scratch, clears inherited credentials/configuration, creates temporary home/cache/reference/ledger roots, supplies a restricted local-tool PATH, and uses recording provider stubs. Python sockets are restricted to loopback for the Mathpix HTTP contract tests. The 25-page fixture and CLI envelopes are synthetic. Fixture compilation requires `pdflatex` with common LaTeX packages; it disables shell escape. Tests establish local behavior, not live provider authentication, availability, or transcription accuracy. Nothing in the package invokes MinerU or downloads model weights.
