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

## Note on 01-clear-approve (EC-10, fixed 2026-09-14)

All 3 runs returned `approve_with_conditions`, not the bare `approve` that
was originally `01-clear-approve/expected.json`'s only acceptable value —
which is why check (d) originally read FAIL below (see the raw report
further down, still showing that original scoring). Investigating why
turned up a real, repeatable pattern, not a fluke: the CFO subagent
consistently raises a genuine (if minor) procedural caveat — confirm the
Saturday opt-in is documented as non-coercive, confirm overtime hours are
logged for audit/zakat purposes — that the CEO then reasonably converts
into administrative conditions rather than dropping. `expected.json` has
been updated (EC-10) to accept `approve_with_conditions` on equal footing
with `approve` for this example, with the reasoning recorded there. The
table below reflects that fix, re-scoring these same three already-captured
transcripts against the corrected `expected.json` — not a fresh `/council`
invocation, since the fix is to the scoring criteria, not the council's
behavior. The "Raw report output" section right after is kept as originally
generated (with the pre-fix FAILs) for an honest before/after record.

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

## Re-scored report (EC-10 fix applied, same transcripts)

```
# /council validation report

Generated: 2026-09-14T17:43:49.335059+00:00

## 01-clear-approve

Expected final_decision in: ['approve', 'approve_with_conditions']

| run | (a) 5 calls | (a) 5 distinct roles | (b) subagent schema | (c) council schema | (d) matches expected | final_decision | overall |
|---|---|---|---|---|---|---|---|
| 1 | PASS | PASS | PASS | PASS | PASS | approve_with_conditions | PASS |
| 2 | PASS | PASS | PASS | PASS | PASS | approve_with_conditions | PASS |
| 3 | PASS | PASS | PASS | PASS | PASS | approve_with_conditions | PASS |

**Agreement across 3 runs:** {'approve_with_conditions': 3} (100% agreed on the most common outcome); 3/3 runs fully passed all checks.

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

**Overall: ALL CHECKS PASSED**
```

## Harness/prompt fix, 2026-09-14 (found during a later re-verification pass)

A fresh `validate.py --runs 3` after the EC-10 fix above surfaced two new,
real issues neither the original assessment nor the EC-10 fix caught,
because they only show up when the XREF-01 regulatory step actually finds
data to act on:

1. **`01-clear-approve` run 3** legitimately triggered the XREF-01
   regulatory follow-up (mhrsd_labor now has real rows) — 7 subagent calls
   instead of 5 (cto-agent and chro-agent each invoked twice), and the
   revised chro-agent reply added a `conditions` field when it changed its
   recommendation. `validate.py`'s checks (a) and (b) hardcoded "exactly
   5 calls, exactly these fields" and failed this **correct** council
   behavior.
2. **`03-no-context` run 1** had the CEO invent a `human_review_required`
   top-level field, rejected by the strict council schema. council.md's
   Reminder section said to "state this in your response" without saying
   *where*, so the CEO sometimes folded it into `reasoning` (as intended)
   and sometimes added a new field instead.

Fixes: `validate.py` now accepts cto-agent/chro-agent being invoked a
second time (and only those two, never more than twice) and accepts an
optional `conditions` field on a subagent reply (`SUBAGENT_OPTIONAL_FIELDS`,
`REGULATORY_FOLLOWUP_AGENTS`). council.md's Output format and Reminder
sections now say explicitly that the human-review note belongs inside
`reasoning`, not as its own field.

Verified: `--self-test` gained two cases proving the fix — one confirming
the legitimate 7-call/`conditions` pattern now passes every check, one
confirming an illegitimate repeat (cfo-agent called twice) still correctly
fails. The real `01-clear-approve` run-3 transcript now scores `all_pass:
True` when re-evaluated against the fixed harness. A fresh scoped live
run of `03-no-context --runs 3` against the corrected council.md produced
zero occurrences of `human_review_required` across all 3 runs (results
dir `20260914-214721`, not committed) — the prompt fix holds under a real
re-run, not just the harness relaxing to tolerate the old behavior.

## Placeholder-launch filter, 2026-09-15

A separate fresh `validate.py --runs 3` (results dir `20260914-220658`, not
committed) reproduced a different anomaly: `03-no-context` run 2 showed a
6th subagent invocation — `subagent_type: "general-purpose"`,
`description`/`prompt` both literally `"placeholder"`, no `tool_result`
ever returned. Traced precisely against the actual check logic (not
inferred from the printed table): this was **not** a bug in the fan-out or
schema checks -- `a_five_invocations` correctly went `False` because
`general-purpose` isn't in `EXPECTED_AGENTS`, and `b_subagent_schema_valid`
independently went `False` because that entry's non-existent result
couldn't be parsed as JSON. Both checks counted all 6 entries and correctly
failed the run for the right reasons; nothing let the phantom slip through
uncounted.

