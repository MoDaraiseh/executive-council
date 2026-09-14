# /council validation report — real run

This is the actual, unedited output of a real `/council` validation run —
not `--self-test`. `examples/.results/<timestamp>/` (the raw per-run
`.stdout.jsonl` transcripts and `summary.json` this generates) is gitignored
as disposable output; this file is the durable, committed record of one such
run, kept up to date by re-running the command below and replacing this file
wholesale.

**Command:** `python examples/validate.py --runs 3`
**Run at:** 2026-09-14T13:11:08Z (results dir: `20260914-155236`, not committed)
**council.md / agent prompts at time of run:** the XREF-01 regulatory step
(saudi-law-tracker query, `regulatory_context_consulted`) is live in
`.claude/commands/council.md`, and all five `.claude/agents/*-agent.md`
files echo their own `role` in their JSON reply.

## XREF-01 confirmation

Every one of the 9 real runs' final JSON included `regulatory_context_consulted`
(now a required field in `COUNCIL_SCHEMA_FIELDS`), confirmed by directly
parsing each run's final JSON rather than a raw-text grep: `true` for 8 of 9
runs, `false` for one (02-compliance-red-flag, run 3) — varying by run, which
confirms the CEO is actually judging each decision's regulatory-tagging
itself rather than hardcoding the field.

## Note on 01-clear-approve

All 3 runs returned `approve_with_conditions`, not the bare `approve` in
`expected.json`'s primary acceptable set — which is why check (d) reads FAIL
below. Per that example's own documented notes, this is an anticipated soft
variance, not a hard failure: `expected.json` explicitly says
`approve_with_conditions` "would not be a hard failure of the scenario
design ... treat it as a soft warning." `expected.json` was **not** edited
to add it to the acceptable set for this report — the result is reported
faithfully as the harness actually scored it, not adjusted to look cleaner.

## Raw report output

```
# /council validation report

Generated: 2026-09-14T13:11:08.123217+00:00

## 01-clear-approve

Expected final_decision in: ['approve']

| run | (a) 5 calls | (a) 5 distinct roles | (b) subagent schema | (c) council schema | (d) matches expected | final_decision | overall |
|---|---|---|---|---|---|---|---|
| 1 | PASS | PASS | PASS | PASS | FAIL | approve_with_conditions | FAIL |
  - note (run 1): final_decision 'approve_with_conditions' not in acceptable set ['approve']
| 2 | PASS | PASS | PASS | PASS | FAIL | approve_with_conditions | FAIL |
  - note (run 2): final_decision 'approve_with_conditions' not in acceptable set ['approve']
| 3 | PASS | PASS | PASS | PASS | FAIL | approve_with_conditions | FAIL |
  - note (run 3): final_decision 'approve_with_conditions' not in acceptable set ['approve']

**Agreement across 3 runs:** {'approve_with_conditions': 3} (100% agreed on the most common outcome); 0/3 runs fully passed all checks.

## 02-compliance-red-flag

Expected final_decision in: ['reject', 'approve_with_conditions']

| run | (a) 5 calls | (a) 5 distinct roles | (b) subagent schema | (c) council schema | (d) matches expected | final_decision | overall |
|---|---|---|---|---|---|---|---|
| 1 | PASS | PASS | PASS | PASS | PASS | reject | PASS |
| 2 | PASS | PASS | PASS | PASS | PASS | reject | PASS |
| 3 | PASS | PASS | PASS | PASS | PASS | reject | PASS |

**Agreement across 3 runs:** {'reject': 3} (100% agreed on the most common outcome); 3/3 runs fully passed all checks.

## 03-no-context

Expected final_decision in: ['insufficient_data']

| run | (a) 5 calls | (a) 5 distinct roles | (b) subagent schema | (c) council schema | (d) matches expected | final_decision | overall |
|---|---|---|---|---|---|---|---|
| 1 | PASS | PASS | PASS | PASS | PASS | insufficient_data | PASS |
| 2 | PASS | PASS | PASS | PASS | PASS | insufficient_data | PASS |
| 3 | PASS | PASS | PASS | PASS | PASS | insufficient_data | PASS |

**Agreement across 3 runs:** {'insufficient_data': 3} (100% agreed on the most common outcome); 3/3 runs fully passed all checks.

---

**Overall: SOME CHECKS FAILED**

Full results written to: C:\Users\darai_qtguapt\Documents\executive-council\examples\.results\20260914-155236\summary.json
```

## Bottom line

Checks (a) fan-out, (b) subagent schema, and (c) council schema now pass on
**all 9 of 9** real runs — the async role-attribution fix and the
`regulatory_context_consulted` schema addition both hold up under a real,
repeated run, not just a hand-picked single transcript. 6 of 9 runs pass
every check including (d); the other 3 (all `01-clear-approve`) fail only
the documented soft-variance case above.
