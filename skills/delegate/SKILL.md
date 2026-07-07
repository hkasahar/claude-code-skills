---
name: delegate
description: >-
  Delegate heavy or token-expensive work from Claude Code to Codex CLI (GPT) and
  Antigravity CLI (Gemini), plus an independent Claude Code delegate (isolated config
  dir, OAuth subscription auth) usable as a 3rd voter or agentic worker. Use when a task is
  large, parallelizable, or benefits from cross-model verification: dispatch a
  self-contained prompt to an external CLI, write its output to a file, and read only the
  STATUS/VERDICT header to keep the main context clean. Good for code implementation,
  debugging, refactoring, proof and derivation checking, literature review, and any
  second-opinion cross-check. Triggers include "delegate to Codex/Antigravity",
  "cross-verify with ask_both", "run this in parallel", and "save tokens".
---

# Delegate Skill

Orchestrate token-efficient workflows by delegating heavy tasks to **Codex CLI** (`codex`) and **Antigravity CLI** (`agy`), which run on separate subscription quotas at ~$0 marginal cost. An independent **Claude Code delegate** (via `ask_claude.sh`) runs under an isolated config dir (`~/.claude-delegate`) on OAuth subscription auth, providing a 3rd voter for majority-of-3 vote flows and an agentic worker mode symmetric with the Codex leg.

The core discipline: dispatch a self-contained prompt to an external CLI, write its output to a file, then read only the first few lines (a `STATUS:`/`VERDICT:` header) instead of pasting the whole result into context. On success this costs ~15–50 tokens instead of thousands.

## Prerequisites

- **Antigravity CLI** installed and authenticated (`agy` on PATH; install: `curl -fsSL https://antigravity.google/cli/install.sh | bash`)
- **Codex CLI** installed and authenticated (`codex` on PATH; install: `npm install -g @openai/codex`)
- **Claude Code CLI** installed. Only needed for `ask_claude.sh` (3rd voter / agentic worker). The delegate runs under an ISOLATED config dir (`~/.claude-delegate` by default, override: `CLAUDE_DELEGATE_CONFIG_DIR`), independent of your main session's config (default `~/.claude`). One-time setup — **run in a regular terminal, NOT via `!` inside a Claude Code session** (the long-lived token must never enter argv, shell history, or a session transcript, which memory plugins may index):

  ```bash
  CLAUDE_CONFIG_DIR=~/.claude-delegate claude setup-token   # browser flow, prints an sk-ant-oat… token
  umask 077
  cat > ~/.claude-delegate/oauth_token    # paste token, Enter, Ctrl-D
  ```

  Rationale (verified 2026-07-07 on Claude Code 2.1.198): `--bare` **cannot** be used for OAuth delegation — bare-mode auth is strictly `ANTHROPIC_API_KEY`/apiKeyHelper ("OAuth and keychain are never read") — so the wrapper passes an explicit `CLAUDE_CODE_OAUTH_TOKEN` env token (honored without `--bare`; takes precedence over keychain credentials) and replicates `--bare`'s hygiene mechanically: fresh config dir (no hooks/plugins/memory/user CLAUDE.md), throwaway mktemp cwd in voter mode, `--tools ""`, `--no-session-persistence`. Authenticating `~/.claude-delegate` against a second subscription (optional) isolates quota from your main session; with the same account, config/process/cwd isolation still holds but quota is shared. (Env-token auth also sidesteps a macOS caveat: `CLAUDE_CONFIG_DIR` does not namespace the macOS Keychain, so keychain-based logins may be shared across profiles.)
- All CLIs must support non-interactive/headless mode.

New here? See [`docs/SETUP.md`](../../docs/SETUP.md) for step-by-step install and authentication, and [`docs/WORKFLOW.md`](../../docs/WORKFLOW.md) for how this skill fits the orchestrate-and-verify workflow.

## Locating the scripts

Every example below calls scripts through `$DELEGATE`. Set it once per shell so the examples work whether the skill was installed as a plugin or copied manually:

```bash
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  DELEGATE="$CLAUDE_PLUGIN_ROOT/skills/delegate"   # installed as a Claude Code plugin
else
  DELEGATE="$HOME/.claude/skills/delegate"          # installed by manual copy
fi
```

Verify before first use:
```bash
bash "$DELEGATE/scripts/preflight.sh"   # checks agy + codex (required) + claude (optional)
ls "$DELEGATE/prompts/"                 # compact_code.txt, compact_verify.txt, result_only.txt, ...
```

### Antigravity sandbox note

`agy` binds a localhost TCP port for its in-process language server. Under Claude Code's default sandbox this is blocked with `bind: operation not permitted`. The `ask_antigravity.sh` wrapper detects this and synthesizes a clear `STATUS: FAILED — Sandbox blocked agy TCP bind` line. To actually run `agy`, callers must either:

