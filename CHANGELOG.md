# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-07-07

### Changed — `delegate` plugin v0.2.0: independent Claude delegate under OAuth

- **`ask_claude.sh` rewritten.** The Claude leg now runs a fully ISOLATED Claude Code
  instance: dedicated config dir (`~/.claude-delegate`, override
  `CLAUDE_DELEGATE_CONFIG_DIR`) with OAuth subscription auth via a `setup-token`-minted
  token file — never the orchestrator's keychain login (an inherited env token is
  accepted only as an explicit, warned fallback), and never a metered API key unless
  opted in (`CLAUDE_DELEGATE_ALLOW_API_KEY=1`). Independence is enforced on four axes
  (config / process-env / auth / cwd); see the new "Independence guarantees" section in
  the skill.
- **New agentic worker mode** (`CLAUDE_DELEGATE_AGENTIC=1` + mandatory
  `CLAUDE_DELEGATE_WORKDIR`): a second Claude works a scoped task with pre-approved
  tools, with `--resume` support for multi-turn follow-ups. Voter mode stays the
  default: zero tools (`--tools ""`, fail-closed), no session persistence, no MCP,
  throwaway cwd.
- **Env-var namespace**: all wrapper knobs moved from `CLAUDE_*` to `CLAUDE_DELEGATE_*`
  (an orchestrating Claude Code session ambiently exports `CLAUDE_EFFORT` and
  `CLAUDE_CODE_*` into shell children, which silently hijacked un-namespaced knobs).
  Legacy names are ignored.
- **Robustness**: `claude -p` exits 0 even on auth failures — the wrapper now parses the
  JSON envelope (`is_error`/`subtype`) on every path (jq → python3 → dependency-free
  grep, all fail-closed); outputs are truncated at dispatch start so a killed run can
  never leave a stale result readable as fresh; token-file permissions self-heal
  (700/600).
- **Timeout defaults corrected** to sit under the Bash tool's real 600 s cap
  (`ask_claude.sh` 570 s; table in the skill now documents the true per-script
  defaults); guidance added for >10-minute dispatches.
- `ask_both.sh`: the Claude leg is pinned to voter mode (an inherited
  `CLAUDE_DELEGATE_AGENTIC`/`RESUME` can no longer corrupt a majority-of-3 vote), and
  leg stdout no longer pollutes the documented output-path contract.
- `preflight.sh`: the Claude probe now mirrors the wrapper's auth env exactly
  (isolated config dir + token file, `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
  stripped) and judges by the JSON envelope, not the exit code.
- Docs: `SETUP.md` Step 6 rewritten for the token-safe one-time setup; sandbox guidance
  corrected (`ask_claude.sh` needs no `dangerouslyDisableSandbox`; `ask_both.sh`
  inherits the Antigravity requirement).
- Verified with 165+ automated checks (offline stub-CLI units, live voter/agentic
  probes, cross-CLI smokes, adversarial isolation tests).

## [0.2.0] — 2026-07-05

### Added
- **`gemini-pdf` skill** — convert PDFs to clean Markdown (LaTeX math, tables, structure)
  via Antigravity CLI, with auto-chunking, a quality-score gate, and an optional Mathpix
  fallback. Install with `/plugin install gemini-pdf@kasahara-skills`.
- `docs/gemini-pdf.md` — setup, usage, and the opt-in reference-library sync.

### Changed
- Repository renamed `claude-code-delegate` → **`claude-code-skills`** (GitHub redirects the
  old URL). Marketplace `kasahara-skills` now lists two independent plugins (`delegate`,
  `gemini-pdf`); the reference-library sync in `gemini-pdf` is opt-in via
  `GEMINI_PDF_REFERENCE_DIR` (no default path).

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
