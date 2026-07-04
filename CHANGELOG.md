# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-05

Initial public release.

### Added
- **`delegate` skill** — dispatch heavy or token-expensive tasks from Claude Code to
  Codex CLI (GPT) and Antigravity CLI (Gemini), with an optional Claude CLI third voter.
- Six wrapper scripts: `ask_codex.sh`, `ask_antigravity.sh`, `ask_claude.sh`,
  `ask_both.sh` (parallel cross-verification), `ask_codex_batch.sh` (parallel batch),
  and `preflight.sh` (install/auth check).
- Five compact output-format templates in `skills/delegate/prompts/` for token-efficient
  read discipline (`head -3` on a `STATUS:`/`VERDICT:` header instead of `cat`).
- Packaged as a Claude Code plugin with a marketplace manifest for one-command install.
- Documentation: `docs/SETUP.md` (step-by-step setup for newcomers),
  `docs/WORKFLOW.md` (the orchestrate-and-verify workflow), and `docs/example-CLAUDE.md`
  (an adaptable `CLAUDE.md` template).
