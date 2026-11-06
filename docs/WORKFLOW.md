# A research workflow with checkable results

Use the available agent as the root coordinator. It owns the question, scope, evidence, integration, and final quality gate. Delegate when a bounded worker improves speed or provides useful fresh review. A task does not require every provider.

## Explore, plan, review, implement

1. Inspect the project, branch, instructions, and task ledger. Establish what is already complete.
2. For substantial work, save a concrete plan: inputs, assumptions, permitted edits, deliverables, checks, and stopping conditions. Clarify choices that change the method or claim.
3. Obtain fresh review of the actual saved plan before implementation when independent review is requested or the stakes warrant it. Give the reviewer the original request and source artifacts, without a preferred verdict.
4. Resolve material findings, then implement in a scoped directory. Assign non-overlapping files to concurrent writers.
5. Review against the request, run proportionate checks, inspect the diff, and report evidence and remaining limits.

Separate exploratory suggestions from supported conclusions. A reader should be able to trace important numbers and statements to a source or computation.

## Give each worker one task

A delegation packet contains the objective, necessary context, allowed files/tools, output format, success criterion, and stop rule. Omit irrelevant files and credentials. A tool-free reviewer needs evidence in the approved prompt; an agentic worker needs an explicitly scoped working directory.

A fresh context reduces anchoring. For cross-family review, identify the served model's family rather than its CLI transport. Two sessions of one family are not cross-family evidence. Different families may still make correlated errors.

The public default review uses Codex and Gemini. Extra Grok or Claude requests are explicit options. Fixed output slots are documented in [delegate](../skills/delegate/SKILL.md); omitting a leg does not shift them. Resolve disagreements from assumptions, source text, counterexamples, and calculations. Vote counts do not decide a proof.

## Verify the claim as well as the artifact

| Work | Useful checks | What the check alone cannot establish |
| --- | --- | --- |
| Code and data | Known-answer cases, merge coverage, counts, units, seeds, edge cases, reproducibility | That the specification answers the economic question |
| Empirical analysis | Estimand, identifying variation, uncertainty/clustering, design diagnostics, sensitivity | Causality from a successful program run |
| Proofs | Original theorem statements, explicit steps, counterexamples, symbolic or numerical checks | A proof from agreement or numerical success |
| Manuscripts and slides | Source-to-claim audit, code/table/prose consistency, compiled and rendered pages | Contribution from fluent prose or extra output |
| PDF conversion | Page coverage, numbers/equation tags, findings, residual markers, final status | Perfect transcription from a score or AI audit |

Technical correctness and an important contribution require separate judgments. Verify citations against original text. Search approved local holdings first for sensitive work; obtain permission before disclosing private search terms externally.

Define checks before expanding generated output. Automate invariants, trace conclusions end to end, and inspect secondary material. A sample does not certify its uninspected remainder. Investigate failures and expand the audit when needed.

## Keep context and persistent state useful

Use scripts for deterministic extraction, counting, and file operations; inspect compact results. Shell execution still uses compute and tool messages, so describe it as economical rather than free. Read status/provenance first, then the evidence needed for the decision. A success header alone is insufficient for integrating code, a research claim, or a proof.

Keep standing instructions short and conditional procedures in skills/references. Before clearing or compacting context, record completed checks, active assumptions, unresolved findings, and the next action in the task ledger. Context-size thresholds are workflow choices, not universal model limits.

## Protect research and publication

Keep restricted data, credentials, confidential reports, and unpublished findings out of external prompts without authorization. Public source material and a private research question have different boundaries. Permission to inspect a document locally does not imply permission to upload it.

Before publication, review an allowlisted export and exact staged diff. Exclude raw inputs, private paths, screenshots, logs, session envelopes, caches, backups, and credentials. Check metadata as well as visible text. Preserve licenses and required installation identifiers. A normal commit can sanitize the current tree while older content remains in history, forks, or caches.

The [project instruction template](example-CLAUDE.md) implements these practices without prescribing personal subscriptions, model rankings, or private routing policy.
