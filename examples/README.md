# Council examples & validation harness

Four worked decisions for the `/council` command, plus a headless validation
script that runs each one against the real `.claude/agents/*.md`,
`.claude/commands/council.md` and jurisdiction-gate hook, and checks the
output.

## Examples

| Example | Decision | Jurisdiction | Seeded context | Expected `final_decision` | Regulatory step |
|---|---|---|---|---|---|
| [01-clear-approve](01-clear-approve/) | Extend support hours to Saturdays using existing opt-in staff | `unspecified` (nothing in decision.md or context/ names one) | All five `context/<role>/` folders support the change; no red flags | `approve` or `approve_with_conditions` (EC-10: real runs consistently attach a minor CFO documentation caveat) | Must NOT run |
| [02-compliance-red-flag](02-compliance-red-flag/) | Email a pre-GDPR EU contact list for a flash sale | `EU` (the compliance issue is GDPR/EU consent law, not Saudi) | Finance/ops/market look favorable; `people/` carries a General Counsel memo blocking the list, echoed as a data-compliance gap in `technology/` | `reject` or `approve_with_conditions` (never `approve`) | Must NOT run |
| [03-no-context](03-no-context/) | Acquire a competitor's 40-store retail chain for $40M | `unspecified` | No context anywhere (genuinely empty `context/<role>/` folders) | `insufficient_data` | Must NOT run |
| [04-ksa-regulatory](04-ksa-regulatory/) | Open a Riyadh office and hire 12 engineers (10 expatriate, 2 Saudi) | `KSA` | Finance/ops/market/tech favorable; `people/` shows the hiring mix takes Saudization below the company's own Nitaqat line, with a compliant alternative mix | `approve_with_conditions` or `reject` (never `approve`) | MUST run |

`04-ksa-regulatory` is the positive-path case: it shows the regulatory query
still runs, returns data and reaches cto-agent/chro-agent when the jurisdiction
is Saudi, so the gate isn't simply blocking everything. Its check (e) needs the
saudi-law-tracker CLI (see the root README). Without it, (e) is reported as
n/a for that example instead of failing. Its Nitaqat figures are illustrative
internal estimates written for this fixture, not real MHRSD thresholds.

Each example folder has:
- `decision.md` — the exact proposal text, passed verbatim as the command's arguments, starting with a `jurisdiction:` line (required — see the root [README.md](../README.md#how-to-invoke-it)).
- `expected.json` — the acceptable `final_decision` value(s), `expected_regulatory_context_consulted` (checked by check (e)), and for 04 `requires_regulatory_tool`.
- `context/<role>/` — the documents the subagents read for that run (absent for `03-no-context`, by design).

## Running validation

```
python examples/validate.py                    # all 4 examples, 3 runs each
python examples/validate.py --runs 1           # quick single pass
python examples/validate.py --jobs 3           # up to 3 council runs at once
python examples/validate.py --example 02-compliance-red-flag
python examples/validate.py --no-cli-gate      # hook is the only enforcement, as in an interactive run
python examples/validate.py --subagent-model sonnet
python examples/validate.py --self-test        # offline check of the harness itself, no CLI needed
```

A live run needs the `claude` CLI on `PATH`. For each run, the script:

1. Builds a throwaway workspace in the system temp directory: a copy of the
   repo's `.claude/` (agents, command, hook, settings) and a `context/<role>/`
   tree with only that example's fixtures. **The repo's own `context/`
   folders are never written to**, so a crashed run can't leave them
   polluted (an earlier version staged fixtures in place and once did).
2. Runs the command headlessly from that workspace with
   `claude -p --output-format stream-json`, prompt on stdin. The command is
   copied as `/council-under-test`, because a personal
   `~/.claude/commands/council.md` would otherwise take precedence over the
   repo's copy. Tools are limited at the CLI level: `--tools` lists only
   `Agent,Task,Read,Grep,Glob`, adding `Bash` only for a Saudi jurisdiction;
   non-Saudi runs also get `--disallowedTools Bash,PowerShell`, and
   `--strict-mcp-config` removes MCP servers. The hook runs on top of that.
3. Checks the transcript:
   - **(a)** all five roles are invoked, with no unexpected repeats. cto-agent
     and chro-agent may each be invoked a second time for the regulatory
     follow-up; no other role may repeat, and nothing runs three times.
   - **(b)** every subagent reply is valid JSON matching the 5-field schema
     (`role`, `recommendation`, `confidence`, `key_risks`, `key_benefits`),
     with `conditions` as the only optional extra.
   - **(c)** the final council JSON matches its schema, with no extra fields.
   - **(d)** `final_decision` is in the example's acceptable set.
   - **(e)** the saudi-law-tracker query actually ran if and only if the
     example expects it. This is read from the transcript's real tool calls,
     including calls inside subagents, and from the hook's audit log, not
     from the CEO's self-reported `regulatory_context_consulted`. A
     self-report that disagrees with what actually ran also fails (e).
4. Records the Claude Code version and the exact model IDs that answered
   (CEO and each subagent), a SHA-256 of the raw transcript, and what the
   hook allowed or denied. Then it deletes the workspace.

The report gives per-run pass/fail, agreement across runs, and a 95% Wilson
interval on every pass rate. At these sample sizes the interval is the
honest number: 9/9 is compatible with a true pass rate as low as about 70%,
and 3/3 with one as low as about 44%.

Raw transcripts, hook audit logs and `summary.json` go to
`examples/.results/<UTC timestamp>/`. That folder is git-ignored because the
transcripts are large and contain local machine paths. The committed record
is [VALIDATION_REPORT.md](VALIDATION_REPORT.md), which lists each
transcript's SHA-256 so a figure can be traced to a specific raw file.

**Known transcript artifact:** the Claude Code CLI can emit an extra,
phantom `Agent`/`Task` launch in a transcript when the CEO dispatches
several subagents asynchronously in one batch: `subagent_type:
"general-purpose"`, `description`/`prompt` both literally `"placeholder"`,
and no result ever returned (seen with claude-code 2.1.270).
`parse_transcript` filters these out before checks (a)/(b) see them (see
`_is_placeholder_launch_artifact`). If a run still shows a 6th invocation
after that filter, treat it as a real regression.

**Live vs. offline:** a live run makes real model calls (each council run is
the CEO step plus five or seven subagent calls), so it costs real usage.
`--self-test` needs neither the CLI nor any model: its 20 tests cover
transcript parsing, checks (a)–(e) including several failure modes first
seen in live runs, the jurisdiction rule, the hook run as a real subprocess
(directly and through `run_gate.sh`, including a machine with only `python3`),
the CLI flags, workspace isolation and the report statistics. CI runs it on
every push.

## Subagent model comparison (haiku vs sonnet)

An early, small comparison (2026-09-14, n=2 per cell, run before the current
prompts and outside this harness) was used to choose haiku for the
subagents. See [../README.md](../README.md#subagent-model-haiku) for the
numbers and their limits, and
[model-comparison-results.json](model-comparison-results.json) for the raw
data. `--subagent-model` repeats the comparison through this harness.
