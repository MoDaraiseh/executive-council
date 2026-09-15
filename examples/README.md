# Council examples & validation harness

Three worked decisions for the `/council` skill, plus a headless validation
script that runs each one against the real `.claude/agents/*.md` /
`.claude/commands/council.md` setup and checks the output.

## Examples

| Example | Decision | Seeded context | Expected `final_decision` |
|---|---|---|---|
| [01-clear-approve](01-clear-approve/) | Extend support hours to Saturdays using existing opt-in staff | All five `context/<role>/` folders support the change; no red flags | `approve` or `approve_with_conditions` (EC-10: real runs consistently attach a minor CFO documentation caveat) |
| [02-compliance-red-flag](02-compliance-red-flag/) | Email a pre-GDPR EU contact list for a flash sale | Finance/ops/market look favorable; `people/` carries a General Counsel memo blocking the list, echoed as a data-compliance gap in `technology/` | `reject` or `approve_with_conditions` (never `approve`) |
| [03-no-context](03-no-context/) | Acquire a competitor's 40-store retail chain for $40M | No context anywhere (real `context/<role>/` folders are staged genuinely empty for the run) | `insufficient_data` |

Each example folder has:
- `decision.md` — the exact proposal text, passed verbatim as `$ARGUMENTS` to `/council`.
- `expected.json` — the acceptable `final_decision` value(s) and why.
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
   follow-up reply that revises a recommendation); (c) the final council
   JSON matches its schema; (d) `final_decision` is in the example's
   acceptable set.
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

**Note:** this sandbox does not have the `claude` CLI on `PATH`, so the script
was validated here only via `--self-test` (canned transcripts covering a
well-formed run, a run missing an invocation with a malformed subagent reply,
and a run with an unexpected `final_decision`) plus a manual check of the
context staging/restore logic — both pass. Run the script for real wherever
`claude` is installed to get an actual live report against the CLI.

## Subagent model comparison (haiku vs sonnet)

These three examples were also used to decide the subagent model — see
[../README.md](../README.md#subagent-model-haiku) for the measured numbers and
the reasoning, and [model-comparison-results.json](model-comparison-results.json)
for the raw per-run data. Short version: haiku was kept, since it matched
sonnet closely enough on schema validity and decision consistency (including
on the compliance red-flag case) that the cost step-up wasn't justified.
