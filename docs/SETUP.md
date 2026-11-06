# Setup

Install only the providers needed for your selected route. The default review needs Codex and Antigravity; individual wrappers work independently. Availability and account permissions differ by provider and region. This guide does not promise access or recommend bypassing a restriction.

## Local tools

Use Bash, Python 3, Perl, and jq. The PDF converter also uses Poppler (`pdfinfo`, `pdftotext`, and splitting tools) and pypdf. Install the public Python requirements into your chosen environment:

```bash
python3 -m pip install -r skills/gemini-pdf/requirements.txt
```

Mathpix needs `requests` and separately configured credentials. `markitdown` is optional for the legacy text pipeline; the experimental local model/MinerU environments are not part of this package. Tests additionally need the dependencies listed with the PDF tests and LaTeX for the seeded fixture.

## Configure the chosen CLI

- **Codex:** follow the [official CLI guide](https://developers.openai.com/codex/cli). One supported installation is `npm install -g @openai/codex`; run `codex` from a project directory and choose a supported sign-in method. Account-based and API-based methods have different billing/access requirements.
- **Antigravity:** install an approved `agy` distribution using its upstream instructions and complete its sign-in flow outside an agent prompt. The wrapper requires headless output and explicit model selection; inspect `agy --help` and `agy models` yourself before use.
- **Optional Grok:** configure a compatible `grok` CLI if you deliberately select that wrapper/leg. Its API backend is a separate metered opt-in, unavailable in the public review round.
- **Optional isolated Claude:** configure `claude` only when selecting that wrapper. See the credential procedure below.

Installers and sign-in may access the network or update local configuration. They are setup actions, not part of the offline test suite. Use upstream documentation for current installers and model IDs; this repository does not bundle provider clients or credentials.

## Install the skills

In Claude Code:

```text
/plugin marketplace add hkasahar/claude-code-skills
/plugin install delegate@kasahara-skills
/plugin install gemini-pdf@kasahara-skills
```

Or clone and call scripts directly. For a manual skill installation, review/back up any existing installation before copying:

```bash
git clone https://github.com/hkasahar/claude-code-skills.git
mkdir -p "$HOME/.claude/skills"
cp -R claude-code-skills/skills/delegate "$HOME/.claude/skills/"
cp -R claude-code-skills/skills/gemini-pdf "$HOME/.claude/skills/"
```

Relative sibling lookup supports manual and marketplace layouts; explicit converter overrides remain available. No private batch-download, reference-search, or decision-service skill is installed by this repository.

## Check without sending prompts

From the clone:

```bash
bash skills/delegate/scripts/preflight.sh --offline
```

This checks local binaries and required flags. It does not establish authentication, capacity, or model availability. `--live` explicitly runs provider requests and can consume quota or incur charges; do not use it for a no-network check. Provider failures are runtime state, without deciding whether a proposed method or result is correct.

## Keep credentials out of prompts

The optional Claude wrapper uses an isolated configuration directory and a locally stored OAuth token. Run setup in a regular terminal, outside a recorded agent conversation. Obtain the token through the provider's supported setup flow, then create the destination with private permissions; never include the token value in a shell command or prompt:

```bash
umask 077
mkdir -p "$HOME/.claude-delegate"
chmod 700 "$HOME/.claude-delegate"
cat > "$HOME/.claude-delegate/oauth_token"
# Paste in the terminal, then end input with Ctrl-D.
chmod 600 "$HOME/.claude-delegate/oauth_token"
```

A separate configuration is not a security sandbox. An agentic worker can access resources allowed by its runtime, including its own credentials. Use trusted tasks and a scoped directory.

Paid DeepSeek fallback additionally requires bounded OpenRouter key/preset verification. Do not copy another person's configuration or turn a local offline test into an admission claim. Read [wrapper modes](../skills/delegate/references/legs.md) before configuring or selecting that route. The default is off.

## A first bounded task

Write a self-contained `task.md` with the objective, inputs, allowed edits, output contract, checks, and stopping condition. Review the exact disclosure, then invoke one selected wrapper:

```bash
bash skills/delegate/scripts/ask_codex.sh @task.md result.md
head -3 result.md
```

Review the complete evidence needed before integrating the result. For a review round, use fixed slots:

```bash
bash skills/delegate/scripts/ask_both.sh @review.md codex.md "" gemini.md
```

Explicit extra-provider options, model/effort controls, deadlines, and output contracts are documented in [delegate](../skills/delegate/SKILL.md). Model defaults are examples from the code, not assurances of current service availability.
