# claude-code-skills

Two reusable skills for research workflows. Keep one agent responsible for the task, give workers bounded inputs, and verify results against original evidence.

| Skill | Capability |
| --- | --- |
| [delegate](skills/delegate/SKILL.md) | File-based delegation to Codex and Antigravity, explicit optional provider wrappers, configurable review, and compact status/provenance reporting. |
| [gemini-pdf](skills/gemini-pdf/SKILL.md) | PDF-to-Markdown conversion with LaTeX math, deterministic quality checks, chunk audits/repair, deadlines, and capped optional Mathpix escalation. |

The [workflow guide](docs/WORKFLOW.md) covers plan review, narrow tasks, source verification, code review, and context handoffs. This distribution contains generic guidance and synthetic tests, without workshop slides, participant data, personal machine configuration, or research manuscripts.

## Install

In Claude Code:

```text
/plugin marketplace add hkasahar/claude-code-skills
/plugin install delegate@kasahara-skills
/plugin install gemini-pdf@kasahara-skills
```

The existing marketplace identifier is retained for installation compatibility. Restart or reload plugins as supported by your client. See [setup](docs/SETUP.md) for prerequisites and manual installation.

From a clone, wrappers can also be called directly by another orchestrator:

```bash
git clone https://github.com/hkasahar/claude-code-skills.git
cd claude-code-skills
DELEGATE="$PWD/skills/delegate"

# Local binary/flag checks; no inference or authentication request.
bash "$DELEGATE/scripts/preflight.sh" --offline

# After authorizing disclosure and configuring the chosen CLI:
bash "$DELEGATE/scripts/ask_codex.sh" @task.md result.md
head -3 result.md

# Default review: Codex + Gemini. Keep the disabled Grok slot empty.
bash "$DELEGATE/scripts/ask_both.sh" @review.md codex.md "" gemini.md
```

Review output slots are **Codex, Grok, Gemini, optional Claude**. Ambiguous older two-output calls are refused. Extra providers require explicit selection; missing evidence or a failed requested leg is not agreement. Model agreement is a diagnostic, without proving correctness or independent errors.

## Convert a PDF

```bash
GEMINI_PDF="$PWD/skills/gemini-pdf"
bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf paper.md \
  --mathpix-page-cap 0 --strict
```

This example disables paid Mathpix escalation. The primary path still sends selected PDF pages to the configured service. Read [PDF setup, statuses, and outputs](docs/gemini-pdf.md) before use. Reference-library synchronization is opt-in, with no personal directory default.

## Privacy and execution boundaries

- Review exact inputs before an external dispatch. Credentials, student records, referee material, restricted data, and unpublished research require their own authorization; installation does not grant it.
- Supply credentials through documented local configuration or environment variables, never prompts or tracked files. Keep outputs, audit traces, usage ledgers, session envelopes, and settings local.
- Agentic workers can execute tools. Separate processes/configurations are not security sandboxes or proof of statistical independence.
- Paid credit fallback, Grok's API backend, and Mathpix require deliberate configuration and approval for the actual dispatch. The review wrapper refuses paid reserve/shadow voters.
- PDF confidentiality notices are warnings, not access control. A spend cap does not make a document appropriate to upload.

Defaults describe the packaged code. Access, prices, models, and CLI compatibility can change; offline tests do not establish live authentication, capacity, or regional availability.

## Development and release checks

```bash
python3 scripts/run_offline_tests.py
python3 scripts/check_release.py
# Immediately before publication, inspect actual staged blobs too:
python3 scripts/check_release.py --staged
```

Tests run in scratch with an allowlisted environment, temporary state, and fail-closed provider/HTTP stubs. The release check covers packaging, local links, and common leakage patterns. Review the exact public tree as well. These checks do not erase earlier commits, forks, or cached copies.

Released under the [MIT License](LICENSE).
