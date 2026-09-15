# executive-council

A Claude Code project that runs a business decision through a simulated executive council — CFO, COO, CMO, CTO, and CHRO — before committing to a final call. Each C-suite role is an independent subagent that reads only its own domain's supporting documents and returns a structured recommendation; a CEO synthesis step then combines all five into one accountable decision, with an explicit rule that a real compliance/legal/security objection can't be steamrolled by a strong financial or market case.

This is a decision-support tool, not a decision-maker. See [Human review](#human-review) below.

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

This isn't optional decoration — it's how the council decides whether to run its one regulatory tool (a Saudi-law lookup, see step 4 below), and it's read mechanically, not inferred. `unspecified` is a fully valid, expected value: it guarantees the regulatory step is skipped rather than guessed at. Only `KSA` and `Saudi Arabia` (case-insensitive) are recognized as Saudi jurisdiction; anything else — a different country/region name, `unspecified`, `unknown`, or the field being left off entirely — skips the regulatory step and is noted plainly in the final `reasoning`. If you omit the field, the council still runs, but treats it as `unspecified` rather than guessing your company's jurisdiction from the rest of the decision text — do not rely on that fallback for a decision that actually needs the regulatory check; state the jurisdiction explicitly instead. (This replaced free-text jurisdiction inference by the CEO step, which repeatedly assumed Saudi Arabia by default even when nothing in the decision said so — see [examples/VALIDATION_REPORT.md](examples/VALIDATION_REPORT.md).)

For example:

```
/council jurisdiction: unspecified

Should we extend customer support to Saturdays using existing opt-in part-time staff?
```

That single command is defined in [.claude/commands/council.md](.claude/commands/council.md). Behind it:

1. The CEO (this command, running on `model: opus` — the highest-judgment step in the flow) reads the `jurisdiction:` field mechanically, then sends your exact question, unmodified, to all five subagents in [.claude/agents/](.claude/agents/): `cfo-agent`, `coo-agent`, `cmo-agent`, `cto-agent`, `chro-agent`.
2. Each subagent reads every file under its own `context/<role>/` folder (see [The context/ folder](#the-context-folder)) and returns a fixed JSON structure:
   ```json
   { "recommendation": "approve | approve_with_conditions | reject | insufficient_data",
     "confidence": "low | medium | high",
     "key_risks": ["..."],
     "key_benefits": ["..."] }
   ```
3. The CEO step collects all five, applies the decision logic below, and returns one final JSON verdict (same shape, plus a `council_summary` of all five replies and a `key_disagreements` list) — see [.claude/commands/council.md](.claude/commands/council.md) for the exact schema.

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

Because these folders are meant to hold real, often sensitive business documents (budgets, legal memos, headcount data...), everything placed under `context/<role>/` other than the folder's own `README.md` is git-ignored — see [.gitignore](.gitignore). Nothing you drop in there gets committed by default.

Each of the CFO, CTO, and CHRO agents also carries a short **Local context** section covering Saudi Arabia (KSA) regulatory norms — the CFO agent for Sharia-compliant financing, zakat, VAT, and ZATCA e-invoicing; the CTO agent for PDPL data residency and the NCA's Essential Cybersecurity Controls; the CHRO agent for Saudization/Nitaqat bands, KSA notice periods, and end-of-service benefit exposure. Each of these three only weighs its KSA-specific items when the decision's `jurisdiction:` field literally reads `KSA` or `Saudi Arabia` — this is a mechanical field read, the same one the CEO step uses, not something any agent infers from the rest of the decision text.

## Examples and validation

[examples/](examples/) has three worked decisions (a clear approve, a compliance red-flag that must trigger the near-veto rule, and an empty-context case that must return `insufficient_data`) plus a headless validation harness (`examples/validate.py`) that runs `/council` against each one and checks the output against a fixed schema. See [examples/README.md](examples/README.md) for details.

## Subagent model: haiku

The five C-suite subagents (`cfo-agent`, `coo-agent`, `cmo-agent`, `cto-agent`, `chro-agent`) run on **`model: haiku`**, not `sonnet`.

**Why:** haiku matched sonnet closely enough on the metrics that matter here — schema validity and decision consistency — that the cost step-up to sonnet (several times the price per token) wasn't justified for what is a bounded, structured evaluation task over a small set of already-curated context documents, not open-ended reasoning.

**What was measured:** both settings were run live against all three [examples/](examples/) (a clear approve, a compliance red-flag that must trigger the near-veto rule, and an empty-context case that must return `insufficient_data`), 2 runs each, 5 subagent calls per run — 60 live subagent invocations total, using the actual `.claude/agents/*.md` definitions with `model:` temporarily set to each value. (The `claude` CLI wasn't available in the sandbox this was run in, so `examples/validate.py` itself couldn't be shelled out to; the same decisions, context-staging, and schema-validation logic from that script were run directly instead, via live Agent-tool calls. Raw results: [examples/model-comparison-results.json](examples/model-comparison-results.json).)

| Metric | haiku | sonnet |
|---|---|---|
| Subagent replies matching the 4-field JSON schema | 30/30 (100%) | 30/30 (100%) |
| Per-role recommendation agreement, run 1 vs run 2 | 12/15 (80%) | 13/15 (87%) |
| Compliance red-flag example: reject held across both runs, all 5 roles | 10/10 (100%) | 9/10 (one run had COO soften to `approve_with_conditions`, though still on the same cited compliance risk) |
| Council-level final_decision matched the strict expected outcome | 4/6 (67%) | 5/6 (83%) |

The gap that shows up (mostly on the clear-approve case, where a subagent sometimes adds conditions and sometimes doesn't) was about *how cautious the wording came out*, not about missing the substance — no run of either model missed the compliance red flag's core issue, and no run of either model approved something it shouldn't have. On the one scenario with real teeth — the compliance near-veto — haiku was actually the more consistent of the two in this sample.

**If you re-run this and see a different picture** (e.g. a sharper depth gap on more ambiguous or adversarial proposals than these three examples cover), the fix is a one-line change: set `model: sonnet` in each of the five `.claude/agents/*-agent.md` files.

## License

MIT — see [LICENSE](LICENSE).
