# Setup guide — from zero to a working orchestrate-and-delegate setup

This guide assumes **no prior experience** with terminals, Node.js, or command-line AI tools. If you can copy and paste, you can finish it. It targets **macOS**; Linux users can follow the same steps with their package manager instead of Homebrew.

By the end you will have:

```
        You  ──►  Claude Code  ──┬──►  Codex CLI   (OpenAI GPT)     "the coder"
                 (orchestrator)  ├──►  Antigravity CLI (Gemini)     "the reader"
                                 └──►  Claude CLI  (optional voter)  "the tiebreaker"
```

Claude Code stays in charge, hands heavy jobs to the others, and checks their work.

> **A note on cost.** The talk this repo accompanies used paid tiers (Claude, ChatGPT, and Google subscriptions). You do **not** need all three to start — Claude Code alone is useful, and you can add Codex and/or Antigravity later. Prices and tiers change constantly; check each provider's current pricing rather than trusting any number written here.

---

## Step 0 — Open the Terminal

Press `⌘ + Space`, type **Terminal**, press Return. A window with a text prompt appears. Every command below is typed here, one line at a time, pressing Return after each. When a command finishes it returns you to the prompt.

Tip: to paste, use `⌘ + V`. Long-press-free — it just works.

---

## Step 1 — Install the basics (git + Homebrew)

**1a. Apple's command-line tools** (this gives you `git`, needed later):

```bash
xcode-select --install
```

A dialog pops up — click **Install** and wait for it to finish. If it says "already installed," you're fine.

**1b. Homebrew**, the macOS package manager (makes installing other tools painless):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

At the end it may print two `echo ... >> ~/.zprofile` lines and an `eval` line under "Next steps." **Run those lines** (copy-paste them) so `brew` is on your `PATH`, then **close and reopen Terminal**. Verify:

```bash
brew --version
```

