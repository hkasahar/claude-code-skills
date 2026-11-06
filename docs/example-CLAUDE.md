# Example project instructions

Adapt this into the instruction file your agent reads, such as `CLAUDE.md` or `AGENTS.md`. Retain rules relevant to the project. Do not store credentials or personal records in instructions.

```markdown
# Project instructions

## Start and resume
- Identify the project and Git branch.
- Read project instructions and tasks/lessons.md when present.
- Use task ledgers as persistent state; checkpoints are supplementary.

## Scope and planning
- Inspect discoverable facts before asking questions.
- Save a checkable plan before substantial edits.
- Clarify choices that change assumptions, methods, claims, or scope.
- Complete required independent plan review before implementation.
- Continue routine, reversible work within the authorized scope.

## Delegation
- The root owns integration and the final quality gate.
- Give workers one bounded task, permitted files, and a return contract.
- Use disjoint files or isolated worktrees for parallel writers.
- Give reviewers original evidence without a preferred verdict.
- Distinguish fresh context from model-family independence.
- Require authorization before external disclosure or paid dispatch.
- Keep credentials and destructive operations with the root.

## Evidence and checks
- Claim a test passed only when it ran successfully in this work.
- Check important claims against original sources or reproducible computation.
- Verify code, tables, figures, and prose agree.
- State estimands and identification assumptions for empirical claims.
- Numerical checks provide evidence; they are not proofs.
- Resolve disagreements from evidence rather than vote totals.
- Bound retries and record failed checks and unresolved issues honestly.

## Persistent state
- Record assumptions, progress, checks, and next actions in task ledgers.
- After substantive corrections, add concise prevention rules.
- Write a concrete handoff before clearing context.

## Publication
- Keep raw inputs, credentials, settings, private paths, logs, and caches local.
- Review an allowlisted export and staged blobs before publication.
- Deleting a current file does not delete public history.
```

Add domain-specific language, notation, style, and test commands in your own project. Keep provider/model choices configurable rather than embedding an account-specific quota policy in every task.
