# Example `CLAUDE.md` (a template you adapt)

`CLAUDE.md` is a file Claude Code reads at the start of every session and after every `/clear`. It's where you put standing instructions — how you want Claude to plan, delegate, and verify. A global one lives at `~/.claude/CLAUDE.md`; a per-project one lives at the repo root and adds to it.

Below is a **generic, ready-to-adapt** template that encodes the orchestrate-and-verify workflow this repo supports. It contains **no personal paths, tools, or data** — fill in the `<…>` placeholders with your own. Copy the parts you want; delete the rest. Keep it short: `CLAUDE.md` is read into context every session, so every line costs tokens on every task.

---

```markdown
# CLAUDE.md

## Session start
1. Read `CHECKPOINT.md` and `MEMORY.md` if they exist — resume from the last known state.
2. Read `tasks/lessons.md` if it exists — don't repeat past mistakes.
3. Identify the active project from the working directory and git branch.

## Core principles
- **Maximum effort.** Think exhaustively; don't cut corners or leave TODOs. Produce complete work on the first pass, then re-read it against the request before replying.
- **Plan before acting.** For any non-trivial task (3+ steps or a design decision), use plan mode. Interview me with concrete options; end planning prompts by asking me any open questions. One detailed prompt beats a long chat.
- **Verify before "done".** Prove it works — tests, logs, a diff of behavior. Ask: "would a senior reviewer approve this?" Don't claim something passes without showing the check.
- **Ask, don't guess.** Under ambiguity, ask 2–5 targeted questions with options before proceeding.

## Orchestration (delegate aggressively, verify rigorously)
Operate as a manager. Hand heavy work to Codex (code, math, simulation) and Antigravity
(reading, literature, long-context) via the `delegate` skill; keep orchestration,
integration, and the final quality gate here. Package full context into every delegation
prompt — the external CLIs cannot see project files.

Routing (first match wins):
| # | Condition | Route |
|---|-----------|-------|
| 1 | Iterative local loops (edit→run→test→fix) | Claude Code |
| 2 | Secrets, credentials, destructive ops | Claude Code |
| 3 | High-stakes proof / identification argument | Cross-verify (Codex + Antigravity; Claude CLI breaks ties) |
| 4 | Routine check of a known result / algebra | Antigravity |
| 5 | Code: implement, debug, refactor, test, simulate, mechanical LaTeX | Codex |
| 6 | Reading/reasoning: literature, citations, summaries | Antigravity |
| 7 | <your specialized skills> | <that tool> |
| 8 | Drafting prose / teaching material | Antigravity draft → Claude edit |

For trivial fixes (typo, off-by-one, missing import), skip the table and do it directly.

## Delegation discipline
- One task per subagent; keep the main context clean.
- Read delegation output with `head -3` (the STATUS/VERDICT header); `cat` only on failure.
- Cap total concurrent external dispatches at <N> across all subagents (I use 6).
- When Codex and Antigravity disagree on a high-stakes claim, resolve it before accepting — do not average two answers.

## Quality gates (check before presenting)
| Task type | Must verify |
|-----------|-------------|
| Code | Runs; tests pass; seeds set; no absolute paths |
| Proofs | Every step explicit; assumptions stated; cited results checked against the source |
| Empirical | Identification clear; SEs appropriate; diagnostics included |
| Delegated | STATUS/VERDICT checked; output matches spec; disagreements resolved |
If a gate fails, retry — don't present broken work.

## Context and memory
- `/clear` once the context window is well used (performance degrades and tokens rise as it fills).
- Before clearing, make sure the next steps are written down (`tasks/todo.md`, `MEMORY.md`).

## About me / conventions
- <your field, languages, and style preferences — e.g. "Default to R; LaTeX for writing.">
```

---

## How to use this

1. Save your version as `~/.claude/CLAUDE.md` (global) or `<repo>/CLAUDE.md` (project).
2. Fill in every `<…>` placeholder; delete rows and sections you don't need.
3. Install the [`delegate`](../README.md) skill so the "Codex / Antigravity" routes actually work.
4. Iterate: when Claude makes a mistake, add a one-line rule to `tasks/lessons.md` (or here) so it doesn't recur.

See [WORKFLOW.md](WORKFLOW.md) for the reasoning behind each section.
