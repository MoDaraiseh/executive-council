# executive-council

A Claude Code project that runs a business decision through a simulated executive council — CFO, COO, CMO, CTO, and CHRO — before committing to a final call. Each C-suite role is an independent subagent that reads only its own domain's supporting documents and returns a structured recommendation; a CEO synthesis step then combines all five into one accountable decision, with an explicit rule that a real compliance/legal/security objection can't be steamrolled by a strong financial or market case.

This is a decision-support tool, not a decision-maker. See [Human review](#human-review) below.

## Results at a glance

- **Jurisdiction gate:** in 19/19 live runs across 4 scenarios, the regulatory query ran only when it should. The 14 non-Saudi runs never ran it; the 5 Saudi runs did.
- **All checks:** 18/19 live runs passed every check (95% interval 75–99%). The one failure was a subagent replying in prose instead of JSON.
- **Offline:** 20 self-tests of the harness and the hook run in CI on every push, on Ubuntu and Windows.

Sample sizes, model versions and limits: [examples/VALIDATION_REPORT.md](examples/VALIDATION_REPORT.md).

## How to invoke it

From within Claude Code, in this repo:

```
/council <the decision or question you want evaluated>
```

**`jurisdiction:` is a required field.** Prefix your decision with a line naming the jurisdiction, or `unspecified` if it doesn't have one:

```
/council jurisdiction: KSA

Should we launch this hiring plan in Riyadh?
```

```
/council jurisdiction: unspecified

Should we acquire this competitor's retail chain?
```

This field decides whether the council runs its one regulatory tool (a Saudi-law lookup, see step 4 below). It is read in code, not inferred by the model. `unspecified` is a valid, expected value: it guarantees the regulatory step is skipped rather than guessed at.

The value counts as Saudi if it names Saudi Arabia: `KSA`, `Saudi Arabia`, `Kingdom of Saudi Arabia` or `Saudi`, case-insensitive. Dots and a parenthetical are ignored (`K.S.A.`, `KSA (Riyadh)`), and in a list such as `KSA, UAE` or `Riyadh, KSA` any matching entry counts. Anything else (another country or region, `unspecified`, `unknown`, `non-Saudi`, a blank value, or no field at all) skips the regulatory step, and the final `reasoning` says so. A missing field is treated as `unspecified`; don't rely on that for a decision that actually needs the regulatory check, state the jurisdiction instead. (This replaced free-text jurisdiction inference by the CEO step, which repeatedly assumed Saudi Arabia when nothing in the decision said so; see [examples/VALIDATION_REPORT.md](examples/VALIDATION_REPORT.md).)

That single command is defined in [.claude/commands/council.md](.claude/commands/council.md). Behind it:

1. The CEO (this command, running on `model: opus`, the highest-judgment step in the flow) reads the `jurisdiction:` field, then sends your exact question, unmodified, to all five subagents in [.claude/agents/](.claude/agents/): `cfo-agent`, `coo-agent`, `cmo-agent`, `cto-agent`, `chro-agent`.
2. Each subagent reads every file under its own `context/<role>/` folder (see [The context/ folder](#the-context-folder)) and returns a fixed 5-field JSON structure:
   ```json
   { "role": "cfo | coo | cmo | cto | chro",
     "recommendation": "approve | approve_with_conditions | reject | insufficient_data",
     "confidence": "low | medium | high",
     "key_risks": ["..."],
     "key_benefits": ["..."] }
   ```
   `role` lets the CEO step and the validation harness attribute a reply that arrives asynchronously. A cto/chro reply to the regulatory follow-up may add a sixth field, `conditions`.
3. The CEO step collects all five, applies the decision logic below, and returns one final JSON verdict (`final_decision`, `confidence`, `regulatory_context_consulted`, a `council_summary` of all five replies, `key_disagreements` and `reasoning`); see [.claude/commands/council.md](.claude/commands/council.md) for the exact schema.

### Regulatory lookup dependency (KSA decisions only)

For a decision whose jurisdiction is Saudi and that touches Saudi regulation, the CEO step runs the separate [saudi-law-tracker](https://github.com/MoDaraiseh/saudi-law-tracker) project's `query --json` command, then forwards the rows to `cto-agent` and `chro-agent` for a revised view. [council.md](.claude/commands/council.md) step 5 runs:

```
"${SAUDI_LAW_TRACKER:-saudi-law-tracker}" query --sector <sector> --json
```

Install saudi-law-tracker so `saudi-law-tracker` is on your `PATH`, or set the `SAUDI_LAW_TRACKER` environment variable to the full path of its executable, for example on Windows:

```powershell
setx SAUDI_LAW_TRACKER "C:\path\to\saudi-law-tracker\.venv\Scripts\saudi-law-tracker.exe"
```

Restart Claude Code after setting it. (It can also go in the `env` block of a git-ignored `.claude/settings.local.json`.)

If the command is missing or fails, the council is instructed to carry on, say plainly in its `reasoning` that no regulatory context was available, and never fabricate a citation to fill the gap.

### Jurisdiction gate (enforced in code)

The rule "never run the regulatory query for a non-Saudi decision" is not left to the model. [.claude/settings.json](.claude/settings.json) registers [.claude/hooks/jurisdiction_gate.py](.claude/hooks/jurisdiction_gate.py) as a Claude Code hook, started by [run_gate.sh](.claude/hooks/run_gate.sh), so it applies to every `/council` run in this project, interactive or headless:

- When a prompt starts with `/council`, the hook reads the `jurisdiction:` field itself, using the rule above.
- For the rest of that `/council` run, every Bash and PowerShell call is denied unless the jurisdiction is Saudi **and** the command is the saudi-law-tracker query. Hooks also see tool calls made inside subagents, so handing the command to a subagent doesn't get around it.
- Outside a `/council` run, a saudi-law-tracker command is denied too; other shell commands are unaffected.

The hook needs Python 3.9+ and `sh` (on Windows, the Git Bash that Claude Code already requires). `run_gate.sh` uses whichever of `python3`, `python` or `py -3` actually runs. If none does, or the gate itself fails, it blocks the shell call (exit 2) instead of letting it through. Without the launcher, a hook that can't start is a non-blocking error in Claude Code and the call would go ahead. The cost is that, with no working Python, every Bash and PowerShell call in this project is blocked until Python is fixed. The validation harness doesn't depend on the hook alone: it also removes the shell tools at the CLI level (`--tools`, `--disallowedTools Bash,PowerShell`), and `--no-cli-gate` turns that off to test the hook by itself.

## Decision logic

The CEO synthesis step doesn't just average the five recommendations. Two rules take priority over majority vote:

- **`insufficient_data` is a signal, not a vote to ignore.** If any subagent returns `insufficient_data` — most often because its `context/<role>/` folder was empty or didn't actually address the question — that's treated as "go get more information," not as a data point to average away. Each subagent is instructed to return `insufficient_data` outright if its context folder is empty, rather than guess.
- **Near-veto on compliance/security/legal risk.** If any single subagent raises a real compliance, security, or legal objection, that objection cannot be overridden just because the financial or market case is strong. The CHRO agent in particular is instructed to flag unreviewed legal/contractual exposure as a blocker requiring legal sign-off, and the CTO agent to flag security/compliance risk without softening it for consensus.
- Anywhere else the five disagree without a hard veto, the CEO step is expected to weigh the disagreement against overall priorities and explain its reasoning explicitly, rather than default to majority vote or quietly drop a dissent.

## Human review

**Every decision this council produces is provisional pending a human sign-off.** The command's output instructions require the CEO step to explicitly state, whenever the final decision is `approve` or `approve_with_conditions`, that a human must review the decision before it's acted on. This council is a structuring/synthesis aid over the five domain views — it does not have authority to actually approve or execute anything.

## The context/ folder

Each subagent's judgment is only as good as what it's given to read, so [context/](context/) holds the supporting documents, one subfolder per role:

| Folder | Feeds | What goes there |
|---|---|---|
| `context/finance/` | `cfo-agent` | cash position, budgets, capex/opex forecasts, revenue models |
| `context/operations/` | `coo-agent` | org structure, capacity data, current workload |
| `context/market/` | `cmo-agent` | brand guidelines, customer data, past campaign performance |
| `context/technology/` | `cto-agent` | system architecture, infrastructure docs, prior incidents |
| `context/people/` | `chro-agent` | org structure, headcount plans, past change initiatives |

Each folder ships with its own `README.md` describing what belongs there. Before answering, every subagent is instructed to read every file under its own `context/<role>/` folder — and to return `insufficient_data` outright if the folder is empty, rather than guess. To run a real decision through the council, drop the relevant documents into these folders first; an empty (or README-only) folder is a deliberate signal to the agent that it has nothing to go on.

Because these folders are meant to hold real, often sensitive business documents (budgets, legal memos, headcount data...), everything placed under `context/<role>/` other than the folder's own `README.md` is git-ignored — see [.gitignore](.gitignore). Nothing you drop in there gets committed by default. The validation harness never writes to these folders; it uses a temporary workspace.

Each of the CFO, CTO, and CHRO agents also carries a short **Local context** section covering Saudi Arabia (KSA) regulatory norms — the CFO agent for Sharia-compliant financing, zakat, VAT, and ZATCA e-invoicing; the CTO agent for PDPL data residency and the NCA's Essential Cybersecurity Controls; the CHRO agent for Saudization/Nitaqat bands, KSA notice periods, and end-of-service benefit exposure. Each of these three weighs its KSA-specific items only when the decision's `jurisdiction:` field names Saudi Arabia under the rule above, not from anything inferred from the rest of the decision text.

## Examples and validation

[examples/](examples/) has four worked decisions plus a headless validation harness (`examples/validate.py`):

- a clear approve;
- a compliance red flag that must trigger the near-veto rule;
- an empty-context case that must return `insufficient_data`;
- a `jurisdiction: KSA` hiring plan, the one case where the regulatory query must actually run.

The harness runs each decision through the real agents, command and hook in a throwaway workspace and checks the output against fixed schemas. `python examples/validate.py --self-test` checks the harness itself offline, and CI runs it on every push. See [examples/README.md](examples/README.md) for details, and [examples/VALIDATION_REPORT.md](examples/VALIDATION_REPORT.md) for live results, sample sizes and known limits.

## Example output

The final verdict from a real run of [the compliance red-flag example](examples/02-compliance-red-flag/) on 2026-11-14 (run 1, transcript `4e40ccc193e4` in the [validation report](examples/VALIDATION_REPORT.md)). A marketing send to a 2019 EU contact list with no consent records is rejected, even though the CFO's own figures show $410,000 of upside:

<details>
<summary>Final verdict JSON</summary>

```json
{
  "final_decision": "reject",
  "confidence": "high",
  "regulatory_context_consulted": false,
  "council_summary": {
    "cfo": {
      "recommendation": "reject",
      "confidence": "high"
    },
    "coo": {
      "recommendation": "reject",
      "confidence": "high"
    },
    "cmo": {
      "recommendation": "reject",
      "confidence": "high"
    },
    "cto": {
      "recommendation": "reject",
      "confidence": "high"
    },
    "chro": {
      "recommendation": "reject",
      "confidence": "high"
    }
  },
  "key_disagreements": [],
  "reasoning": "The jurisdiction field was read mechanically and literally as `EU`, which is not a recognized Saudi-jurisdiction value, so this decision is not regulatory-tagged under this council's two-part test and no saudi-law-tracker query was run — the GDPR exposure the council raises is real, but it is not Saudi regulatory subject matter and the council's only regulatory tool is Saudi-specific, so no sourced regulatory rows were available or cited here. All five C-suite agents returned `reject` at high confidence; there was no dissent to record and none has been softened or omitted. The inputs converge on a single binding constraint rather than a balance of risk: the General Counsel memo in `context/people/legal-memo.md` states that the 2019 EU list predates the current consent-tracking system, cannot be verified as GDPR-compliant for marketing use, must not be used without per-contact consent re-confirmation, and is explicitly characterized as \"a blocker, not a risk to be weighed against upside,\" with \"no exceptions for revenue timing pressure.\" The CTO adds an independent and arguably more decisive finding: consent-tracking fields were not added to the CRM until 2021, so there is no per-contact consent record for these 280,000 contacts and the system cannot suppress non-consented recipients at send time — meaning this is not a process delay that urgency can compress, but a missing technical capability with no retroactive fix. The CFO was the only agent to quantify an upside ($410,000 incremental revenue at near-zero marginal send cost) and still rejected, because that figure sits against a GDPR Article 83 ceiling of 4% of global annual revenue; under this council's decision logic a compliance/legal objection acts as a near-veto and is not overridden by financial or market upside, and here the financial lens itself reached the same conclusion. The COO confirmed the send is technically trivial and marketing ops has bandwidth, which isolates the blocker as purely legal/data-compliance rather than capacity. The CMO noted genuine offsetting market factors — strong historical list performance, two competitors running concurrent promotions, on-brand creative — but flagged that the market brief's favorable read was explicitly conditioned on \"assuming the list is clear to use,\" which it is not, and that in EU markets a consent violation becoming public inverts the brand's privacy positioning. The CHRO added culture and personal-liability dimensions: approving this would signal that revenue targets override standing counsel instructions, and staff executing a knowingly non-compliant send carry their own exposure. No agent returned `insufficient_data`, and the figures needed for the call were present in context, so no further information is required before finalizing. Consistent with this council's scope, the compliance flags above are a routing signal, not a legal opinion — the proposal as written should be declined and any urgent request to accelerate re-permissioning routed to Legal directly per the standing memo, with quarter-end revenue pursued through initiatives that do not depend on this list; a compliant alternative worth putting to Legal and Marketing is a send restricted to contacts acquired after the 2021 consent-tracking rollout, whose consent status the CRM can actually verify and suppress against, paired with a separate re-permissioning campaign for the 2019 cohort on a timeline Legal sets rather than one quarter-end dictates. Because this decision is a rejection rather than an approval, the standing human-review reminder is not triggered as an approval condition, but this synthesis should still be reviewed by a human — and specifically by counsel — before any related campaign proceeds."
}
```

</details>

## Known issues

### A user-level `council.md` overrides this one

If you also have `~/.claude/commands/council.md`, Claude Code runs **that** file when you type `/council`, not this repo's copy (confirmed with Claude Code 2.1.270). Project agents do win over `~/.claude/agents/` copies of the same name, but the command doesn't. Delete or update any personal copy so `/council` runs the version in this repo. `examples/validate.py` avoids the problem by running a copy of the command under a different name.

### The CEO model can fall back to your session model

The command's `model: opus` applies to the turn that runs `/council`. If subagent results arrive asynchronously, the CEO's later turns, including the final synthesis, can run on your session's default model instead. This was seen in 1 of 31 harness runs on 2026-11-14. Select Opus for the session when running `/council`; the harness passes `--model opus` for the same reason.

## Subagent model: haiku

The five C-suite subagents (`cfo-agent`, `coo-agent`, `cmo-agent`, `cto-agent`, `chro-agent`) run on **`model: haiku`**, not `sonnet`. In live runs on 2026-11-14 that resolved to `claude-haiku-4-5-20251001`, and the CEO step's `model: opus` to `claude-opus-5`. The harness records the exact IDs for every run.

**Why:** in a small comparison, haiku matched sonnet closely enough on schema validity and decision consistency that sonnet's higher price wasn't justified for a bounded, structured evaluation over a few curated documents.

**How much weight that comparison can carry:** not much. Read it as a preliminary signal, not a benchmark:

- It ran on 2026-09-14, before the `role` field, the `jurisdiction:` field and the jurisdiction gate existed, so it tested earlier agent prompts against the 4-field reply schema of the time.
- It did not go through `examples/validate.py`. The `claude` CLI wasn't available in that environment, so the same decisions and schema checks were run via direct Agent-tool calls instead.
- It used the first three examples only, 2 runs each: n=2 per model per example, 60 subagent replies in total.

| Metric (2026-09-14, n=2 per cell) | haiku | sonnet |
|---|---|---|
| Subagent replies matching the then-current 4-field schema | 30/30 | 30/30 |
| Per-role recommendation agreement, run 1 vs run 2 | 12/15 | 13/15 |
| Compliance red-flag example: reject held across both runs, all 5 roles | 10/10 | 9/10 (one COO reply softened to `approve_with_conditions`, citing the same compliance risk) |
| Council-level `final_decision` matched the strict expected outcome | 4/6 | 5/6 |

Raw results: [examples/model-comparison-results.json](examples/model-comparison-results.json). The differences above are one or two replies, well inside noise at this sample size. To repeat the comparison through the harness against the current prompts, run `python examples/validate.py --subagent-model sonnet` and compare it with a default (haiku) run. That rewrites `model:` only in the throwaway workspace copy, and the report shows which model IDs actually answered. If sonnet comes out clearly better, set `model: sonnet` in each of the five `.claude/agents/*-agent.md` files.

## License

MIT — see [LICENSE](LICENSE).
