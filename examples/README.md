# Council examples & validation harness

Three worked decisions for the `/council` skill, plus a headless validation
script that runs each one against the real `.claude/agents/*.md` /
`.claude/commands/council.md` setup and checks the output.

## Examples

| Example | Decision | Jurisdiction | Seeded context | Expected `final_decision` | Regulatory step |
|---|---|---|---|---|---|
| [01-clear-approve](01-clear-approve/) | Extend support hours to Saturdays using existing opt-in staff | `unspecified` (verified 2026-09-15: nothing in decision.md or context/ ever names a jurisdiction) | All five `context/<role>/` folders support the change; no red flags | `approve` or `approve_with_conditions` (EC-10: real runs consistently attach a minor CFO documentation caveat) | Must NOT run |
| [02-compliance-red-flag](02-compliance-red-flag/) | Email a pre-GDPR EU contact list for a flash sale | `EU` (the compliance issue is genuinely GDPR/EU consent law, not Saudi) | Finance/ops/market look favorable; `people/` carries a General Counsel memo blocking the list, echoed as a data-compliance gap in `technology/` | `reject` or `approve_with_conditions` (never `approve`) | Must NOT run |
| [03-no-context](03-no-context/) | Acquire a competitor's 40-store retail chain for $40M | `unspecified` | No context anywhere (real `context/<role>/` folders are staged genuinely empty for the run) | `insufficient_data` | Must NOT run |

Every example here is jurisdiction-`unspecified` or non-Saudi — none of the three currently exercises the regulatory step's positive path against a real live decision (only `--self-test`'s `build_regulatory_followup_transcript` fixture covers that path, synthetically). `01-clear-approve` was originally assumed to be a KSA scenario; live re-verification (2026-09-15, see `VALIDATION_REPORT.md`) found it never actually stated one, and the CFO's recurring "zakat" caveat there had been the same unjustified-jurisdiction bug this suite's check (e) now catches, not a real scenario feature.

Each example folder has:
- `decision.md` — the exact proposal text, passed verbatim as `$ARGUMENTS` to `/council`, starting with a `jurisdiction:` line (required — see the root [README.md](../README.md#how-to-invoke-it)).
- `expected.json` — the acceptable `final_decision` value(s), an optional `expected_regulatory_context_consulted` (true/false) checked by check (e), and why.
- `context/<role>/` — the documents to stage into the repo's real `context/<role>/` folders before running (absent for `03-no-context`, by design).

## Running validation

```
python examples/validate.py                    # all 3 examples, 3 runs each
python examples/validate.py --runs 1            # quick single pass
python examples/validate.py --example 02-compliance-red-flag
python examples/validate.py --self-test         # exercise the harness's own parsing/validation logic, no CLI needed
```

Requires the `claude` CLI on `PATH` (the script shells out to
`claude -p "/council <decision>" --output-format stream-json`). For each run,
the script:

1. Backs up the repo's real `context/<role>/` folders.
2. Clears them and copies in that example's `context/<role>/` files (or leaves
   them empty, for `03-no-context`).
3. Runs `/council` headlessly and parses the full tool-call transcript.
4. Checks: (a) all five roles are invoked at least once, with no unexpected
   repeats (cto-agent/chro-agent may legitimately be invoked a second time,
   for XREF-01's regulatory follow-up — never any other role, and never a
   third time); (b) every subagent reply is valid JSON matching the 5-field
   schema (`conditions` is an optional 6th field, added by a regulatory
   follow-up reply that revises a recommendation), with no extra top-level
   fields beyond that; (c) the final council JSON matches its schema, with
   no extra top-level fields beyond it either; (d) `final_decision` is in
   the example's acceptable set; (e) when an example declares an
   `expected_regulatory_context_consulted`, whether the saudi-law-tracker
   Bash query actually ran (detected from the transcript's real tool_use
   calls, not the CEO's self-reported `regulatory_context_consulted` field)
   matches it — this is a defense-in-depth backstop for the `jurisdiction:`
   gate (see the root README), watching for the failure mode in case
   anything ever bypasses that gate.
5. Restores the original `context/<role>/` contents (always, even on error).

**Known transcript artifact:** the Claude Code CLI can emit an extra,
phantom `Agent`/`Task` launch in a transcript when the CEO dispatches
several subagents asynchronously in one batch — `subagent_type:
"general-purpose"`, `description`/`prompt` both literally `"placeholder"`,
and no result ever returned (confirmed live 2026-09-14, claude-code
2.1.270, alongside two genuine async launches in the same
`background_tasks_changed` event). This is a client-side quirk, not
anything council.md or a subagent prompt causes. `parse_transcript` filters
these out before check (a)/(b) ever see them (see
`_is_placeholder_launch_artifact` in `validate.py`) — if a run still shows
a 6th invocation after that filter, treat it as a real regression, not this
artifact recurring.

It repeats each example the requested number of times (default 3) and reports
per-run pass/fail plus agreement across runs — e.g. whether all 3 runs landed
on the same `final_decision`, and whether that decision was the expected one.
Raw transcripts and a machine-readable `summary.json` are written to
`examples/.results/<timestamp>/` for debugging; that folder is generated
output, not source, and can be deleted freely.

**Live vs. offline:** a real run needs the `claude` CLI and makes live model
calls (each council run invokes the CEO step plus five subagents), so it costs
real usage. `--self-test` needs neither: its 12 canned transcripts exercise
the harness's own parsing and checks (a)–(e), including several failure modes
first seen in live runs. The live results and how each finding was verified are
recorded in [VALIDATION_REPORT.md](VALIDATION_REPORT.md) (latest full live
batch: 2026-09-16).

## Subagent model comparison (haiku vs sonnet)

These three examples were also used to decide the subagent model — see
[../README.md](../README.md#subagent-model-haiku) for the measured numbers and
the reasoning, and [model-comparison-results.json](model-comparison-results.json)
for the raw per-run data. Short version: haiku was kept, since it matched
sonnet closely enough on schema validity and decision consistency (including
on the compliance red-flag case) that the cost step-up wasn't justified.
