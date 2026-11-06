---
name: delegate
description: Dispatch bounded tasks and fresh reviews through external Codex, Gemini, Grok or Claude wrappers, with explicit cost choices and provenance.
---

Use this skill for a self-contained implementation task, independent review, or a mechanical transformation that benefits from another model. Keep tightly coupled work in the main session. A second run is another source of evidence; it does not establish statistical independence or certify correctness.

Before dispatch, state the task, allowed files, required output and deadline. Check what text, files and credentials the selected client can access. Obtain permission before disclosing sensitive material to an external provider or using a metered route when that use has not already been authorized. A prompt restriction is not a filesystem or network sandbox.

Resolve scripts relative to this skill's installation directory. Do not assume a particular home directory or marketplace cache path. Start with `bash scripts/preflight.sh --offline`: it checks binaries and flags only, without auth, model-catalog, quota, credit or inference probes. `--live` explicitly runs small provider requests and may use quota or incur charges. Offline success does not prove authentication, capacity, model availability or live correctness.

For one task:

```bash
bash scripts/ask_codex.sh @task.md result.md
bash scripts/ask_antigravity.sh @review.md review.md
```

The single Codex wrapper defaults to `gpt-6.1-sol`/`xhigh`; change `CODEX_MODEL` and `CODEX_EFFORT` deliberately. Gemini defaults to `gemini-4-argon-high`; configure `ANTIGRAVITY_MODEL` or `--model`. These are configurable code defaults, not availability or ranking claims. Read each metadata header and terminal status; transport success alone does not prove task success.

For a fresh review round, include the output format from [compact_verify.txt](prompts/compact_verify.txt) in a neutral, self-contained prompt:

```bash
bash scripts/ask_both.sh @review.md codex.md "" gemini.md
INCLUDE_GROK=1 bash scripts/ask_both.sh @review.md codex.md grok.md gemini.md
```

Default legs are Codex and Gemini. The review Codex pin is `gpt-6-astra`/`max`, configurable independently of the single-wrapper default. Slots are always query, Codex output, Grok output, Gemini output, optional Claude output. Leave the Grok slot empty when disabled. Ambiguous calls with exactly two output arguments are refused before dispatch. Requested legs must have distinct outputs; stale outputs are removed before the new round.

Grok is explicit opt-in (`INCLUDE_GROK=1`). Claude is explicit opt-in (`INCLUDE_CLAUDE=1` and the fifth output slot), an investigation aid never silently counted as independent. Retired DeepSeek reserve/shadow switches are refused; no paid credit fallback or Grok API backend is allowed in a review round. Any requested transport failure or malformed review output fails the round. `ERROR` or `GAP` is a substantive finding, not a transport failure. Read every report and resolve each concern from assumptions, original sources, counterexamples or reproducible checks.

`ask_codex.sh` credit fallback is **off** unless supplied as `--credit-fallback deepseek` (Codex harness) or `--credit-fallback deepseek-api` (self-contained API task). An environment variable is not consent. These routes require separate OpenRouter setup, bounded key credit and provider/preset checks; they may incur charges. Unknown or mid-run tool history prevents automatic replay. Max/ultra requests refuse fallback. See [wrapper modes](references/legs.md) before opting in.

For PDF conversion, [ask_pdf2md.sh](scripts/ask_pdf2md.sh) resolves a bundled sibling `gemini-pdf` converter before a home-directory fallback; `PDF2MD_CONVERTER` overrides both. It sends the document to external conversion services and prints a confidentiality warning. That warning is disclosure, not an enforcement boundary. Reference-library sync is opt-in with `--bib-key`; no reference-search or private integration is bundled. Read the compact status envelope and audit sidecars, especially partial/failed results and paid OCR page counts.

Use [review guidance](references/proof-audit.md) for proof-sensitive work. Use [wrapper modes](references/legs.md) for dependencies, credentials, deadlines, provenance and optional agentic modes. Run the synthetic tests through the repository's isolated offline test launcher; no live inference is required to verify the packaged contracts.
