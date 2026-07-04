# claude-code-skills

A small collection of [Claude Code](https://claude.com/claude-code) skills for research and orchestration workflows, distributed as a plugin marketplace. Two skills so far:

| Skill | What it does |
|---|---|
| **[`delegate`](skills/delegate/SKILL.md)** | Offload heavy or token-expensive work from Claude Code to **Codex CLI** (GPT) and **Antigravity CLI** (Gemini), with an optional Claude CLI third voter. Save tokens, run jobs in parallel, and cross-verify across independent models. |
| **[`gemini-pdf`](skills/gemini-pdf/SKILL.md)** | Convert PDFs to clean **Markdown** (LaTeX math, tables, section structure) via Antigravity CLI, with auto-chunking for long papers, a quality-score gate, and an optional Mathpix fallback. |

Both are aimed at researchers — including people new to command-line tools. If you've never used a terminal AI before, start with **[docs/SETUP.md](docs/SETUP.md)**.

> **Background / talk.** These skills accompany the talk *"Using LLMs and Generative AI for Economics Research"* (H. Kasahara, 2026). The slides are included as **[docs/llm_research_slides.pdf](docs/llm_research_slides.pdf)**, and **[docs/WORKFLOW.md](docs/WORKFLOW.md)** distills the orchestrate-and-verify workflow they describe.

---

## Install

Add the marketplace once, then install whichever skills you want:

```text
/plugin marketplace add hkasahar/claude-code-skills
/plugin install delegate@kasahara-skills
/plugin install gemini-pdf@kasahara-skills
```

Then restart Claude Code (or run `/reload-plugins`). If the GitHub shorthand doesn't resolve on your setup, use the full URL: `/plugin marketplace add https://github.com/hkasahar/claude-code-skills.git`.

Non-interactive equivalent (for scripts/CI):

```bash
claude plugin marketplace add hkasahar/claude-code-skills
claude plugin install delegate@kasahara-skills
claude plugin install gemini-pdf@kasahara-skills
```

**Manual copy** (no versioning): clone the repo and copy the skill folder(s) into `~/.claude/skills/`:

```bash
git clone https://github.com/hkasahar/claude-code-skills.git
cp -R claude-code-skills/skills/delegate   ~/.claude/skills/
cp -R claude-code-skills/skills/gemini-pdf ~/.claude/skills/
```

---

## `delegate` — quick look

```bash
DELEGATE="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/delegate}"
DELEGATE="${DELEGATE:-$HOME/.claude/skills/delegate}"

bash "$DELEGATE/scripts/preflight.sh"                     # check Codex/Antigravity installed + signed in
bash "$DELEGATE/scripts/ask_codex.sh" "Fix …" out.md      # delegate to Codex
head -3 out.md                                            # read the STATUS header, not the whole file
bash "$DELEGATE/scripts/ask_both.sh" "Verify …" a.md b.md # cross-verify a claim across both models
```

Full pattern library: **[skills/delegate/SKILL.md](skills/delegate/SKILL.md)**. Prerequisites and setup: **[docs/SETUP.md](docs/SETUP.md)**.

## `gemini-pdf` — quick look

```bash
GEMINI_PDF="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/gemini-pdf}"
GEMINI_PDF="${GEMINI_PDF:-$HOME/.claude/skills/gemini-pdf}"

bash "$GEMINI_PDF/scripts/pdf_to_markdown.sh" paper.pdf   # → paper.md (LaTeX math, tables, structure)
```

Prerequisites (`agy`, `markitdown`, `poppler`, optional Mathpix) and all flags: **[docs/gemini-pdf.md](docs/gemini-pdf.md)**.

---

## Security

These skills run shell commands and send your prompts/documents to external services — read this before use:

- **Review the scripts before running them.** They're short and readable.
- **Delegation/conversion sends data off your machine** — to OpenAI (Codex) and/or Google (Antigravity). **Do not send confidential, embargoed, or privacy-sensitive material.**
- **Never paste API keys, tokens, or passwords into a prompt.** Each CLI handles its own sign-in.
- The Antigravity/Claude wrappers need the sandbox relaxed only so the CLI can bind a local port / read its credential store to authenticate — relax it narrowly, never globally. See each skill's `SKILL.md`.

---

## Attribution & license

Workflow and examples adapted from H. Kasahara, *"Using LLMs and Generative AI for Economics Research"* (2026) — slides in [`docs/llm_research_slides.pdf`](docs/llm_research_slides.pdf).

Model names, effort tiers, prices, and CLI flags change quickly; values in these docs are dated snapshots — verify the current ones for your tools.

Released under the [MIT License](LICENSE).
