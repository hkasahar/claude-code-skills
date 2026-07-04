---
name: delegate
description: >-
  Delegate heavy or token-expensive work from Claude Code to Codex CLI (GPT) and
  Antigravity CLI (Gemini), with an optional Claude CLI third voter. Use when a task is
  large, parallelizable, or benefits from cross-model verification: dispatch a
  self-contained prompt to an external CLI, write its output to a file, and read only the
  STATUS/VERDICT header to keep the main context clean. Good for code implementation,
  debugging, refactoring, proof and derivation checking, literature review, and any
  second-opinion cross-check. Triggers include "delegate to Codex/Antigravity",
  "cross-verify with ask_both", "run this in parallel", and "save tokens".
---

# Delegate Skill

Orchestrate token-efficient workflows by delegating heavy tasks to **Codex CLI** (`codex`) and **Antigravity CLI** (`agy`), which run on separate subscription quotas at ~$0 marginal cost. An optional **Claude CLI** third voter (via `ask_claude.sh`) is available for vote-based flows (e.g. a majority-of-3 verification).

The core discipline: dispatch a self-contained prompt to an external CLI, write its output to a file, then read only the first few lines (a `STATUS:`/`VERDICT:` header) instead of pasting the whole result into context. On success this costs ~15–50 tokens instead of thousands.

## Prerequisites

- **Antigravity CLI** installed and authenticated (`agy` on PATH; install: `curl -fsSL https://antigravity.google/cli/install.sh | bash`)
- **Codex CLI** installed and authenticated (`codex` on PATH; install: `npm install -g @openai/codex`)
- **Claude CLI** installed and authenticated for headless use (`claude setup-token`, or set `ANTHROPIC_API_KEY`). Only needed for the optional 3rd voter (`ask_claude.sh`).
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

The same applies to `claude -p` (the 3rd voter): it works once `claude setup-token` has been run AND the wrapper is dispatched with sandbox disabled.

> **⚠️ Security — read before disabling the sandbox.** `dangerouslyDisableSandbox: true` lets the delegation CLI reach the network and your credential store (it needs both to authenticate and call the model API). Only disable the sandbox for these specific delegation calls — never as a blanket setting — and never delegate a prompt built from untrusted input. Also note that **delegating sends your prompt, and any file content you paste into it, to the external provider** (OpenAI for Codex, Google for Antigravity). Do not delegate confidential, embargoed, or privacy-sensitive material. See `docs/SETUP.md` for the full security checklist.

---

## Models & Reasoning Effort

CLI interfaces change quickly. **Re-verify quarterly** via `codex --version` / `codex debug models`, `agy --version`, and `claude --version`; update the defaults below if the resolved model identifier in recent session logs has moved.

| CLI | Default model (verified 2026-05-28) | Effort tiers | Effort knob | Override env var(s) |
|---|---|---|---|---|
| Codex | `gpt-5.5` | `low \| medium \| high \| xhigh` (per `codex debug models`; `minimal` is **not** in the `gpt-5.5` catalog) | `-c model_reasoning_effort=xhigh` (passed via `-c` config override on `codex exec`) | `CODEX_MODEL`, `CODEX_EFFORT` |
| Antigravity | `gemini-3.1-pro` (logically — see note) | n/a | **none exposed at CLI level** (agy v1.0.0 has NO `--model` flag); model is configured via `~/.gemini/antigravity-cli/settings.json` or the `agy /model` interactive command | `ANTIGRAVITY_MODEL` (informational only — recorded in metadata header; **not** passed to the binary) |
| Claude SDK (optional 3rd voter) | `opus` alias → `claude-opus-4-7` | `low \| medium \| high \| xhigh \| max` (passed via `--effort`) | `--effort max` (default in wrapper) | `CLAUDE_MODEL`, `CLAUDE_EFFORT` |

### Default invocation = best model, max effort

`ask_codex.sh` always pins effort to `xhigh` and uses `gpt-5.5` as the model. No flag is needed to "go strongest" — that is the default. **The pinning is intentional**: the script unconditionally passes `-c model_reasoning_effort="$EFFORT"` (with `EFFORT=${CODEX_EFFORT:-xhigh}`), which **overrides whatever `~/.codex/config.toml` says**. Per-call overrides via `CODEX_EFFORT=…` and `CODEX_MODEL=…` are still respected.

