# delegate — a Claude Code skill for offloading heavy work to Codex & Antigravity

**`delegate`** turns [Claude Code](https://claude.com/claude-code) into an *orchestrator*: it hands token-expensive or parallelizable work to two other command-line AIs — **Codex CLI** (OpenAI's GPT) and **Antigravity CLI** (Google's Gemini) — and, optionally, a third **Claude CLI** voter. Each job runs as a separate process, writes its result to a file, and Claude reads back only a one-line `STATUS:`/`VERDICT:` header instead of pasting the whole answer into its context.

The payoff is three things at once:

- **Token savings.** A routine code fix that would cost ~10,000 tokens read inline costs ~50–150 when delegated and read with `head -3`. Your Claude context stays clean, so it stays fast and cheap.
- **Cross-model verification.** Send the same proof or piece of code to two independent models and compare their verdicts. Disagreement is a signal to look closer — invaluable for math, identification arguments, and subtle bugs.
- **Parallelism.** Fan several jobs out at once (e.g. review six paper sections simultaneously) instead of doing them one at a time in a single conversation.

This skill is aimed at researchers — including people who are new to command-line tools. If you have never used a terminal AI before, start with **[docs/SETUP.md](docs/SETUP.md)**, which walks you through everything from installing Node.js to signing in to each CLI.

> **Background / talk.** This skill accompanies the talk *"Using LLMs and Generative AI for Economics Research"* (H. Kasahara, 2026). The slides are included as **[docs/llm_research_slides.pdf](docs/llm_research_slides.pdf)**, and **[docs/WORKFLOW.md](docs/WORKFLOW.md)** distills the orchestrate-and-verify workflow they describe.

---

## What you need first

- A terminal, [Node.js](https://nodejs.org) (LTS), and **Claude Code** itself.
- **Codex CLI** (`codex`) — signs in with a ChatGPT account; no API key required.
- **Antigravity CLI** (`agy`) — signs in with a Google account.
- *(Optional)* the **Claude CLI** as a third voter — you already have it if you have Claude Code.

Full, click-by-click instructions (including the common macOS pitfalls) are in **[docs/SETUP.md](docs/SETUP.md)**.

---

## Install

**Recommended — as a Claude Code plugin (versioned, one command):**

Inside Claude Code, run:

```text
/plugin marketplace add hkasahar/claude-code-delegate
/plugin install delegate@kasahara-skills
```

Then restart Claude Code (or run `/reload-plugins`).

If the GitHub shorthand doesn't resolve on your setup, use the full URL:

```text
/plugin marketplace add https://github.com/hkasahar/claude-code-delegate.git
/plugin install delegate@kasahara-skills
```

Equivalent non-interactive form (for scripts/CI):

```bash
claude plugin marketplace add hkasahar/claude-code-delegate
claude plugin install delegate@kasahara-skills
```

**Alternative — manual copy (no versioning):**

```bash
git clone https://github.com/hkasahar/claude-code-delegate.git
mkdir -p ~/.claude/skills/delegate
cp -R claude-code-delegate/skills/delegate/* ~/.claude/skills/delegate/
```

Once installed, Claude Code will reach for the skill on its own whenever a task is worth delegating; you can also ask for it explicitly ("delegate this to Codex", "cross-verify this proof with `ask_both`").

---

## Quick start

**1. Check your tools are installed and signed in:**

```bash
# Set DELEGATE to wherever the skill lives (plugin or manual copy):
DELEGATE="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/delegate}"
DELEGATE="${DELEGATE:-$HOME/.claude/skills/delegate}"

bash "$DELEGATE/scripts/preflight.sh"
```

You should see `codex` and `agy` reported as available (and `claude` if you set up the optional voter).

**2. Delegate a single job to Codex and read only the header:**

```bash
bash "$DELEGATE/scripts/ask_codex.sh" "Reply with exactly: STATUS: DONE — hello" /tmp/out.md
head -3 /tmp/out.md            # ~15 tokens, not the whole file
```

**3. Cross-verify a claim across both models:**

```bash
bash "$DELEGATE/scripts/ask_both.sh" \
  "Verify: for i.i.d. X with finite variance, the sample mean is a consistent estimator of E[X]." \
  /tmp/agy.md /tmp/cdx.md
head -3 /tmp/agy.md /tmp/cdx.md   # compare verdicts; read in full only on disagreement
```

In practice you rarely type these yourself — you tell Claude Code what you want and it runs the right script with the right compact template. See [`skills/delegate/SKILL.md`](skills/delegate/SKILL.md) for the full pattern library.

---

## What's in the box

```
skills/delegate/
├── SKILL.md            # the skill: rules, templates, usage patterns, read discipline
├── prompts/            # 5 compact output-format templates (STATUS/VERDICT contracts)
└── scripts/
    ├── ask_codex.sh        # delegate to Codex (GPT)
    ├── ask_antigravity.sh  # delegate to Antigravity (Gemini)
    ├── ask_claude.sh       # optional Claude CLI 3rd voter
    ├── ask_both.sh         # run Codex + Antigravity in parallel (cross-verify)
    ├── ask_codex_batch.sh  # fan out many Codex jobs from a manifest
    └── preflight.sh        # verify installs + auth
docs/
├── SETUP.md            # step-by-step setup for newcomers
├── WORKFLOW.md         # the orchestrate-and-verify workflow
├── example-CLAUDE.md   # an adaptable CLAUDE.md template
└── llm_research_slides.pdf
```

---

## Security

These scripts run shell commands and send your prompts to external services — read this before use:

- **Review the scripts before running them.** They are short and readable; know what they do.
- **Delegating sends data off your machine.** Your prompt, and any file content you paste into it, goes to OpenAI (Codex) or Google (Antigravity). **Do not delegate confidential, embargoed, or privacy-sensitive material.**
- **Never paste API keys, tokens, or passwords into a prompt.** Authentication is handled by each CLI's own sign-in, not by putting secrets in text.
- **The Antigravity/Claude wrappers need the sandbox relaxed** for one specific reason (they bind a localhost port / read your credential store to authenticate). Only relax it for these calls — see the note in [`SKILL.md`](skills/delegate/SKILL.md) and [`docs/SETUP.md`](docs/SETUP.md).

---

## Attribution & license

Workflow and examples adapted from H. Kasahara, *"Using LLMs and Generative AI for Economics Research"* (2026) — slides in [`docs/llm_research_slides.pdf`](docs/llm_research_slides.pdf).

Model names, effort tiers, prices, and CLI flags change quickly; the values in these docs are snapshots — verify the current ones for your tools.

Released under the [MIT License](LICENSE).