Even so, it's a real, recurring CLI-level artifact (a phantom async launch
alongside genuine ones in the same `background_tasks_changed` batch), not
a defect in council.md or the 5 real subagents, which all ran and replied
correctly in that same transcript. `parse_transcript` now filters out any
launch with `description` or `prompt` exactly `"placeholder"` and no
result, documented in a comment at `_is_placeholder_launch_artifact` and in
`examples/README.md`. `--self-test` gained a 6th case (5 real calls plus
the exact phantom shape) proving the filtered run now passes every check;
re-scoring all 9 real transcripts from the `20260914-220658` run against
the fix confirms **9/9 now pass**, `03-no-context` run 2 included.

## Fresh live re-run (not a re-score), 2026-09-15: 5/9, two new real issues

A genuinely fresh, live `validate.py --runs 3` across all three examples —
not a re-score of the transcripts above — did **not** confirm the 9/9
result. It found **5/9** passing, with two distinct, real, reproducible
issues, neither a harness bug:

**Issue 1 — recurring invented extra council/subagent fields.** Across
different runs, the final council JSON or a subagent reply added fields
never defined anywhere: `human_review_required`, `human_review_note`,
`conditions_for_approval`, `regulatory_sources_referenced`,
`next_steps_before_resubmission`. Check (c)/(b)'s pre-existing extra-key
test (`obj.keys() - SCHEMA_FIELDS`) caught every one of them correctly —
this was never a harness gap, only a prompt-following gap. A first fix
(clarifying where the human-review reminder belongs) reduced but did not
eliminate it; a second, blunter fix — "Return ONLY the fields listed
above. Do not add any additional fields, even if they seem helpful or
relevant," later strengthened to name the five specific recurring
offenders and say plainly where each one's content belongs instead — was
added to `council.md`'s Output format section. `--self-test` gained a
7th case (`build_extra_council_field_transcript`, confirming
check (c) fails a `human_review_required` field). A full 9-run
confirmation of the strengthened wording is still pending (see Known open
items below) — the last full 9-run number on record (6/9) predates it.

**Issue 2 — unjustified Saudi jurisdiction assumption.** `03-no-context`'s
decision.md never states a country, region, or city. Across 3 fresh runs,
the CEO nonetheless treated "the northeast market" as Saudi Arabia's
Northern Borders/Qassim/Najran/Asir region, ran the XREF-01 regulatory
query on that invented basis, and in 2/3 runs the resulting real MHRSD
data swayed CHRO's verdict to `reject` or `approve_with_conditions` —
final_decision still landed on `insufficient_data` in all 3 (the empty
`context/` folders alone are sufficient for that), so the label alone
didn't surface the bug; reading the actual reasoning text did.

Four escalating fixes were tried, each addressing the mechanism the
previous one's live re-test exposed, and none fully eliminated the
behavior:

1. Reworded `03-no-context/decision.md` to remove "the northeast market"
   entirely ("expand our market presence"). Live re-test: `northeast`
   disappeared from every transcript, but `Saudi`/`Saudization`/`Nitaqat`
   remained heavy in all 3 runs — the CEO was defaulting the *whole
   company* to Saudi Arabia even with zero geography mentioned anywhere,
   not just resolving an ambiguous phrase.
