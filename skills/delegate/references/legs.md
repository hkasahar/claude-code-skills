# Wrapper modes and dependencies

All wrappers accept a literal prompt or `@prompt_file`, plus an optional output path. Helpers resolve relative to the installation directory. Credentials, sessions and runtime sidecars are user state and are not distributed. Inspect the first three output lines for status/provenance, then read full reports when assessing a review.

| Entry point | Default | Controls and dependencies |
| --- | --- | --- |
| `ask_codex.sh` | Codex, `gpt-6.1-sol`, `xhigh`, credit fallback off | `CODEX_MODEL`, `CODEX_EFFORT`, `CODEX_TIMEOUT` (3600 s), deadline-bounded capacity retries. Requires Codex CLI. |
| `ask_codex_batch.sh` | Four concurrent tasks; TSV `output_path<TAB>@prompt_file` | `BATCH_CONCURRENCY`, `BATCH_TIMEOUT` (3600 s); per-task classified status. One explicit fallback mode per batch. |
| `ask_antigravity.sh` | agy, `gemini-4-argon-high`, 600 s, rerouting off | `--model`, `--reroute off|auto|refuse`, `ANTIGRAVITY_VERIFY_MODEL=1`, `ANTIGRAVITY_TIMEOUT`. |
| `ask_both.sh` | Codex `gpt-6-astra`/`max` + verified Gemini Pro; 3600/1800 s | Optional `INCLUDE_GROK=1`, `INCLUDE_CLAUDE=1`; fixed output slots. Every requested leg required; no paid fallback. |
| `ask_grok.sh` | Optional CLI, `grok-4.7`, `xhigh`, tool-denied voter mode | `GROK_DELEGATE_*`; explicit agentic workdir or explicit metered API backend. Required flags fail closed. |
| `ask_claude.sh` | Optional `opus`/`xhigh`, 570 s, tool-free voter mode | `CLAUDE_DELEGATE_*`; separate config/token, recursion guard, explicit agentic workdir. |
| `ask_deepseek.sh` | Optional OpenRouter API, `deepseek/deepseek-v4.1-flash-1017`, fixed `max` effort | `curl`, `jq`, `perl`, SHA-256 utility; key-cap and provider/preset checks. No model tools. |
| `ask_pdf2md.sh` | Sibling gemini-pdf converter, compact status | `PDF2MD_CONVERTER` overrides location; converter dependencies documented by sibling skill. |
| `preflight.sh` | Offline binary/flag checks; Codex + agy required | Grok, Claude, PDF and API dependencies optional. `--live` makes small Codex/agy inference requests. |

Model IDs are configurable code defaults, not availability or ranking claims. Actual served family determines family diversity; the transport name alone does not. Keep concurrency within the authorized resource budget and use an interruptible job controller for long tasks.

## Cost and failure controls

Codex account/model usage limits and server-capacity failures are classified separately. Capacity retries keep the same model and effort and share one deadline. Credit fallback is off by default. Only `--credit-fallback deepseek` (Codex harness) or `--credit-fallback deepseek-api` (self-contained API task) opts in. `CODEX_CREDIT_FALLBACK` is ignored. These are metered routes requiring prior authorization and separate setup.

An opted-in fallback applies only to a clean account-limit failure with proven absent tool calls. Unknown, malformed or mid-run history prevents automatic replay and requires an explicitly planned fresh worktree. Direct DeepSeek selection accepts only the configured admitted model and still runs key/preset checks. Max/ultra requests refuse fallback and direct DeepSeek selection. No fallback, reserve/shadow or Grok API route is allowed in a review round.

The DeepSeek API route checks credit, sends a provider allowlist with data collection denied, and rejects missing/outside-provider provenance. Its baked key-limit ceiling is USD 10; its ledger is telemetry, not spending enforcement. Concurrent requests can still consume available credit. No account configuration or admission evidence ships here.

The Codex-harness DeepSeek route always uses `~/.config/openrouter/key`, ignoring key overrides during both `--key-check` and the child. Keys must be regular, safely permissioned files owned by the current user. `--key-check` makes live HTTP GET requests for credit and preset state. It requires active preset `deepseek-codex`, a nonempty subset of the baked provider allowlist, data collection denied, DeepSeek-only models and no tools/plugins. It also requires a regular user-owned marker, mode at most 0600, at `~/.config/openrouter/deepseek-codex.verified`, matching the canonical preset-config SHA-256.

`--preset-fingerprint` makes the same live key/preset checks without requiring or writing the marker. The marker represents separate review of the exact preset and an authorized live routing verification; offline tests cannot establish that. Record the fingerprint before verification, perform the authorized routing checks, compare a fresh fingerprint, and only then atomically write the matching fingerprint with mode 0600. Changed presets require fresh verification. Never create a marker merely to bypass refusal. Agents with shell access may still read keys: use trusted prompts and rotate exposed credentials.

## Isolation and provenance

Grok voter mode denies tools, disables subagents/web search and disables home-level Claude/Cursor harness imports. Claude voter mode uses a separate config, temporary cwd, zero builtin tools, strict MCP settings when supported and no session persistence. Its token file takes precedence over inherited OAuth; API keys require `CLAUDE_DELEGATE_ALLOW_API_KEY=1`. Explicit agentic modes can execute trusted code and require a workdir. A workdir is not an operating-system jail.

Codex and agy can access files/tools permitted by their clients. This wrapper does not enforce their filesystem or network isolation. Provide only an authorized packet and audit session logs when isolation matters. Metadata records requested/served model, effort or tier, reroute and failure where exposed. agy identity and quota probes are provider operations; offline preflight never makes them. Explicit `--reroute auto` may change the served model/family and records it. Review mode requires Gemini Pro and forbids rerouting. Claude is optional and never silently counted as independent.

Each wrapper has its own deadline. The review wrapper cancels process groups on a failed requested leg or signal. Preserve failures and sidecars; partial output is not completion. Review prompts must request `VERDICT: CORRECT|ERROR|GAP` as the first nonblank body line. Findings require evidence-based adjudication; malformed output or any failed requested leg makes the round incomplete.

## PDF conversion

An explicit `PDF2MD_CONVERTER` wins; otherwise bundled sibling gemini-pdf precedes the manual home fallback. Preflight uses the same layout order with `GEMINI_PDF_SCRIPT` as its explicit override. The PDF watchdog is at least converter deadline + 300 s and cancels the converter process group. The compact envelope reports audit/residual ranges, OCR page counts, freshness and sidecar paths; prior output is marked stale when not replaced.

The confidentiality warning discloses external submission; it does not block it. Obtain relevant permission before dispatch. Paid OCR retains converter page-cap semantics. Reference sync is opt-in through `--bib-key`; separately installed reference indexes may need manual refresh. Batch converters, reference-search and private integrations are not bundled.

## Offline verification

Run tests through the repository's isolated offline launcher. It copies sources to scratch, supplies temporary home/config/cache/reference/ledger paths and a restricted PATH with fail-closed provider/HTTP stubs. Tests layer recording fakes and assert resolution. This verifies packaged behavior under fakes, not live authentication, availability, capacity, provider routing or billing.
