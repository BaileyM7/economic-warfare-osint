## Summary
<!-- One-paragraph description of what this PR changes and WHY. -->

## Type of change
- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behavior change)
- [ ] Docs / chore
- [ ] Security / dependency update

## Pre-merge checklist
- [ ] Tests added or updated for the change
- [ ] No new secrets committed (run `python scripts/redact-cassettes.py` if cassettes were touched)
- [ ] No regressions in existing tests (`pytest tests/`)
- [ ] If changing API surface: docs / OpenAPI examples updated
- [ ] If touching `src/routers/*`: confirmed rate-limit + auth still applied
- [ ] If touching frontend: ran `npm run build` locally

## Test plan
<!-- How did you verify this works? Include commands run, screenshots, or
     curl examples. CI gates the obvious cases; this section captures
     the manual / visual checks that CI can't. -->

## Rollback plan
<!-- If this PR causes issues in prod, what's the recovery? Usually "revert
     the merge commit" — but if there's anything stateful (DB migration,
     env var change), call it out here. -->