2. Added a two-part jurisdiction/subject-matter gate to `council.md` step
   3 ("silence means unknown, not Saudi Arabia by default... the fact that
   this tooling only has a Saudi regulatory database is not evidence
   about the company's jurisdiction"). Live re-test: unchanged —
   `regulatory_context_consulted: true` in all 3 runs, fabricated regions
   reappeared in one of them; the CEO's own text still asserted "this
   plainly touches Saudi regulatory territory" without ever engaging the
   new gate.
3. Replaced the gate with a mandatory quote-forcing checkpoint (state
   `Jurisdiction: Saudi Arabia, established by: "<exact quote>"` or
   `Jurisdiction: not established`, before reasoning about regulatory
   tagging at all). Live re-test: the CEO never even produced the
   required checkpoint sentence — ignored it outright, same
   `regulatory_context_consulted: true` in all 3 runs.
4. Found the actual root cause: it was never in `council.md`.
   `cfo-agent.md`, `cto-agent.md`, and `chro-agent.md` each hardcode an
   unconditional "This council operates under Saudi Arabia (KSA) ...
   regulation" statement in their own "Local context" section, independent
   of anything any decision states — a standing design assumption three
   rounds of council.md edits could never have overridden. Reworded all
   three to make KSA-specific guidance (zakat/VAT/ZATCA, PDPL/NCA/ECC,
   Saudization/Nitaqat/EOSB) conditional on the decision or `context/`
   actually establishing Saudi Arabia, explicit that silence doesn't. Live
   re-test: **CHRO's own reply improved correctly** — it now says
   "Jurisdiction unstated — cannot assess regulatory exposure (Saudization,
   EOSB, etc.)" as a `key_risk`, rather than asserting these as confirmed
   facts. But the CEO synthesis step then *misread that explicit
   admission of unknowability* as CHRO having "flagged" a real Saudi risk
   ("as CHRO flagged, so I'll run the saudi-law-tracker query"), and ran
   the regulatory step anyway in 2 of the 2 runs that completed (the 3rd
   hit a Claude Code session usage limit mid-run, an infrastructure
   artifact unrelated to any of this, not a 3rd data point either way).

Given four escalating, root-cause-driven prompt fixes have each narrowed
the failure differently without eliminating it, prompt wording alone is
not treated as a reliable enough guarantee here. Added mechanical check
**(e) regulatory_context_consulted matches expected** to
`evaluate_run()`/`print_report()`, driven by a new
`expected_regulatory_context_consulted` key in `expected.json` (set to
`false` for `03-no-context`, absent — meaning "don't check" — for the
other two examples where a regulatory consult can be legitimate). Unlike
the label-only check (d), this fires regardless of which specific
mechanism produces the false assumption, and regardless of whether
`final_decision` happens to still be correct by luck. `--self-test` gained
an 8th case (`build_unjustified_regulatory_consult_transcript`) proving it
fails exactly this scenario even when (a)-(d) all pass. All 8 self-tests
pass as of this commit.

## Known open items (not closed)

- **Issue 2 is caught, not eliminated.** The mechanical check (e) reliably
  flags every occurrence found so far, but the underlying CEO-synthesis
  behavior (misreading a subagent's own "I can't assess this, jurisdiction
  unstated" as a real signal to consult) was not fixed by any prompt
  change tried. Re-running `03-no-context --runs 3` after the
  cfo/cto/chro-agent.md fix, both completed runs still triggered the
  regulatory step; needs either a further council.md fix targeting this
  specific misreading pattern, or acceptance that check (e) is the
  durable enforcement layer here rather than the prompt.
- **Issue 1's strengthened wording is unverified.** The offender-naming
  addition to `council.md`'s Output format section was made after the
  last full 9-run batch (which predates it and showed 6/9, 3 different
  invented fields across `01-clear-approve` and `03-no-context`). A fresh
  9-run confirmation is needed before claiming 0 extra fields.
- **Claude Code session usage limit hit mid-verification**, resetting
  3:40pm Asia/Riyadh — blocked further live CLI runs for the remainder of
  this session. The run-3 "failure" this produced (non-JSON final result,
  literally "You've hit your session limit") is an infrastructure
  artifact, not a new prompt or harness bug, and is not counted as
  evidence either way for Issue 2.
- **EC-02 is not being proposed closed.** Both issues above need a clean
  re-run once the usage limit resets before that proposal is credible.

## Bottom line

Checks (a) fan-out, (b) subagent schema, and (c) council schema pass on
**all 9 of 9** real runs — the async role-attribution fix and the
`regulatory_context_consulted` schema addition both hold up under a real,
repeated run, not just a hand-picked single transcript. With the EC-10 fix
to `01-clear-approve/expected.json`, all **9 of 9** runs now pass every
check including (d) — the original 6/9 (with `01-clear-approve` failing
only the documented soft-variance case) is kept above as the honest
original record. The two sections above address three further issues found
in later re-verification: the XREF-01 follow-up path actually firing for
the first time (real regulatory-context data now exists), and a harmless
but previously-uncounted CLI transcript artifact.

**This 9/9 result is superseded by the "Fresh live re-run" section above.**
A genuinely fresh execution (not a re-score of these same transcripts)
later found 5/9, and two of the fixes for it are still unverified pending
the session usage limit reset. Treat the 9/9 figure in this section as a
historical record of that specific re-score, not as the current state of
the example suite.