1. Pass `dangerouslyDisableSandbox: true` on the Bash tool call, or
2. Run from a shell outside the Claude Code sandbox.

`claude -p` (via `ask_claude.sh`) does **not** need the sandbox disabled: auth is an explicit env token (no keychain), and full voter + agentic dispatches (including `~/.claude-delegate` session writes and `--resume`) were verified working under the default sandbox on 2026-07-07.

> **⚠️ Security — read before disabling the sandbox.** `dangerouslyDisableSandbox: true` lets the delegation CLI reach the network and your credential store (it needs both to authenticate and call the model API). Only disable the sandbox for these specific delegation calls — never as a blanket setting — and never delegate a prompt built from untrusted input. Also note that **delegating sends your prompt, and any file content you paste into it, to the external provider** (OpenAI for Codex, Google for Antigravity). Do not delegate confidential, embargoed, or privacy-sensitive material. See `docs/SETUP.md` for the full security checklist.

---

## Models & Reasoning Effort

CLI interfaces change quickly. **Re-verify quarterly** via `codex --version` / `codex debug models`, `agy --version`, and `claude --version`; update the defaults below if the resolved model identifier in recent session logs has moved.

| CLI | Default model (verified 2026-05-28) | Effort tiers | Effort knob | Override env var(s) |
|---|---|---|---|---|
| Codex | `gpt-5.5` | `low \| medium \| high \| xhigh` (per `codex debug models`; `minimal` is **not** in the `gpt-5.5` catalog) | `-c model_reasoning_effort=xhigh` (passed via `-c` config override on `codex exec`) | `CODEX_MODEL`, `CODEX_EFFORT` |
| Antigravity | `gemini-3.1-pro` (logically — see note) | n/a | **none exposed at CLI level** (agy v1.0.0 has NO `--model` flag); model is configured via `~/.gemini/antigravity-cli/settings.json` or the `agy /model` interactive command | `ANTIGRAVITY_MODEL` (informational only — recorded in metadata header; **not** passed to the binary) |
| Claude Code (delegate: 3rd voter + agentic worker; verified 2026-07-07) | `opus` alias → latest Claude Opus (4.8 as of 2026-07) | `low \| medium \| high \| xhigh \| max` | `--effort max` if the installed CLI advertises the flag; otherwise omitted (feature-detected via `claude --help`) | `CLAUDE_DELEGATE_MODEL`, `CLAUDE_DELEGATE_EFFORT`, `CLAUDE_DELEGATE_CONFIG_DIR` |

### Default invocation = best model, max effort

`ask_codex.sh` always pins effort to `xhigh` and uses `gpt-5.5` as the model. No flag is needed to "go strongest" — that is the default. **The pinning is intentional**: the script unconditionally passes `-c model_reasoning_effort="$EFFORT"` (with `EFFORT=${CODEX_EFFORT:-xhigh}`), which **overrides whatever `~/.codex/config.toml` says**. Per-call overrides via `CODEX_EFFORT=…` and `CODEX_MODEL=…` are still respected.