You should see a version number. (Curious what that `curl … | bash` command does? See ["What does `curl … | bash` mean?"](#what-does-curl--bash-mean) below.)

---

## Step 2 — Install Node.js

Claude Code and Codex are installed through `npm`, which comes with Node.js.

```bash
brew install node
node --version    # should print something like v22.x or newer
npm --version
```

### Avoid the `npm -g` permission error (important)

Installing global npm packages sometimes fails with `EACCES: permission denied`. **Do not fix this with `sudo`** — that causes worse problems later. Instead, point npm's global folder at your home directory once:

```bash
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc      # or just close and reopen Terminal
```

Now global installs (`npm install -g ...`) land in a folder you own, and their commands are on your `PATH`.

---

## Step 3 — Install and sign in to Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Then start it from any folder:

```bash
claude
```

The first run walks you through signing in (it opens a browser). Once you're in, type `/help` to look around, and `/exit` to quit. If `claude: command not found`, revisit the `PATH` line in Step 2 and reopen Terminal.

> Everything from here — installing Codex, Antigravity, and this skill — you can also just **ask Claude Code to do for you**. Paste the command block and say "run this and tell me if it worked." That is itself the workflow this repo teaches.

---

## Step 4 — Install and sign in to Codex CLI (OpenAI / GPT)

```bash
npm install -g @openai/codex
```

Sign in with your ChatGPT account (**no API key needed** — it uses your ChatGPT Plus/Pro subscription):

```bash
codex
```

Follow the browser sign-in, then quit. A quick sanity check that headless mode works:

```bash
codex exec --skip-git-repo-check "Reply with exactly: OK"
```

If it prints `OK` (possibly after a few seconds), Codex is ready.

---

## Step 5 — Install and sign in to Antigravity CLI (Google / Gemini)

Antigravity's installer is a shell script you download and run:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

See ["What does `curl … | bash` mean?"](#what-does-curl--bash-mean) if you'd like to inspect the script before running it (recommended good habit). After it installs, the `agy` command may live in `~/.local/bin`; if `agy` isn't found, add that to your `PATH`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Sign in with your Google account:

```bash
agy
```

Follow the sign-in, then quit. Antigravity uses your Google subscription for access.

> **Antigravity and the Claude Code sandbox.** `agy` opens a small local network port to run. Inside Claude Code's default protective sandbox that is blocked, and the `ask_antigravity.sh` wrapper will report `STATUS: FAILED — Sandbox blocked agy TCP bind`. That's expected. When you (or Claude) call `ask_antigravity.sh` — or `ask_both.sh`, whose Antigravity leg inherits the requirement — the Bash call must be made with the sandbox relaxed (`dangerouslyDisableSandbox: true`). Only do this for these specific delegation calls — see [Security](#security). `ask_claude.sh` does **not** need it: it authenticates via an env token, not the keychain, and runs fully under the default sandbox (verified 2026-07-07).

---

## Step 6 — (Optional) the independent Claude delegate (3rd voter + agentic worker)

`ask_claude.sh` runs a second, fully isolated Claude Code instance: its own config dir (`~/.claude-delegate`), its own OAuth token, no access to your hooks, plugins, memory, or CLAUDE.md. It serves as a tie-breaking 3rd voter (`INCLUDE_CLAUDE=1 ask_both.sh …`) and as an agentic worker (`CLAUDE_DELEGATE_AGENTIC=1`). One-time setup — **run these in a regular terminal, NOT via `!` inside a Claude Code session** (the long-lived token must never land in shell history or a session transcript):

```bash
CLAUDE_CONFIG_DIR=~/.claude-delegate claude setup-token   # browser flow; prints an sk-ant-oat… token
umask 077
cat > ~/.claude-delegate/oauth_token    # paste the token, press Enter, then Ctrl-D
```

Sanity-check: `wc -c ~/.claude-delegate/oauth_token` should report roughly 90–130 bytes (0 or 1 means the paste didn't take — `pbpaste > ~/.claude-delegate/oauth_token` is a reliable alternative on macOS). The wrapper self-heals file permissions (700/600) on first run.

Why a token file instead of your normal login: the wrapper authenticates via an explicit `CLAUDE_CODE_OAUTH_TOKEN` env var, which keeps the delegate's auth deterministic and independent of your main session (and avoids the macOS Keychain, which `CLAUDE_CONFIG_DIR` does not namespace). You can mint the token from a second subscription for full quota isolation, or from your main account (config isolation still holds; quota is shared). A metered `ANTHROPIC_API_KEY` fallback exists but is opt-in only (`CLAUDE_DELEGATE_ALLOW_API_KEY=1`).

This step is optional — skip it if you only want the two-way cross-check.

---

## Step 7 — Install the delegate skill

**As a plugin (recommended).** Inside Claude Code:

```text
/plugin marketplace add hkasahar/claude-code-skills
/plugin install delegate@kasahara-skills
```

Then **restart Claude Code** (or `/reload-plugins`) so it loads.

If the shorthand doesn't resolve, use the full URL form:

```text
/plugin marketplace add https://github.com/hkasahar/claude-code-skills.git
/plugin install delegate@kasahara-skills
```

**Or copy it manually** (no auto-updates):

```bash
git clone https://github.com/hkasahar/claude-code-skills.git
mkdir -p ~/.claude/skills/delegate
cp -R claude-code-skills/skills/delegate/* ~/.claude/skills/delegate/
```

---

## Step 8 — Verify everything

Set a helper variable pointing at the skill, then run the preflight check:

```bash
# Plugin install exposes CLAUDE_PLUGIN_ROOT; manual install uses ~/.claude:
DELEGATE="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/delegate}"
DELEGATE="${DELEGATE:-$HOME/.claude/skills/delegate}"

bash "$DELEGATE/scripts/preflight.sh"
```

Expected: `codex` and `agy` reported as installed and authenticated (and `claude` too, if you did Step 6). Warnings about the sandbox are normal and don't count as failures.

---

## Step 9 — Your first delegation

```bash
bash "$DELEGATE/scripts/ask_codex.sh" "Reply with exactly: STATUS: DONE — hello world" /tmp/out.md
head -3 /tmp/out.md
```

You should see a `STATUS: DONE` line. That's the whole idea: the heavy thinking happened in Codex, and you (or Claude) only read three lines back.

From now on you don't run these by hand — you tell Claude Code *what* you want ("refactor this function with Codex," "cross-verify this proof") and it picks the right script and template. Read [../skills/delegate/SKILL.md](../skills/delegate/SKILL.md) for the pattern library and [WORKFLOW.md](WORKFLOW.md) for how it fits the bigger loop.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: claude` / `codex` / `agy` | The install folder isn't on your `PATH`. Re-check the `export PATH=...` lines in Steps 2/5, then **close and reopen Terminal** (or `source ~/.zshrc`). |
| `npm install -g` fails with `EACCES` | Don't use `sudo`. Do the `npm config set prefix ~/.npm-global` fix in Step 2. |
| `agy` calls fail with `bind: operation not permitted` | You're inside the sandbox. Make the Bash call with `dangerouslyDisableSandbox: true`, or run from a normal Terminal outside Claude Code. |
| A delegation "hangs" | These jobs can take 30s–several minutes. When calling from Claude Code, set the Bash `timeout` to `600000` ms (the tool's default maximum; the scripts' internal timeouts sit just under it). For genuinely longer jobs see the "Timeout Configuration" section of the delegate `SKILL.md`. Network retries add time. |
| `head -3` shows `STATUS: FAILED` | Read the whole file (`cat /tmp/out.md`) — the failure reason (auth, quota, model) is in there. |
| Sign-in didn't stick | Re-run `codex` / `agy` / `claude` and complete the browser step; make sure your subscription is active. |
| `jq: command not found` (some scripts use it) | `brew install jq`. |

---

## Security

- **Read a script before you run it.** These are short; skim `skills/delegate/scripts/*.sh` so you know what they do.
- **Delegation sends data off your machine** to OpenAI (Codex) and Google (Antigravity). Never delegate confidential, embargoed, or privacy-sensitive material, and never paste API keys, tokens, or passwords into a prompt.
- **Relax the sandbox narrowly.** `dangerouslyDisableSandbox: true` is needed only so `agy` (directly, or via `ask_both.sh`'s Antigravity leg) can bind a local port. Use it only for these delegation calls, never as a global default, and never on a prompt built from untrusted input. `ask_claude.sh` needs no sandbox relaxation (env-token auth; no keychain access).
- **Protect the delegate token.** `~/.claude-delegate/oauth_token` is a long-lived credential: create it with `umask 077`, never paste it into a Claude Code session or a delegation prompt, and note that an agentic delegate (Bash pre-approved) can read its own token by design — treat agentic prompts as trusted code.
- **Guard rails are worth setting up.** Claude Code's "auto" permission mode is a reasonable balance of convenience and safety. A `PreToolUse` hook can block destructive shell operations (e.g. `rm -rf`) and writes to your raw data folders. See the Claude Code docs on hooks and settings.

---

### What does `curl … | bash` mean?

`curl -fsSL <url>` downloads a script; the `|` pipes it straight into `bash`, which runs it. It's convenient but means you're executing code you haven't read. To inspect first, download it, read it, then run it:

```bash
curl -fsSL https://antigravity.google/cli/install.sh -o /tmp/agy-install.sh
less /tmp/agy-install.sh     # press q to quit the viewer
bash /tmp/agy-install.sh     # run it only once you're comfortable
```

This is a good habit for any `curl … | bash` you encounter, not just this one. Prefer official domains (here, `antigravity.google`).
