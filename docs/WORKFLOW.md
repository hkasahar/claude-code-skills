# The workflow: orchestrate, delegate, verify

This skill exists to support one working style: **Claude Code as an orchestrator that delegates heavy work to other models and owns the quality gate.** This page distills that workflow, adapted from H. Kasahara, *"Using LLMs and Generative AI for Economics Research"* (2026) (slides: [`llm_research_slides.pdf`](llm_research_slides.pdf)). Everything here is generic — swap in your own tools and skills.

## The core idea

No single model dominates. Each has a comparative advantage:

- **Claude Code** — agentic orchestration, long-horizon tasks, tool use, and the final judgment call.
- **Codex (GPT)** — math, computation, simulation, and code.
- **Antigravity (Gemini)** — very large context: reading and reasoning over whole papers and long literatures.

So use all of them: let Claude Code plan and integrate, push the heavy lifting out to Codex and Antigravity, and cross-check anything that matters. Delegation is done through *subagents* so each job runs in its own context window and returns only a short summary — the main context stays clean for the work you actually supervise.

## A routing table

Decide where each piece of work goes. Evaluate top to bottom; **first match wins**. (This is a template — the "specialized tool" rows are wherever you'd point domain-specific skills of your own.)

| # | If the task… | Route it to | Example |
|---|---|---|---|
| 1 | needs iterative local feedback loops (edit → run → test → fix) | **Claude Code** | debugging across files, CI fixes, a migration with rollback |
| 2 | touches secrets, credentials, or destructive/stateful operations | **Claude Code** | API keys, deploys, `rm -rf`, writes to real data |
| 3 | is a **high-stakes** proof, derivation, or identification argument | **cross-verify** (Codex **and** Antigravity; Claude CLI breaks ties) | a proposition, theorem, or lemma your result depends on |
| 4 | is routine verification of a known result or a mechanical algebra check | **Antigravity** | "does Theorem 2 in X actually say Y?" |
| 5 | is code: implement, debug, refactor, test, simulate, wrangle data, mechanical LaTeX | **Codex** | a bootstrap CI routine, a booktabs table, a regex |
| 6 | is reading/reasoning over long text: literature, citations, summaries | **Antigravity** | a scooping check, a citation audit, a paper summary |
| 7 | needs a specialized tool you've set up | **that tool** | large multi-source synthesis, a domain checker, cross-session memory |
| 8 | is drafting prose or teaching material | **Antigravity draft → Claude edit** | an intro paragraph, lecture notes |

If nothing matches cleanly, decompose the task and route each piece. For a trivial fix (typo, off-by-one, missing import), skip the table and just do it — delegation overhead isn't worth it.

## The authoring loop: Explore → Plan → Review → Implement

For anything non-trivial, don't dive straight into edits. Run this loop:

1. **Explore.** Have Claude read the relevant files and understand the terrain first.
2. **Plan.** Switch Claude Code into **plan mode** (`Shift+Tab`) and give it *one* detailed prompt. End planning prompts with **"Ask me any questions"** so it interviews you before committing — precise input means fewer corrections. Avoid long back-and-forth chats; token cost grows fast with conversation length.
3. **Review.** Have the plan reviewed by a *different* model before you build — e.g. delegate the plan to Codex with `ask_codex.sh` using the `compact_review.txt` template. A second model catches what the author (human or AI) is blind to.
4. **Implement.** Only now write the code — ideally in a fresh context, so the window is clean for the work itself.

Why bother? Quality goes up (fewer errors reach implementation) *and* tokens go down (the exploration/planning context can be cleared before the build).

## Read discipline: "Bash is all you need"

Tokens are spent reading and writing in the context window — so don't spend them on things a script can do for free.

- **Zero-token execution.** Let the model run a shell command and read three lines of output, not paste a whole file into context.
- **`head -3`, not `cat`.** Every delegation writes its result to a file with a `STATUS:`/`VERDICT:` header on top. Read the header; open the full file only when the header says something went wrong.
- **Make a skill for anything you do more than twice.** A skill is a folder with a short instruction file and some shell/Python scripts the model runs — like this one.

## Cross-verification and the third voter

For high-stakes claims, one opinion isn't enough:

- **`ask_both.sh`** sends the same prompt to Codex and Antigravity in parallel. Compare the two verdicts. **Agreement** is reassuring; **disagreement halts** the pipeline until you resolve it.
- When the two disagree, add a **third voter**: `INCLUDE_CLAUDE=1 ask_both.sh …` brings in the Claude CLI for a majority-of-3. (Caveat: the Claude voter shares a model family with the orchestrator, so treat it as mechanical disagreement-detection, not fully independent triangulation.)
- **You own the final verdict.** The models assist; the human integrates and decides.

## Verification as the load-bearing idea

In software, "looks done" is caught by a test or a build. In research it isn't: a hidden gap in a proof, or a consistent-looking but inconsistent estimator, survives every automatic check and a confident summary. The error is cheap to catch early and expensive once it reaches a referee. So build an explicit **pass/fail gate** for each checkable thing and wire it into your skills and hooks so it runs every time. A few canonical gates:

| Kind of work | A canonical check | Stop rule |
|---|---|---|
| Theory, proofs | cross-agent re-derivation; formalization | disagreement halts; the theorem must compile |
| Reduced-form empirics | placebo test; known-effect recovery | effect must vanish on placebo, reappear on truth |
| Structural / macro | residuals ≈ 0; moment match | off target ⇒ do not report |
| Estimation / Monte Carlo | coverage in band; sandwich SE vs. MC SD | undercoverage ⇒ diagnose, never patch |

## Putting it together

A useful mental model has four layers, each a place to intervene:

**Prompt** (write one instruction well) → **Context** (curate what the model sees) → **Harness** (equip what the model can do — tools, subagents, skills like this one) → **Loop** (structure how it iterates — verification, delegation, stopping rules).

`delegate` lives in the *harness* layer: it expands what Claude Code can do by giving it two more models to hand work to, and the read discipline to do so cheaply. The [example `CLAUDE.md`](example-CLAUDE.md) shows how to encode the routing table and these principles as standing instructions.