`ask_antigravity.sh` does **not** pass a `--model` flag (agy v1.0.0 doesn't have one). The model is whatever the user has set in `~/.gemini/antigravity-cli/settings.json`. For math-heavy / proof-verification workloads, set the model to `Gemini 3.1 Pro (High)` via the interactive `agy /model` command. `ANTIGRAVITY_MODEL` is recorded in the wrapper's metadata header for provenance but is not passed to the binary.

`ask_claude.sh` defaults to model `opus` (resolves to the latest Claude Opus) with `--effort max` and `--tools ""` (disables all tool use — voting is pure judgment). The script also passes `--bare` to skip CLAUDE.md auto-discovery, giving the 3rd voter fresh context.

### Independence caveat (3rd voter)

The Claude SDK 3rd voter shares the model family with the orchestrating Claude Code session. Vote independence is mechanically preserved (3 distinct dispatches), but **statistically reduced** vs. using a third model from a different provider. Use this for mechanical disagreement detection, not as a true triangulation against the orchestrator's biases.

### When to override the defaults

- **Cheaper / faster verifications**: `CODEX_EFFORT=high bash ask_codex.sh ...` (drops one tier).
- **Probing alternative models**: `CODEX_MODEL=gpt-5.4 bash ask_codex.sh ...`.
- **Switch Antigravity model**: edit `~/.gemini/antigravity-cli/settings.json` (model field is a display string like "Gemini 3.1 Pro (High)"); the wrapper does not change this.
- **Switch Claude model**: `CLAUDE_MODEL=sonnet bash ask_claude.sh ...` (faster/cheaper alternative for the 3rd voter; not recommended for high-stakes votes).

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
head -1 result.md          # check for FAILED line; otherwise paste directly
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
# Independence caveat: Claude shares model family with the orchestrator — see Models & Reasoning Effort.
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
head -1 result.md          # check for FAILED; otherwise paste the artifact
```

### Pattern 7: Parallel Codex batch (3+ independent tasks)

Write prompt files first, then create a manifest referencing them:
```bash
# Write prompt files
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

**Critical:** The Bash tool's timeout must exceed the delegation script's timeout, or the script will be killed before it can finish.

| Script | Default Internal Timeout | Recommended Bash `timeout` |
|--------|-------------------------|---------------------------|
| `ask_antigravity.sh` | 1200s (`ANTIGRAVITY_TIMEOUT` env) | `timeout: 1200000` |
| `ask_codex.sh`  | 1200s (`CODEX_TIMEOUT` env)  | `timeout: 1200000` |
| `ask_claude.sh` | 1200s (`CLAUDE_TIMEOUT` env) | `timeout: 1200000` |
| `ask_both.sh`   | 1200s (inherits from above)  | `timeout: 1200000` |
| `ask_codex_batch.sh` | 1200s (`BATCH_TIMEOUT` env) | `timeout: 1200000` |

**Always set `timeout: 1200000` (20 min, the Bash tool maximum) when calling delegation scripts.** Even simple Antigravity queries can take 30s+ baseline, and network retries add 2-5 minutes.

**Bash invocation must also pass `dangerouslyDisableSandbox: true`** for `ask_antigravity.sh` and `ask_claude.sh` (both bind localhost TCP ports / access keychain) — see Antigravity sandbox note in Prerequisites.

Example:
```
Bash(command="bash $DELEGATE/scripts/ask_antigravity.sh 'your query' output.md", timeout=1200000)
```

To override the internal timeout:
```bash
ANTIGRAVITY_TIMEOUT=900 bash $DELEGATE/scripts/ask_antigravity.sh "long query" output.md
```

---

## Team Delegation Patterns

When working in agent teams (multiple subagents running in parallel), each teammate can independently delegate to Antigravity/Codex/Claude. This creates a three-tier hierarchy:

```
Orchestrator (team lead)
├── Teammate A (general-purpose) → Antigravity CLI (agy)
├── Teammate B (general-purpose) → Codex CLI
├── Teammate C (general-purpose) → ask_both.sh (Antigravity + Codex, optionally + Claude CLI)
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
- bash $DELEGATE/scripts/ask_claude.sh "prompt" output.md     (optional 3rd voter)
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
          claude-opus-4-7 + effort max (Claude SDK, optional 3rd voter via ask_claude.sh).
See "Models & Reasoning Effort" section for env-var overrides.
Bash invocation: pass `dangerouslyDisableSandbox: true` for ask_antigravity.sh and ask_claude.sh.
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
