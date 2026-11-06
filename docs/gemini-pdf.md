# PDF to Markdown with audits

The converter sends page-range PDFs to the selected external model service, checks deterministic fidelity indicators, audits chunks, attempts bounded repair, and marks unresolved results. Neither a passing score nor an AI audit establishes perfect transcription. Its confidentiality messages are warnings, without blocking an upload; authorize the actual document disclosure first.

## Prerequisites and installation

Use an authenticated compatible `agy`, Python 3, Poppler, and pypdf. Mathpix additionally uses requests and your own credentials; the legacy text pipeline can use optional markitdown. See [setup](SETUP.md) and the public [requirements](../skills/gemini-pdf/requirements.txt). Local model spikes, caches, and weight manifests are excluded.

Install with `/plugin install gemini-pdf@kasahara-skills` after adding the marketplace, or use the scripts in a clone. The [skill entrypoint](../skills/gemini-pdf/SKILL.md) describes the full interface.

## Conservative first run

```bash
GEMINI_PDF="$PWD/skills/gemini-pdf"
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf paper.md \
  --mathpix-page-cap 0 --strict
```

The page cap of zero disables Mathpix dispatches, including paid escalation. The native model path is still external. Output goes to the requested path; stdout reports the produced Markdown path. Use `--audit-model`, `GEMINI_PDF_MODEL`, and `GEMINI_PDF_AUDIT_MODEL` only after checking supported IDs. Configured IDs and offline fixtures do not prove live availability.

Conversion calls are serialized under a shared lock and bounded by watchdogs. A deadline can skip audit/repair stages; skipped work remains visible in the final report. The audited default deadline is 3300 seconds with a 120-second publication reserve. Long jobs need an appropriate execution and cost plan.

## Read the status before ingesting

| Result | Default exit | Strict exit | Meaning |
| --- | --- | --- | --- |
| verified | 0 | 0 | Packaged checks completed without unresolved findings |
| degraded | 0 | 3 | At least one audit/check is unavailable or skipped |
| partial | 0 | 4 | Unresolved chunks remain and are marked |
| failed | 2 | 2 | A chunk/run failed; the intended normal output is not trustworthy |
| disabled audit | 0 | 0 | Audit was deliberately disabled; do not describe it as verified |

Usage/environment failures use exit 1. Default exit zero does not imply verified status. On audited failure, best effort is written as `output.failed.md`; a pre-existing normal output is preserved under a run-specific `.pre-...` name. The stdout path may therefore name failed output. Inspect `output.audit.json` and residual markers.

Sidecars can include `.err`, `.meta.json`, `.quality.json`, `.audit.json`, and a detail directory for nonverified chunks. These may contain document text, paths, model responses, and operational data; keep them local unless separately reviewed for release.

## Optional paid OCR

Mathpix escalation requires credentials. The default page cap is 20 per document over the ledger's 24-hour window; zero disables it. Reservations count dispatches even when their final outcome is uncertain, preventing a failed request from silently freeing spend allowance. A cap is a limit, not consent.

The reported per-page dollar amount is an estimate controlled by configuration, not a current price quote. Use the provider's current terms, approve the actual upload/cost, and inspect the ledger and report. Explicit Mathpix/hybrid modes still pass through the page-cap guard.

## Optional local reference synchronization

There is no personal library default. Set an explicit destination and a citation key only if you want a local library write:

```bash
export GEMINI_PDF_REFERENCE_DIR="$HOME/reference-library"
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf paper.md \
  --bib-key example2026 --mathpix-page-cap 0
```

`REFERENCE_DIR` is an alternative explicit setting. Without a destination, the sync helper refuses to infer one. Inspect existing entries before selecting `--force-sync`. Strict mode changes exit codes; it does not by itself prevent a requested synchronization of partial/degraded output. Consumers requiring verified-only ingestion must inspect the audit status and control sync accordingly.

## Tests

The [isolated repository launcher](../scripts/run_offline_tests.py) uses synthetic fixtures and fail-closed external-command stubs. Unit tests cover audit parsing/scoring, page accounting, failed outputs, and opt-in synchronization; offline scenarios exercise conversion/repair/deadline/cap behavior. No model inference, document upload, paid OCR, or private library is required.