`ask_antigravity.sh` does **not** pass a `--model` flag (agy v1.0.0 doesn't have one). The model is whatever the user has set in `~/.gemini/antigravity-cli/settings.json`. For math-heavy / proof-verification workloads, set the model to `Gemini 3.1 Pro (High)` via the interactive `agy /model` command. `ANTIGRAVITY_MODEL` is recorded in the wrapper's metadata header for provenance but is not passed to the binary.

`ask_claude.sh` defaults to model `opus` (resolves to the latest Claude Opus) with `--effort max` (feature-detected). Voter mode disables all tool use with `--tools ""` — verified present on 2.1.198 and **fail-closed**: the wrapper hard-errors rather than dispatch a vote if a future CLI drops the flag — plus `--permission-mode dontAsk` as a backstop (`dontAsk` alone would still allow read-only tools), `--strict-mcp-config` (MCP tools are NOT governed by `--tools ""`), `--no-session-persistence`, and `--max-turns 1`. It does **not** pass `--bare` (which would break OAuth auth — see Prerequisites); context-freedom comes from the fresh delegate config dir and a throwaway mktemp cwd. Wrapper knobs use the `CLAUDE_DELEGATE_*` namespace because an orchestrating Claude Code session ambiently exports `CLAUDE_EFFORT` (and other `CLAUDE_CODE_*` vars) into every Bash child — un-namespaced knobs would silently inherit the orchestrator's values. Because the voter has zero file access, it will **confabulate** plausible-looking content (even fake tool transcripts) if asked about local files — always paste the material to be judged directly into the prompt; never ask the voter to "read" anything.

### Independence caveat (3rd voter)

The Claude 3rd voter shares the model family with the orchestrating Claude Code session. Vote independence is mechanically preserved (3 distinct dispatches), but **statistically reduced** vs. using a third model from a different provider. Use this for mechanical disagreement detection, not as a true triangulation against the orchestrator's biases.

### Independence guarantees (Claude-from-Claude)

`ask_claude.sh` enforces independence on four axes:

1. **Config**: `CLAUDE_CONFIG_DIR=~/.claude-delegate` — own auth, settings, session store. No contention with the orchestrating session; optionally a separate subscription. Keep this dir free of `CLAUDE.md`/memory (the wrapper WARNs if one appears).
2. **Process**: ALL `CLAUDECODE*`/`CLAUDE_CODE_*` harness vars are unset in the child — an orchestrating session ambiently exports `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_EFFORT_LEVEL`, and more (measured 2026-07-07), which would otherwise alter the delegate's behavior. `CLAUDE_DELEGATE_DEPTH` blocks delegate-of-delegate recursion (depth ≥ 1 fails fast).
3. **Auth**: explicit env token, priority token file > inherited `CLAUDE_CODE_OAUTH_TOKEN` (stderr WARN — orchestrator's token, independence degraded) > `ANTHROPIC_API_KEY` **opt-in only** (`CLAUDE_DELEGATE_ALLOW_API_KEY=1`; an orchestrating session exports a metered API key ambiently, so an unguarded fallback would silently bill the API). Per the documented CLI auth precedence, `ANTHROPIC_API_KEY` **outranks** `CLAUDE_CODE_OAUTH_TOKEN` (and `ANTHROPIC_AUTH_TOKEN` outranks both), so the wrapper unsets `ANTHROPIC_API_KEY` whenever an OAuth token is selected and drops `ANTHROPIC_AUTH_TOKEN` unconditionally — subscription billing, never metered. An empty token file is a hard error, not a fallback.
4. **Cwd**: voter mode runs in a throwaway `mktemp -d` (no project CLAUDE.md pickup, no session-store collisions). Agentic mode requires an explicit `CLAUDE_DELEGATE_WORKDIR` (no `$PWD` fallback). **Honest scope note**: the workdir bounds file tools only — an allowed `Bash` tool is NOT jailed to it, and `--add-dir` widens Edit/Write scope; `dangerouslyDisableSandbox: true` on the dispatch removes the last containment layer. Treat agentic prompts as trusted code. The delegate token is readable by the delegatee by design (same user/host).

The statistical caveat above stands: config/process/auth/cwd independence is mechanical, not statistical.

### Claude delegate environment variables

All knobs live in the `CLAUDE_DELEGATE_*` namespace (legacy `CLAUDE_MODEL`/`CLAUDE_EFFORT`/`CLAUDE_TIMEOUT`/`CLAUDE_AGENTIC`/… are **ignored** — an orchestrating session ambiently exports `CLAUDE_EFFORT` etc. into Bash children, which would silently hijack un-namespaced knobs):

| Var | Default | Meaning |
|---|---|---|
| `CLAUDE_DELEGATE_CONFIG_DIR` | `~/.claude-delegate` | Child's `CLAUDE_CONFIG_DIR` |
| `CLAUDE_DELEGATE_MODEL` | `opus` | Model alias/name |
| `CLAUDE_DELEGATE_EFFORT` | `max` | Effort tier (feature-detected `--effort`) |
| `CLAUDE_DELEGATE_TIMEOUT` | `570` | Internal timeout, seconds — deliberately under the Bash tool's 600s default cap so the wrapper's timeout classification fires before a harness kill (see Timeout Configuration) |
| `CLAUDE_DELEGATE_AGENTIC` | `0` | `1` = agentic worker; else voter |
| `CLAUDE_DELEGATE_WORKDIR` | — | **Required in agentic mode** (hard error if unset) |
| `CLAUDE_DELEGATE_ALLOWED_TOOLS` | `Read,Grep,Glob,Edit,Write,Bash` | Agentic `--allowedTools` |
| `CLAUDE_DELEGATE_MAX_TURNS` | 1 voter / 40 agentic | Turn cap; quota guardrail. Applied via a runtime parser probe — `--max-turns` exists on 2.1.198 but is **hidden from `--help`**. Wrapper WARNs if a future CLI drops the flag |
| `CLAUDE_DELEGATE_RESUME` | unset | Session ID for `--resume`; **agentic-only** (hard error in voter mode) |
| `CLAUDE_DELEGATE_ADD_DIRS` | unset | Colon-separated extra `--add-dir` paths (agentic) |
| `CLAUDE_DELEGATE_ALLOW_API_KEY` | `0` | `1` permits metered `ANTHROPIC_API_KEY` fallback |

### When to override the defaults

- **Cheaper / faster verifications**: `CODEX_EFFORT=high bash ask_codex.sh ...` (drops one tier).
- **Probing alternative models**: `CODEX_MODEL=gpt-5.4 bash ask_codex.sh ...`.
- **Switch Antigravity model**: edit `~/.gemini/antigravity-cli/settings.json` (model field is a display string like "Gemini 3.1 Pro (High)"); the wrapper does not change this.
- **Switch Claude model**: `CLAUDE_DELEGATE_MODEL=sonnet bash ask_claude.sh ...` (faster/cheaper alternative for the 3rd voter; not recommended for high-stakes votes).

### How to refresh this section

```bash
codex --version
codex debug models | jq '.[] | {id: .id, reasoning_efforts: .reasoning_efforts}'
agy --version
cat ~/.gemini/antigravity-cli/settings.json | jq '.model'   # current Antigravity model
claude --version
ls -t ~/.codex/sessions/$(date +%Y)/$(date +%m)/ | head -1   # latest Codex session — open and check resolved model
```

Note: `codex exec --help` does **not** list `model_reasoning_effort` — the value is consumed via `-c` config overrides and `config.toml`, not as a dedicated flag. On `codex exec`, the approval policy flag `-a/--ask-for-approval` is **not accepted** (it lives on root `codex`). Approval behaviour on `exec` comes from `~/.codex/config.toml` (`approval_policy="never"` is the operational default) or via `-c approval_policy=never` override.

---

## Delegation Rules

### Delegate to Antigravity (`scripts/ask_antigravity.sh`) when:
- Literature review, scooping check, citation verification
- Proof or derivation verification (second opinion)
- Summarizing or critiquing long documents or papers
- Any task that is mostly reading + reasoning, not code execution
- Tasks benefiting from large context window (1M+ tokens)
- Deep research queries (use daemon for multi-step; CLI for single-pass)

### Delegate to Codex (`scripts/ask_codex.sh`) when:
- **Code implementation** — writing new functions/scripts from spec (`compact_implement.txt`)
- **Debugging or refactoring** R/Python/Julia code (`compact_code.txt`)
- **Running and iterating** on Monte Carlo simulations (`compact_code.txt`)
- **Building or fixing** estimation routines (`compact_code.txt`)
- **Data wrangling** — CSV/JSON/Parquet manipulation (`result_only.txt`)
- **Shell scripts and automation** (`result_only.txt`)
- **Code review** (assist — Claude Code owns final verdict) (`compact_review.txt`)
- **Test generation** (`compact_code.txt`)
- **Mechanical LaTeX** — booktabs tables, pgfplots, TikZ (`result_only.txt`)
- **Regex, SQL, awk construction** (`result_only.txt`)

### Delegate to Claude agentic worker (`CLAUDE_DELEGATE_AGENTIC=1 ask_claude.sh`) when:
- The task needs Claude-quality reasoning AND file access, but not the orchestrator's context (e.g., self-contained LaTeX refactors, test suites, formalization in a scratch repo)
- Parallel Claude workers are wanted on a second subscription's quota
- Codex is quota-exhausted and the task is code-heavy

```bash
# Isolated workdir is REQUIRED (use a git worktree for parallel dispatches)
git -C ~/proj worktree add /tmp/wt-fix HEAD
CLAUDE_DELEGATE_AGENTIC=1 CLAUDE_DELEGATE_WORKDIR=/tmp/wt-fix \
  bash "$DELEGATE/scripts/ask_claude.sh" @task.md result.md
head -3 result.md    # metadata + STATUS; session= in the header enables follow-ups
```

Multi-turn follow-up (must use the same workdir; session lookup is cwd-scoped):

```bash
SID=$(sed -n 's/.*session=\([^ ]*\).*/\1/p; 1q' result.md)
CLAUDE_DELEGATE_AGENTIC=1 CLAUDE_DELEGATE_WORKDIR=/tmp/wt-fix CLAUDE_DELEGATE_RESUME=$SID \
  bash "$DELEGATE/scripts/ask_claude.sh" "Now add unit tests" result2.md
```

Treat agentic prompts as trusted code: the worker runs `--permission-mode acceptEdits` with pre-approved tools, and an allowed `Bash` is not jailed to the workdir (see Independence guarantees, axis 4). Keep in Claude Code (do NOT delegate to the agentic worker): anything needing the orchestrator's accumulated context, MEMORY.md/CLAUDE.md-dependent edits, secrets, or destructive operations — same exclusions as the "Keep in Claude Code" list below.

### Keep in Claude Code when:
- Orchestration decisions (what to delegate, what to do next)
- Final integration of results from Codex/Antigravity
- Editing LaTeX where project context (CLAUDE.md, MEMORY.md) matters
- Short, context-dependent tasks where re-explaining the project costs more than just doing it
- Tasks requiring access to project-specific skills you've installed
- Multi-file changes requiring iterative test/log feedback loops
- Security-sensitive code changes needing local review
- Destructive/stateful operations (migrations, deploys, writes to prod-like data)
- Secrets/credential-bound tasks
- Tasks where project context (MEMORY.md, CLAUDE.md, git history) is essential

### Delegate to BOTH (cross-verify) when:
- Verifying mathematical proofs or derivations (get two independent opinions)
- Checking identification arguments
- Any high-stakes claim where a second opinion adds value

---

## Output Format Templates

**Critical for token efficiency.** Injecting full Codex/Antigravity output into context via `cat` bloats the context window permanently, increasing input tokens on every subsequent turn. Use compact formats by default and read selectively.

### Template 0: Result only (maximum compression)

Use `$DELEGATE/prompts/result_only.txt` when you want only the corrected artifact with zero reasoning. Best for: applying a known fix, generating a replacement code block, producing a revised LaTeX paragraph.

Read with:
```bash
head -3 result.md          # metadata header + FAILED sentinel line (line 3) if any; otherwise paste the body
```

### Template 1: Compact code fix (default for routine Codex tasks)

Use `$DELEGATE/prompts/compact_code.txt` for routine code fixes where you need a status summary + changed blocks only.

Read with:
```bash
head -3 result.md          # ~15 tokens; read full output only if STATUS != DONE
```

### Template 2: Compact verification (proof/derivation/identification checks)

Use `$DELEGATE/prompts/compact_verify.txt` for verification tasks. Returns a VERDICT line + specific error locations if any.

Read with:
```bash
head -3 result.md          # ~20 tokens; full output only if VERDICT != CORRECT
```

### Template 4: Compact implementation (new code from spec)

Use `$DELEGATE/prompts/compact_implement.txt` for writing new functions, scripts, or modules from a specification. Returns STATUS + FILES list + complete code.

Read with:
```bash
head -3 result.md          # STATUS + FILES; read code only if STATUS == DONE
```

### Template 5: Compact review (code audit)

Use `$DELEGATE/prompts/compact_review.txt` for code review/audit. Returns VERDICT + issue list. **Claude Code owns the quality gate** — Codex assists but doesn't make final accept/reject decisions.

Note: `compact_review.txt` uses VERDICT/PASS/ISSUES/CRITICAL vocabulary (distinct from `compact_verify.txt`'s CORRECT/ERROR/GAP). This is intentional — code review and proof verification are different domains with different severity semantics. The read discipline is the same: `head -3`, escalate to `cat` on non-PASS/non-CORRECT verdicts.

Read with:
```bash
head -3 result.md          # VERDICT line; cat only if VERDICT != PASS
```

### Template 3: Full output (debugging unknown errors, first-pass diagnosis)

No format constraint. Use `cat result.md`. Reserve for:
- First attempt at a non-trivial bug where you need Codex's reasoning trace
- Coverage failures or simulation blow-ups with unknown cause
- Any task where the diagnosis itself is the deliverable

---

## Read Discipline

**Default**: `head -3` for all delegation outputs. This reads the metadata header + first content line (~15-20 tokens).

**Escalate to `cat`** only when:
- STATUS is FAILED or PARTIAL
- VERDICT is ERROR or GAP
- The task is a first-pass diagnosis (Template 3)
- The output is a literature survey you need to ingest

**Never** `cat` a successful compact result. The STATUS/VERDICT line tells you everything you need.

`ask_claude.sh` provenance: the output body is the parsed `.result` from `--output-format json`; the metadata comment records `mode= model= effort= auth= session= cost_usd= turns=`. The raw JSON envelope is preserved at `${output%.md}.json` for forensics — never `cat` it on success. (`auth=token-file` confirms subscription billing; `cost_usd` is informational only.)

---

## Usage Patterns

### Pattern 1: Antigravity single delegation (compact)
```bash
PROMPT="Verify this proof: [content]. $(cat $DELEGATE/prompts/compact_verify.txt)"
bash $DELEGATE/scripts/ask_antigravity.sh "$PROMPT" result.md
head -3 result.md
# cat result.md only if VERDICT != CORRECT
```

### Pattern 2: Parallel cross-verification (high-stakes)
```bash
PROMPT="Verify Proposition 3: [content]. $(cat $DELEGATE/prompts/compact_verify.txt)"
bash $DELEGATE/scripts/ask_both.sh "$PROMPT" antigravity_out.md codex_out.md
head -3 antigravity_out.md
head -3 codex_out.md
# Compare verdicts; read full output only on disagreement or ERROR/GAP
```

### Pattern 2b: Three-way vote with Claude CLI as 3rd voter (majority-of-3)
```bash
PROMPT="Verify identification assumption: [content]. $(cat $DELEGATE/prompts/compact_verify.txt)"
INCLUDE_CLAUDE=1 bash $DELEGATE/scripts/ask_both.sh \
    "$PROMPT" antigravity_out.md codex_out.md claude_out.md
head -3 antigravity_out.md codex_out.md claude_out.md
# Majority-of-3 vote; INCLUDE_CLAUDE=1 makes Claude failure FATAL (caller opts in for vote semantics).
# The claude leg runs as a context-free voter under ~/.claude-delegate (pinned — inherited
# CLAUDE_DELEGATE_AGENTIC/RESUME cannot leak in). Independence caveat: Claude shares model
# family with the orchestrator — see Models & Reasoning Effort.
```

### Pattern 3a: Codex compact (routine code fix)
```bash
PROMPT="Fix the bootstrap CI in sim.R lines 45-80 so coverage reaches 93-97%.
$(cat $DELEGATE/prompts/compact_code.txt)"
bash $DELEGATE/scripts/ask_codex.sh "$PROMPT" result.md
head -3 result.md          # STATUS line: ~15 tokens
# If DONE: apply the changed blocks from result.md
# If FAILED or PARTIAL: cat result.md for diagnosis
```

### Pattern 3b: Codex full (unknown bug, first attempt)
```bash
bash $DELEGATE/scripts/ask_codex.sh \
  "Debug why the GMM weighting matrix is singular in estimator.R. Full diagnosis needed." \
  result.md
cat result.md              # worth it when root cause is unknown
```

### Pattern 3c: Codex result-only (known fix, maximum compression)
```bash
PROMPT="Rewrite the bootstrap_ci() function in sim.R to use BCa intervals instead of percentile.
$(cat $DELEGATE/prompts/result_only.txt)"
bash $DELEGATE/scripts/ask_codex.sh "$PROMPT" result.md
head -3 result.md          # FAILED sentinel sits on line 3 (after the metadata header); otherwise paste the artifact
```

### Pattern 7: Parallel Codex batch (3+ independent tasks)

Write prompt files first, then create a manifest referencing them:
```bash
# Write prompt files
mkdir -p /tmp/claude
cat > /tmp/claude/p1.md << 'EOF'
Implement compute_bootstrap_ci() in R that takes a numeric vector and returns
BCa confidence intervals. Include: bias correction, acceleration estimate.

Return your response in EXACTLY this format:
STATUS: [DONE|FAILED|PARTIAL] — [one sentence describing what was created]
FILES: [comma-separated list of files/functions created]
---
[complete code, ready to paste. Include all necessary imports/headers.
No explanation prose outside the code blocks.]
EOF

cat > /tmp/claude/p2.md << 'EOF'
Refactor the estimation loop in the following R code to use vectorized operations:
[paste code here]

Return your response in EXACTLY this format:
STATUS: [DONE|FAILED|PARTIAL] — [one sentence describing what was done or what failed]
CHANGED: [comma-separated list of modified function/section names]
---
[changed code blocks only. No unchanged code. No explanation prose.]
EOF

# Create manifest (output_path<TAB>@prompt_file)
printf '%s\t%s\n' \
  /tmp/claude/impl.md    @/tmp/claude/p1.md \
  /tmp/claude/refactor.md @/tmp/claude/p2.md \
  > /tmp/claude/batch.tsv

# Dispatch all (single tool call, timeout=600000)
bash $DELEGATE/scripts/ask_codex_batch.sh /tmp/claude/batch.tsv
# Read batch summary in stdout; head -3 only on failures
```

### Pattern 4: Antigravity literature survey (compact header + full body)
```bash
bash $DELEGATE/scripts/ask_antigravity.sh \
  "Survey all DiD estimators for staggered adoption published since 2021.
   First line: STATUS: DONE — [N papers found].
   Then list: authors, method, key assumption, software package." \
  survey.md
head -1 survey.md          # confirm it ran; then cat if count looks right
cat survey.md
```

---

## Prompt Engineering for Delegation

When writing prompts for Codex/Antigravity/Claude, include:
1. **Full context**: They have no access to your project. Paste relevant code/text directly into the prompt.
2. **Output format**: Use a template from the Output Format Templates section above. Always specify compact format for routine tasks.
3. **Scope constraint**: "Do not modify anything outside of [specific function/section]."
4. **Success criterion**: "The simulation should produce coverage within 93-97% across 1000 replications."
5. **Read discipline**: Use `head -N` to read status first; `cat` only on failure or when full output is needed.

---

## Timeout Configuration

**Critical:** The Bash tool's timeout must exceed the delegation script's internal timeout, or the script is killed before it can write its `STATUS: FAILED` envelope — the caller then sees a hard tool kill instead of a classified failure.

| Script | Default Internal Timeout | Recommended Bash `timeout` |
|--------|-------------------------|---------------------------|
| `ask_antigravity.sh` | 600s (`ANTIGRAVITY_TIMEOUT` env) | `timeout: 600000` |
| `ask_codex.sh`  | 600s (`CODEX_TIMEOUT` env)  | `timeout: 600000` |
| `ask_claude.sh` | 570s (`CLAUDE_DELEGATE_TIMEOUT` env) | `timeout: 600000` |
| `ask_both.sh`   | inherits per leg (600/600/570)  | `timeout: 600000` |
| `ask_codex_batch.sh` | 580s (`BATCH_TIMEOUT` env; 560s per task) | `timeout: 600000` |

**Always set `timeout: 600000` (10 min — the Bash tool's default maximum) when calling delegation scripts.** Even simple Antigravity queries can take 30s+ baseline, and network retries add 2-5 minutes. `ask_claude.sh` deliberately defaults to 570s so its own timeout classification fires before the 600s harness kill; the agy/codex legs default to 600s, so for guaranteed timeout classification on those, drop their env to ≤570 (e.g. `CODEX_TIMEOUT=570`). For dispatches that need **more** than 10 minutes: raise the script's `*_TIMEOUT` env AND either run the Bash call with `run_in_background: true` (no timeout cap) or raise `BASH_MAX_TIMEOUT_MS` in settings.

**Bash invocation must also pass `dangerouslyDisableSandbox: true`** for `ask_antigravity.sh` — and therefore for **`ask_both.sh`**, whose Antigravity leg otherwise fails every sandboxed cross-verification with "Sandbox blocked agy TCP bind" (and any leg failure makes ask_both exit 1). `ask_claude.sh` alone does **NOT** need it: env-token auth avoids the keychain, and full voter + agentic dispatches were verified working under the default sandbox on 2026-07-07. See Antigravity sandbox note in Prerequisites.

Example:
```
Bash(command="bash $DELEGATE/scripts/ask_antigravity.sh 'your query' output.md", timeout=600000, dangerouslyDisableSandbox=true)
```

To override the internal timeout:
```bash
ANTIGRAVITY_TIMEOUT=570 bash $DELEGATE/scripts/ask_antigravity.sh "long query" output.md
```

---

## Team Delegation Patterns

When working in agent teams (multiple subagents running in parallel), each teammate can independently delegate to Antigravity/Codex/Claude. This creates a three-tier hierarchy:

```
Orchestrator (team lead)
├── Teammate A (general-purpose) → Antigravity CLI (agy)
├── Teammate B (general-purpose) → Codex CLI
├── Teammate C (general-purpose) → ask_both.sh (Antigravity + Codex, optionally + Claude delegate)
└── Teammate D (general-purpose)       → local work only
```

> When you embed the script paths below inside a subagent's prompt, the teammate's shell won't inherit your `$DELEGATE` variable. Either tell the teammate to set it (see "Locating the scripts") or substitute the absolute path (`$CLAUDE_PLUGIN_ROOT/skills/delegate/…` for a plugin install, `~/.claude/skills/delegate/…` for a manual copy) before dispatching.

### Pattern 5: Team with parallel external delegation

```
Task(subagent_type="general-purpose", team_name="paper-review",
     prompt="Review the identification strategy in sections 3-4.
             Use ask_antigravity.sh for literature checks and ask_both.sh
             for proof cross-verification. Scripts are at
             $DELEGATE/scripts/
             Use compact_verify.txt format for all verification calls.
             Read head -3 of output first; cat only on ERROR/GAP.")

Task(subagent_type="general-purpose", team_name="paper-review",
     prompt="Audit the Monte Carlo simulation in sim/.
             Use ask_codex.sh to debug coverage issues. Scripts at
             $DELEGATE/scripts/
             Use compact_code.txt format. Read head -3 of output first.")
```

### Pattern 6: Teammate prompt template

When spawning teammates that should delegate:
```
You have access to external LLM delegation scripts:
- bash $DELEGATE/scripts/ask_antigravity.sh "prompt" output.md
- bash $DELEGATE/scripts/ask_codex.sh "prompt" output.md
- bash $DELEGATE/scripts/ask_claude.sh "prompt" output.md     (independent Claude delegate — voter by default)
- bash $DELEGATE/scripts/ask_both.sh "prompt" agy.md cdx.md
- INCLUDE_CLAUDE=1 bash $DELEGATE/scripts/ask_both.sh "prompt" agy.md cdx.md cl.md

Compact format templates are in $DELEGATE/prompts/.
Always append the appropriate template to delegation prompts:
- compact_code.txt for code fixes/refactoring
- compact_implement.txt for new code from spec
- compact_review.txt for code review/audit
- compact_verify.txt for proof/derivation checks
- result_only.txt for maximum compression (known fixes, shell scripts, LaTeX, regex/SQL)
For 3+ independent Codex tasks, use ask_codex_batch.sh with a manifest file.
Read head -3 of outputs first; cat only on failure or unknown errors.
Include full context in prompts (external CLIs cannot see project files).
Defaults: gpt-5.5 + xhigh effort (Codex); gemini-3.1-pro (Antigravity, via agy);
          opus (latest Opus) + effort max (Claude delegate under ~/.claude-delegate,
          OAuth token auth; overrides use the CLAUDE_DELEGATE_* namespace).
See "Models & Reasoning Effort" section for env-var overrides.
Bash invocation: pass `dangerouslyDisableSandbox: true` for ask_antigravity.sh AND ask_both.sh
(the agy TCP-bind requirement propagates through ask_both's antigravity leg).
ask_claude.sh alone runs fine under the default sandbox (verified 2026-07-07).
```

### Concurrency Note

Multiple teammates can call Antigravity/Codex/Claude simultaneously. The CLIs are independent processes with no parallelism cap at the OS level. If a paper has 6 sections to review, spawn 6 teammates, each dispatching to Antigravity independently. **Set your own cap on total concurrent external dispatches** in your `CLAUDE.md` (the author uses 6 across all subagents) to avoid overloading the CLIs or hitting rate limits.

---

## Token Budget Guideline

| Operation | Without delegation | With delegation (old: cat) | With delegation (new: head + compact) |
|---|---|---|---|
| Routine code fix (success) | 10,000+ | 500–1,500 | 50–150 |
| Routine code fix (needs debug) | 10,000+ | 500–1,500 | 500–1,500 |
| Proof verification | 5,000 | 300–800 | 80–150 |
| Literature check | 8,000 | 300–800 | 100–200 |
| Debug simulation (unknown) | 10,000+ | 1,000–3,000 | 1,000–3,000 |
| Paper revision cycle | 15,000 | 3,000 | 800–1,500 |
| Batch of N tasks (compact) | ~10,000×N | ~200×N | ~30 + 15/failure |
| Team of N + delegation | ~10,000×N | ~700×N | ~200×N |

**Rules:**
- Compact format + `head` by default for all routine tasks.
- `cat` only on failure, unknown errors, or when the reasoning trace is the deliverable.
- Use `ask_both.sh` only for high-stakes claims (proofs, identification arguments); not for routine code fixes.
- Target: Claude Code spends <10% of tokens on any task that can be delegated with compact output.

---

## Context Hygiene

Long delegation sessions accumulate context even with compact templates. Use `/compact` at logical boundaries.

**When to compact:**
- After exploration/research phase, before writing code
- After completing a delegation round (all results read and integrated)
- After any milestone where accumulated context won't be needed verbatim
- When a compaction-reminder hook fires, if you've configured one (the author's fires after 25 tool calls)

**When NOT to compact:**
- Mid-delegation (before reading results)
- While debugging a specific failure across multiple files
- When you need verbatim recall of earlier tool outputs

**Delegation + compaction workflow:**
1. Dispatch delegations (ask_codex/antigravity/claude/both)
2. Read results with `head -3` (compact) or `cat` (full)
3. Integrate findings into code/LaTeX
4. `/compact` before starting next phase
5. Repeat

If you configure a compaction-reminder hook (the author's fires after 25 Edit/Write operations), evaluate at each prompt whether you're at a logical boundary before acting on it.

---

## Appendix: Migration from Gemini CLI (deprecated 2026-06-18)

Google retired the consumer Gemini CLI on 2026-06-18. Antigravity CLI (`agy`) is the official replacement. This delegate skill migrated on 2026-05-28.

If you previously had Gemini CLI plugins/configurations in `~/.gemini/`, the Antigravity CLI offers a one-shot import:

```bash
agy plugin import gemini
```

**Warning**: this command may rewrite `~/.gemini/` state and merge plugin configurations into `~/.gemini/antigravity-cli/`. Inspect the result before relying on it. The delegate skill does NOT run this command — it is opt-in user-driven migration only.
