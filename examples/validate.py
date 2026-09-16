#!/usr/bin/env python3
"""
Headless validation harness for the /council skill.

For each example under examples/<name>/ (a decision.md, an expected.json, and
an optional context/<role>/ tree), this script:

  1. Stages the example's context/<role>/ files into the real context/<role>/
     folders that .claude/agents/*.md instruct each subagent to read.
  2. Invokes `claude -p "/council <decision>"` headlessly, capturing the full
     tool-call transcript via --output-format stream-json.
  3. Parses that transcript and checks:
       (a) exactly five subagent invocations occurred (one per C-suite role)
       (b) every subagent reply is valid JSON matching the 4-field schema
       (c) the final council JSON matches its schema
       (d) final_decision matches what's expected for that example
  4. Restores the original context/<role>/ contents afterward.

Each example is run --runs times (default 3) and the script reports
agreement across runs in addition to per-run pass/fail.

Requires the `claude` CLI on PATH. Run `python examples/validate.py --self-test`
to exercise the parsing/validation logic against canned transcripts without
invoking the CLI at all (useful for checking the harness itself is sound).

Usage:
    python examples/validate.py                      # run all examples, 3x each
    python examples/validate.py --runs 1              # quick single pass
    python examples/validate.py --example 01-clear-approve
    python examples/validate.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONTEXT_ROOT = REPO_ROOT / "context"

# role folder name -> subagent name, per .claude/agents/*.md
ROLE_TO_AGENT = {
    "finance": "cfo-agent",
    "operations": "coo-agent",
    "market": "cmo-agent",
    "technology": "cto-agent",
    "people": "chro-agent",
}
AGENT_TO_ROLE = {v: k for k, v in ROLE_TO_AGENT.items()}
EXPECTED_AGENTS = set(ROLE_TO_AGENT.values())

RECOMMENDATION_VALUES = {"approve", "approve_with_conditions", "reject", "insufficient_data"}
CONFIDENCE_VALUES = {"low", "medium", "high"}

SUBAGENT_SCHEMA_FIELDS = {"role", "recommendation", "confidence", "key_risks", "key_benefits"}
# XREF-01: a regulatory-context follow-up reply from cto-agent/chro-agent
# (see REGULATORY_FOLLOWUP_AGENTS below) may add this when it revises its
# recommendation in light of the saudi-law-tracker query results -- a real,
# expected reply shape, not schema drift.
SUBAGENT_OPTIONAL_FIELDS = {"conditions"}
SUBAGENT_ROLE_VALUES = {"cfo", "coo", "cmo", "cto", "chro"}
# council.md's XREF-01 step re-invokes exactly these two with regulatory
# context and asks them to revise their recommendation -- the only agents
# a real run can legitimately call more than once, and at most twice each.
REGULATORY_FOLLOWUP_AGENTS = {"cto-agent", "chro-agent"}
# Recognized Saudi-jurisdiction values, matching council.md step 1's own
# definition exactly (case-insensitive, exact match only).
RECOGNIZED_SAUDI_JURISDICTIONS = {"ksa", "saudi arabia"}
_JURISDICTION_FIELD_RE = re.compile(r"^\s*jurisdiction\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
COUNCIL_SCHEMA_FIELDS = {
    "final_decision",
    "confidence",
    "regulatory_context_consulted",  # added when council.md gained the XREF-01 regulatory step
    "council_summary",
    "key_disagreements",
    "reasoning",
}
COUNCIL_SUMMARY_KEYS = {"cfo", "coo", "cmo", "cto", "chro"}


# --------------------------------------------------------------------------
# JSON extraction / schema validation
# --------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _escape_raw_control_chars_in_json_strings(text: str) -> str:
    """Escapes literal C0 control characters (newline, tab, carriage return,
    etc.) found INSIDE a JSON string literal, leaving everything else --
    whitespace between tokens, code-fence formatting -- untouched.

    Confirmed live 2026-09-15 (02-compliance-red-flag run 2): the CEO wrote
    a literal, unescaped newline character inside the `reasoning` string
    value -- right at the paragraph break before the mandatory human-review
    reminder sentence council.md's Reminder section asks it to append --
    instead of the JSON-escaped `\\n`. Strict json.loads() correctly rejects
    raw control characters inside a string literal, so this repairs only
    that specific mechanical mistake (an actual Enter keystroke where an
    escape sequence belonged) rather than silently accepting some other,
    genuinely different kind of malformed JSON.
    """
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def _try_parse_json(candidate: str):
    """json.loads(), retrying once with raw-control-character repair if the
    first attempt fails specifically on an embedded control character.
    Returns (parsed_dict_or_None, error_message_or_None)."""
    try:
        return json.loads(candidate), None
    except json.JSONDecodeError as e:
        if "Invalid control character" not in str(e):
            return None, e
        try:
            return json.loads(_escape_raw_control_chars_in_json_strings(candidate)), None
        except json.JSONDecodeError as e2:
            return None, e2


def extract_json(text: str):
    """Best-effort extraction of a JSON object from model output that may be
    wrapped in a ```json fence or have leading/trailing prose. Returns
    (parsed_dict_or_None, error_message_or_None)."""
    if text is None:
        return None, "no text content to parse"
    candidate = text.strip()
    # Strip a wrapping code fence if present.
    if candidate.startswith("```"):
        candidate = _CODE_FENCE_RE.sub("", candidate).strip()
    parsed, err = _try_parse_json(candidate)
    if parsed is not None:
        return parsed, None
    # Fall back to the first {...} block found, in case of stray prose.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = candidate[start : end + 1]
        parsed, err = _try_parse_json(snippet)
        if parsed is not None:
            return parsed, None
        return None, f"could not parse extracted JSON block: {err}"
    return None, "no JSON object found in text"


def validate_subagent_schema(obj, expected_role: str | None = None) -> list[str]:
    """Returns a list of schema violations (empty list = valid).

    `expected_role` (e.g. "cfo", derived from the agent name that was
    actually invoked) cross-checks the reply's own `role` field against
    which subagent this is supposed to be -- catching a role tag that's
    just wrong, not only one that's missing or malformed.
    """
    errors = []
    if not isinstance(obj, dict):
        return ["reply is not a JSON object"]
    missing = SUBAGENT_SCHEMA_FIELDS - obj.keys()
    if missing:
        errors.append(f"missing field(s): {sorted(missing)}")
    extra = obj.keys() - SUBAGENT_SCHEMA_FIELDS - SUBAGENT_OPTIONAL_FIELDS
    if extra:
        errors.append(f"unexpected extra field(s): {sorted(extra)}")
    if "conditions" in obj and not isinstance(obj["conditions"], list):
        errors.append("conditions is not a list")
    if "role" in obj:
        if obj["role"] not in SUBAGENT_ROLE_VALUES:
            errors.append(f"role {obj['role']!r} not in {sorted(SUBAGENT_ROLE_VALUES)}")
        elif expected_role is not None and obj["role"] != expected_role:
            errors.append(f"role {obj['role']!r} does not match the agent invoked ({expected_role!r})")
    if "recommendation" in obj and obj["recommendation"] not in RECOMMENDATION_VALUES:
        errors.append(f"recommendation {obj['recommendation']!r} not in {sorted(RECOMMENDATION_VALUES)}")
    if "confidence" in obj and obj["confidence"] not in CONFIDENCE_VALUES:
        errors.append(f"confidence {obj['confidence']!r} not in {sorted(CONFIDENCE_VALUES)}")
    if "key_risks" in obj and not isinstance(obj["key_risks"], list):
        errors.append("key_risks is not a list")
    if "key_benefits" in obj and not isinstance(obj["key_benefits"], list):
        errors.append("key_benefits is not a list")
    return errors


def validate_council_schema(obj) -> list[str]:
    errors = []
    if not isinstance(obj, dict):
        return ["final reply is not a JSON object"]
    missing = COUNCIL_SCHEMA_FIELDS - obj.keys()
    if missing:
        errors.append(f"missing field(s): {sorted(missing)}")
    extra = obj.keys() - COUNCIL_SCHEMA_FIELDS
    if extra:
        errors.append(f"unexpected extra field(s): {sorted(extra)}")
    if "final_decision" in obj and obj["final_decision"] not in RECOMMENDATION_VALUES:
        errors.append(f"final_decision {obj['final_decision']!r} not in {sorted(RECOMMENDATION_VALUES)}")
    if "confidence" in obj and obj["confidence"] not in CONFIDENCE_VALUES:
        errors.append(f"confidence {obj['confidence']!r} not in {sorted(CONFIDENCE_VALUES)}")
    summary = obj.get("council_summary")
    if not isinstance(summary, dict):
        errors.append("council_summary is not a JSON object")
    else:
        missing_roles = COUNCIL_SUMMARY_KEYS - summary.keys()
        if missing_roles:
            errors.append(f"council_summary missing role(s): {sorted(missing_roles)}")
        for role, entry in summary.items():
            if not isinstance(entry, dict) or "recommendation" not in entry or "confidence" not in entry:
                errors.append(f"council_summary.{role} missing recommendation/confidence")
    if "key_disagreements" in obj and not isinstance(obj["key_disagreements"], list):
        errors.append("key_disagreements is not a list")
    if "reasoning" in obj and not isinstance(obj["reasoning"], str):
        errors.append("reasoning is not a string")
    return errors


# --------------------------------------------------------------------------
# Transcript parsing (Claude Code --output-format stream-json)
# --------------------------------------------------------------------------


_ASYNC_LAUNCH_MARKER = "async agent launched"
_ROLE_RESPONDED_RE = re.compile(r"\b(cfo|coo|cmo|cto|chro)\b[^.\n]{0,40}\bresponded\b", re.IGNORECASE)
_SUBAGENT_JSON_HINT_RE = re.compile(r'"recommendation"\s*:', re.IGNORECASE)


def _is_placeholder_launch_artifact(tc: dict) -> bool:
    """True for a known, harmless Claude Code CLI transcript artifact, not a
    real subagent invocation.

    Confirmed live 2026-09-14 (claude-code 2.1.270, executive-council
    03-no-context run 2): when the CEO launches multiple subagents
    asynchronously in one batch, the stream-json transcript can include an
    extra, phantom `Agent`/`Task` tool_use entry alongside the real ones --
    `subagent_type: "general-purpose"`, with `description` and `prompt`
    both literally the string `"placeholder"`, and no `tool_result` ever
    returned for it (the task is never actually started, just listed in a
    `background_tasks_changed` event next to the real async launches).

    This is a client-side quirk in how the CLI represents a batch of async
    agent launches -- nothing in council.md or any subagent prompt asks for
    or causes it. Left uncounted, it looks like an unexpected 6th subagent
    invocation and fails checks (a) and (b) on an otherwise fully correct
    run (the 5 real subagents all ran and replied correctly in that same
    transcript). Filtering it here, rather than special-casing it in
    evaluate_run, keeps the checks themselves simple and accurate for what
    they're actually meant to verify: real subagent invocations.

    Deliberately narrow: only a launch with *no result* where *either*
    description or prompt is exactly "placeholder" is dropped. If you see
    this recur with a real result attached, or with different placeholder-
    looking text, don't widen this filter to cover it without re-verifying
    live first -- that shape could be a genuine new problem, not this one.
    """
    return (
        tc.get("result_text") is None
        and (tc.get("description") == "placeholder" or tc.get("prompt") == "placeholder")
    )


def parse_transcript(raw_stdout: str):
    """Parses NDJSON stream-json output into (task_calls, final_text).

    task_calls: list of {"id", "subagent_type", "description", "prompt",
    "result_text"} in call order -- known-harmless phantom launches are
    already filtered out (see _is_placeholder_launch_artifact) by the time
    this returns, so callers never need to special-case them.
    final_text: the last assistant-visible text block (expected to be the
    council's final JSON), or the `result` field of a terminal `result`
    event as a fallback.

    The orchestrator sometimes launches subagents *asynchronously* (observed
    live, 2026-09-14) rather than waiting on each Agent tool_use call
    synchronously -- in that mode the tool_result is just a launch
    acknowledgment ("Async agent launched successfully..."), and the actual
    verdict arrives later as a plain assistant text block, which the CEO
    then narrates ("COO responded (approve, high confidence). Waiting on
    ..."). A second pass below recovers those by matching each such
    narration to the most recently seen not-yet-claimed JSON-shaped text
    block, in order -- best-effort, since nothing in the transcript
    otherwise labels which async text block belongs to which role.
    """
    task_calls: list[dict] = []
    by_id: dict[str, dict] = {}
    all_assistant_texts: list[str] = []
    final_text = None
    fallback_result_text = None

    for lineno, line in enumerate(raw_stdout.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # not every line is guaranteed to be a clean event; skip
        if not isinstance(obj, dict):
            # A line can be valid JSON and still not be an event object --
            # confirmed live 2026-09-15 (01-clear-approve): a bare
            # JSON-encoded string line (verbose CLI commentary, not a
            # {"type": ...} event) crashed obj.get("type") here. Any other
            # bare JSON scalar (number, bool, null, list) is equally not an
            # event we can parse, so skip all of them the same way.
            continue

        obj_type = obj.get("type")

        if obj_type == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            text_parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task"):
                    # "Agent" is the current tool name (verified live,
                    # 2026-09-14, claude-code 2.1.270); "Task" is accepted
                    # too in case an older/newer build renames it again.
                    inp = block.get("input") or {}
                    tc = {
                        "id": block.get("id"),
                        "subagent_type": inp.get("subagent_type"),
                        "description": inp.get("description"),
                        "prompt": inp.get("prompt"),
                        "result_text": None,
                    }
                    task_calls.append(tc)
                    if tc["id"]:
                        by_id[tc["id"]] = tc
                elif block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                    all_assistant_texts.append(block.get("text", ""))
            if text_parts:
                final_text = "".join(text_parts)

        elif obj_type == "user":
            content = (obj.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    tc = by_id.get(tid)
                    if tc is None:
                        continue
                    c = block.get("content")
                    if isinstance(c, list):
                        c = "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                    if isinstance(c, str) and _ASYNC_LAUNCH_MARKER in c.lower():
                        continue  # launch ack, not the real verdict -- leave result_text None for now
                    tc["result_text"] = c

        elif obj_type == "result":
            if isinstance(obj.get("result"), str):
                fallback_result_text = obj["result"]

    if final_text is None:
        final_text = fallback_result_text

    pending = [tc for tc in task_calls if tc["result_text"] is None]
    if pending:
        claimed_texts: set[int] = set()  # id() of texts already assigned, so passes below skip them

        # PRIMARY (added 2026-09-14 alongside each agent prompt's new "role"
        # field): every subagent now echoes its own role in its JSON reply,
        # so an async verdict can be attributed directly from its own
        # content -- no dependence on the orchestrator narrating anything.
        for text in all_assistant_texts:
            if not pending or id(text) in claimed_texts:
                continue
            if text == final_text or '"final_decision"' in text:
                continue  # the council's own synthesis, not a subagent verdict
            if not _SUBAGENT_JSON_HINT_RE.search(text):
                continue
            parsed, _ = extract_json(text)
            role = parsed.get("role") if isinstance(parsed, dict) else None
            if not role:
                continue
            agent_name = f"{role}-agent"
            tc = next(
                (t for t in pending if t["subagent_type"] == agent_name and t["result_text"] is None),
                None,
            )
            if tc is not None:
                tc["result_text"] = text
                claimed_texts.add(id(text))

        # FALLBACK for transcripts predating the "role" field (or a reply
        # that somehow omits it despite instructions): narration-based
        # matching, e.g. "COO responded (approve, high confidence)."
        still_pending = [t for t in pending if t["result_text"] is None]
        if still_pending:
            unclaimed_json_texts: list[str] = []
            for text in all_assistant_texts:
                if id(text) in claimed_texts:
                    continue
                if text == final_text or '"final_decision"' in text:
                    continue
                if _SUBAGENT_JSON_HINT_RE.search(text):
                    unclaimed_json_texts.append(text)
                match = _ROLE_RESPONDED_RE.search(text)
                if match and unclaimed_json_texts:
                    agent_name = f"{match.group(1).lower()}-agent"
                    tc = next(
                        (t for t in still_pending if t["subagent_type"] == agent_name and t["result_text"] is None),
                        None,
                    )
                    if tc is not None:
                        tc["result_text"] = unclaimed_json_texts.pop(0)

            # The orchestrator doesn't always narrate the *last* async
            # arrival before jumping straight to final synthesis (observed
            # live, 2026-09-14) -- if exactly one role is still unresolved
            # and exactly one JSON-shaped text is left unclaimed, it's
            # almost certainly that role's verdict.
            still_pending = [t for t in still_pending if t["result_text"] is None]
            if len(still_pending) == 1 and len(unclaimed_json_texts) == 1:
                still_pending[0]["result_text"] = unclaimed_json_texts[0]

        # RESIDUAL LIMITATION: only reachable now for a transcript with no
        # per-reply "role" field AND no "<ROLE> responded" narration AND not
        # exactly-one-left-unclaimed -- i.e. legacy transcripts predating
        # the role-tagging fix, batched with zero narration. Should not occur
        # for any run recorded after 2026-09-14. If you see "invalid JSON: no
        # text content to parse" for a run, grep that run's .stdout.jsonl for
        # `"recommendation":` before concluding the council itself failed.

    # Drop known-harmless phantom launches (see _is_placeholder_launch_artifact)
    # after the resolution passes above, not before: a placeholder entry's
    # subagent_type never matches a real role name, so it can't have been
    # (mis)claimed as one, but checking post-resolution keeps this filter
    # correct even if that ever changed.
    task_calls = [tc for tc in task_calls if not _is_placeholder_launch_artifact(tc)]

    return task_calls, final_text


def regulatory_bash_query_invoked(raw_stdout: str) -> bool:
    """Scans the raw transcript directly for a Bash tool_use that invoked
    the saudi-law-tracker query CLI (council.md step 5's regulatory step)
    AND actually returned real data -- an attempt that hard-errored (e.g.
    --disallowedTools "Bash" makes the tool absent entirely; the CLI
    returns `is_error: true`, "No such tool available: Bash...") never
    reached the real query and returned nothing a subagent could act on, so
    it does not count as the regulatory step having run. Confirmed live
    2026-09-16 (01-clear-approve): with the code-level jurisdiction gate in
    place, the CEO still emitted a Bash tool_use naming the query (its own
    prior turns/training make it try even when the tool isn't listed for
    this session), but the tool_result was
    `<tool_use_error>Error: No such tool available: Bash. Bash is disabled
    for this session, in subagents as well as here.</tool_use_error>` with
    `is_error: true` -- no data was ever returned, `regulatory_context_
    consulted: false` was correctly self-reported, and the final decision
    was reached on purely operational grounds with no Saudi-regulatory
    content anywhere in it. Counting that attempt as "invoked" would be a
    false positive on check (e): the gate worked, only the older
    prompt-level detection heuristic needed updating to know the
    difference between an attempt and an actual, data-returning call.

    This exists as a deliberate second signal, independent of the CEO's own
    self-reported `regulatory_context_consulted` field in the final council
    JSON. A self-report can be wrong in either direction -- the CEO could
    run the query and still write `false`, or claim `true` without ever
    actually invoking Bash -- and check (e) previously trusted it alone.
    Reading the actual tool call the CLI executed is the only way to know
    "the query was never invoked" is true, rather than merely claimed by
    the same model whose jurisdiction judgment this whole gate exists to
    stop trusting (see EC-02, 2026-09-15: the CEO ran this exact query on
    an unestablished jurisdiction in 4/4 live batches despite four
    escalating prompt-only fixes).
    """
    candidate_ids: set[str] = set()
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        if obj.get("type") == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use" or block.get("name") != "Bash":
                    continue
                command = (block.get("input") or {}).get("command") or ""
                if "saudi-law-tracker" in command.lower() and block.get("id"):
                    candidate_ids.add(block["id"])

        elif obj.get("type") == "user" and candidate_ids:
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id")
                if tid not in candidate_ids:
                    continue
                if block.get("is_error"):
                    candidate_ids.discard(tid)  # attempted but hard-errored, no data returned
                    continue
                return True  # this candidate call actually returned real data

    return False


# --------------------------------------------------------------------------
# Context staging / restore
# --------------------------------------------------------------------------


def backup_context() -> Path:
    backup_dir = Path(tempfile.mkdtemp(prefix="council-context-backup-"))
    for role in ROLE_TO_AGENT:
        src = CONTEXT_ROOT / role
        dst = backup_dir / role
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
    return backup_dir


def restore_context(backup_dir: Path) -> None:
    for role in ROLE_TO_AGENT:
        target = CONTEXT_ROOT / role
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        src = backup_dir / role
        if src.exists():
            for item in src.iterdir():
                dest_item = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item)
                else:
                    shutil.copy2(item, dest_item)
    shutil.rmtree(backup_dir, ignore_errors=True)


def stage_example(example_dir: Path) -> None:
    """Clears every real context/<role>/ folder and, if the example provides
    one, copies its context/<role>/ files in. An example with no
    context/<role>/ subfolder (e.g. 03-no-context) is staged as a genuinely
    empty folder, exercising each agent's literal 'if empty, insufficient_data'
    instruction."""
    example_context = example_dir / "context"
    for role in ROLE_TO_AGENT:
        target = CONTEXT_ROOT / role
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        src = example_context / role
        if src.exists() and any(src.iterdir()):
            for item in src.iterdir():
                dest_item = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item)
                else:
                    shutil.copy2(item, dest_item)


# --------------------------------------------------------------------------
# Running the CLI
# --------------------------------------------------------------------------


def extract_jurisdiction_field(decision_text: str) -> str | None:
    """Mechanically extracts the literal value of the first `jurisdiction:`
    line in decision_text, via regex -- no LLM involved. Returns None if the
    field is missing entirely (treated the same as an unrecognized value by
    callers, per council.md step 1: "If the field is missing altogether,
    treat it exactly as unspecified")."""
    m = _JURISDICTION_FIELD_RE.search(decision_text)
    return m.group(1) if m else None


def is_recognized_saudi_jurisdiction(value: str | None) -> bool:
    return value is not None and value.strip().lower() in RECOGNIZED_SAUDI_JURISDICTIONS


def compute_tool_flags(decision_text: str) -> tuple[str, str | None]:
    """Returns (allowed_tools, disallowed_tools_or_None) for a headless
    /council run, gated on decision_text's jurisdiction: field. Bash is
    permitted only for a recognized Saudi jurisdiction -- see
    run_council()'s comment for why this replaced trusting the CEO's own
    prompt-level jurisdiction gate, and for why --disallowedTools (not
    merely omitting Bash from --allowedTools) is the part that actually
    works. A pure function so the gating decision itself is unit-testable
    without mocking subprocess."""
    jurisdiction = extract_jurisdiction_field(decision_text)
    if is_recognized_saudi_jurisdiction(jurisdiction):
        return "Agent,Task,Bash", None
    return "Agent,Task", "Bash"


def run_council(decision_text: str, claude_bin: str, skip_permissions: bool, timeout: int):
    prompt = f"/council {decision_text}"
    # The prompt is passed via stdin (input=prompt below), not as an inline
    # CLI argument -- confirmed live 2026-09-15 (01-clear-approve): once
    # decision.md gained a multi-line `jurisdiction: ...\n\nProposal: ...`
    # shape, an inline `-p <prompt>` arg got silently truncated at the first
    # newline by the Windows claude.cmd npm shim re-parsing the argument
    # string through cmd.exe a second time (the CEO received only
    # "jurisdiction: unspecified" and nothing after it, all 9/9 runs). This
    # is the same root cause already found and fixed the same way in
    # saudi-law-tracker's evals/agent/run_evals.py (run_judge()) -- not a
    # length-limit issue, a multi-line-inline-argument quoting issue.
    # Piping via stdin sidesteps the CLI re-quoting entirely.
    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--verbose"]
    if skip_permissions:
        # Verified live (2026-09-14, claude-code 2.1.270): the subagent-spawn
        # tool is actually named "Agent" now, not "Task" (a real transcript
        # confirmed this: every cfo/coo/cmo/cto/chro invocation showed up as
        # a "name":"Agent" tool_use block); "Task" is kept alongside it in
        # case a different build still uses the legacy name. --allowedTools
        # naming both, plus Bash for the regulatory step's saudi-law-tracker
        # query call, is enough for a fully non-interactive run with zero
        # permission_denials. Prefer this scoped allowlist over
        # --dangerously-skip-permissions, which some harnesses running this
        # script themselves (a Claude Code session) refuse outright as
        # "create unsafe agents" when it's used to spawn a nested claude
        # session -- --allowedTools isn't a blanket bypass, so it isn't
        # caught by that check.
        #
        # Bash is permitted ONLY when decision_text's jurisdiction: field is
        # a recognized Saudi value -- a code-level gate, not a prompt
        # instruction the CEO can fail to follow. Five escalating prompt-only
        # attempts at getting the CEO to self-gate the regulatory step on
        # jurisdiction (never resolve an ambiguous phrase -> a two-part gate
        # -> a quote-forcing checkpoint -> removing the hardcoded Saudi
        # framing from cfo/cto/chro-agent.md -> a required jurisdiction:
        # field read in council.md's own instructions) all failed some
        # fraction of live runs, worsening to 0/3 in the batch that also
        # showed it cascading into a cto-agent schema break (EC-02, see
        # VALIDATION_REPORT.md).
        #
        # IMPORTANT, confirmed live 2026-09-16 by direct experiment, not
        # assumed: simply leaving Bash out of --allowedTools does NOT deny
        # it. A bare `claude -p` call with --allowedTools "Agent,Task" (no
        # Bash) and a prompt explicitly instructing a Bash command still ran
        # it successfully, with permission_denials: [] in the result -- this
        # CLI's headless default (--permission-mode default, no
        # --permission-prompts override) auto-approves a tool omitted from
        # --allowedTools rather than denying it; --allowedTools is a
        # pre-approval list, not an allowlist that excludes anything not on
        # it. The mechanism that actually works, confirmed the same way: a
        # second experiment adding --disallowedTools "Bash" made Bash
        # disappear from the init event's own tool list entirely, and the
        # model correctly reported it had no shell execution tool available
        # rather than finding a workaround. So the gate here is
        # --disallowedTools, not the absence of Bash from --allowedTools --
        # this subsumes the prompt-level gate for this harness; council.md's
        # own instructions and check (e) both stay in place as
        # defense-in-depth (check (e) should now read as a permanent PASS
        # for jurisdiction-unspecified examples specifically because the
        # tool is structurally unavailable, not because the CEO chose
        # correctly).
        allowed_tools, disallowed_tools = compute_tool_flags(decision_text)
        cmd += ["--allowedTools", allowed_tools]
        if disallowed_tools:
            cmd += ["--disallowedTools", disallowed_tools]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.stdout, proc.stderr, proc.returncode, None
    except FileNotFoundError:
        return "", "", None, f"'{claude_bin}' was not found on PATH"
    except subprocess.TimeoutExpired as e:
        return e.stdout or "", e.stderr or "", None, f"timed out after {timeout}s"


# --------------------------------------------------------------------------
# Per-run evaluation
# --------------------------------------------------------------------------


def evaluate_run(task_calls, final_text, expected: dict, raw_stdout: str | None = None):
    """Runs checks (a)-(e) against one parsed transcript. Returns a result dict.

    `raw_stdout`, when given, is the full raw transcript text -- used by
    check (e) to mechanically detect whether the saudi-law-tracker Bash
    query actually ran (see regulatory_bash_query_invoked), independent of
    the CEO's own self-reported regulatory_context_consulted field. Callers
    that don't have the raw transcript handy (e.g. older self-test fixtures
    built directly from task_calls/final_text) fall back to the self-report
    alone; every self-test that specifically exercises check (e) passes the
    raw transcript explicitly."""
    result = {
        "checks": {},
        "final_decision": None,
        "errors": [],
    }

    # (a) all five roles invoked at least once, with no unexpected repeats.
    # A plain run is exactly 5 calls, one per role. council.md's XREF-01
    # regulatory follow-up (step 4) legitimately re-invokes cto-agent and
    # chro-agent a second time with regulatory context -- 7 calls total,
    # only those two roles repeated, never more than twice each. Anything
    # else (a missing role, an unexpected role, a repeat of CFO/COO/CMO, or
    # cto/chro called a third time) is a real fan-out failure.
    called_agents = [tc["subagent_type"] for tc in task_calls]
    counts = Counter(called_agents)
    all_roles_present = EXPECTED_AGENTS.issubset(counts) and not (set(counts) - EXPECTED_AGENTS)
    repeats_ok = all(
        count == 1 or (agent in REGULATORY_FOLLOWUP_AGENTS and count == 2)
        for agent, count in counts.items()
    )
    result["checks"]["a_five_invocations"] = all_roles_present
    result["checks"]["a_five_distinct_roles"] = repeats_ok
    if not all_roles_present:
        result["errors"].append(
            f"expected one invocation of each of {sorted(EXPECTED_AGENTS)}, got {called_agents}"
        )
    elif not repeats_ok:
        result["errors"].append(
            f"unexpected repeat invocation pattern {dict(counts)}: only cto-agent/chro-agent may "
            "be called a second time (XREF-01 regulatory follow-up), and never more than twice"
        )

    # (b) every subagent reply valid JSON matching schema
    subagent_ok = True
    per_agent_schema_errors = {}
    for tc in task_calls:
        agent = tc["subagent_type"] or "?"
        parsed, parse_err = extract_json(tc["result_text"])
        if parsed is None:
            subagent_ok = False
            per_agent_schema_errors[agent] = [f"invalid JSON: {parse_err}"]
            continue
        expected_role = agent[:-len("-agent")] if agent.endswith("-agent") else None
        errs = validate_subagent_schema(parsed, expected_role=expected_role)
        if errs:
            subagent_ok = False
        per_agent_schema_errors[agent] = errs
    result["checks"]["b_subagent_schema_valid"] = subagent_ok
    result["subagent_schema_errors"] = per_agent_schema_errors
    if not subagent_ok:
        result["errors"].append(f"subagent schema violations: {per_agent_schema_errors}")

    # (c) final council JSON matches schema
    council_parsed, council_parse_err = extract_json(final_text)
    if council_parsed is None:
        result["checks"]["c_council_schema_valid"] = False
        result["errors"].append(f"final council reply was not valid JSON: {council_parse_err}")
    else:
        council_errs = validate_council_schema(council_parsed)
        result["checks"]["c_council_schema_valid"] = not council_errs
        if council_errs:
            result["errors"].append(f"council schema violations: {council_errs}")
        result["final_decision"] = council_parsed.get("final_decision")

    # (d) final_decision matches expected
    acceptable = set(expected.get("acceptable_final_decisions", []))
    d_ok = result["final_decision"] in acceptable if acceptable else None
    result["checks"]["d_final_decision_expected"] = d_ok
    if d_ok is False:
        result["errors"].append(
            f"final_decision {result['final_decision']!r} not in acceptable set {sorted(acceptable)}"
        )

    # (e) the regulatory step matches expected, when an example declares one.
    # This exists because council.md's own prompt-level instructions telling
    # the CEO not to assume a Saudi jurisdiction from an unstated location
    # proved unreliable in practice: four escalating rewordings (never
    # resolve an ambiguous phrase into a real place -> a two-part
    # jurisdiction/subject-matter gate -> a mandatory "write down a literal
    # quote or admit jurisdiction is unestablished" checkpoint -> removing
    # the hardcoded "this council operates under Saudi Arabia" framing from
    # cfo/cto/chro-agent.md's own Local context sections) all failed the
    # same live 03-no-context re-test, with the CEO still running the query
    # off nothing but generic labor/tax/licensing language or a subagent's
    # own hedge -- confirmed 2026-09-15 across 4 consecutive fresh 3-run
    # batches. The final_decision label alone doesn't catch this:
    # 03-no-context still reliably lands on insufficient_data because the
    # empty context/ folders are independently sufficient for that outcome,
    # so a passing (d) can silently coexist with the CEO having fabricated a
    # jurisdiction and let it sway CHRO's confidence. council.md now reads
    # jurisdiction from a required, literal `jurisdiction:` field instead of
    # inferring it -- but this check stays in place as a defense-in-depth
    # backstop regardless, watching for the failure mode in case anything
    # else ever triggers it.
    #
    # The primary signal is the actual Bash tool_use captured straight from
    # the raw transcript (regulatory_bash_query_invoked), not the CEO's own
    # self-reported regulatory_context_consulted field -- a self-report can
    # be wrong in either direction, so "the query was never invoked" needs
    # to be verified from what the CLI actually did, not just claimed by the
    # same model this whole gate exists to stop trusting on this point. When
    # no raw transcript is available (older self-test callers), this falls
    # back to the self-reported field alone.
    expected_regulatory = expected.get("expected_regulatory_context_consulted")
    if expected_regulatory is not None:
        actual_self_report = council_parsed.get("regulatory_context_consulted") if council_parsed else None
        if raw_stdout is not None:
            actual_bash_invoked = regulatory_bash_query_invoked(raw_stdout)
            e_ok = actual_bash_invoked == expected_regulatory
            result["checks"]["e_regulatory_consulted_expected"] = e_ok
            if not e_ok:
                result["errors"].append(
                    f"regulatory Bash step actually invoked={actual_bash_invoked}, expected {expected_regulatory!r} "
                    "-- decision.md's jurisdiction: field for this example does not name a recognized Saudi "
                    "jurisdiction, so the saudi-law-tracker query should never run (see council.md step 4)"
                )
            if actual_self_report is not None and actual_self_report != actual_bash_invoked:
                result["errors"].append(
                    f"CEO self-reported regulatory_context_consulted={actual_self_report!r} does not match "
                    f"the actual Bash invocation ({actual_bash_invoked!r}) found in the transcript -- the "
                    "self-report is not trustworthy on its own, which is why check (e) reads the transcript directly"
                )
        else:
            e_ok = actual_self_report == expected_regulatory if actual_self_report is not None else None
            result["checks"]["e_regulatory_consulted_expected"] = e_ok
            if e_ok is False:
                result["errors"].append(
                    f"regulatory_context_consulted was {actual_self_report!r}, expected {expected_regulatory!r} "
                    "(no raw transcript was supplied to cross-check the actual Bash invocation)"
                )
    else:
        result["checks"]["e_regulatory_consulted_expected"] = None

    result["all_pass"] = all(v for v in result["checks"].values() if v is not None) and result["checks"]["d_final_decision_expected"] is not False

    return result


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def discover_examples():
    examples = []
    for d in sorted(SCRIPT_DIR.iterdir()):
        if d.is_dir() and (d / "decision.md").exists() and (d / "expected.json").exists():
            examples.append(d)
    return examples


def run_example(example_dir: Path, runs: int, claude_bin: str, skip_permissions: bool, timeout: int, log_dir: Path):
    decision_text = (example_dir / "decision.md").read_text(encoding="utf-8").strip()
    expected = json.loads((example_dir / "expected.json").read_text(encoding="utf-8"))

    run_results = []
    for i in range(1, runs + 1):
        stage_example(example_dir)
        try:
            stdout, stderr, returncode, run_error = run_council(decision_text, claude_bin, skip_permissions, timeout)
        finally:
            pass  # context is restored once for the whole example in run_all()

        example_log_dir = log_dir / example_dir.name
        example_log_dir.mkdir(parents=True, exist_ok=True)
        (example_log_dir / f"run-{i}.stdout.jsonl").write_text(stdout, encoding="utf-8")
        if stderr:
            (example_log_dir / f"run-{i}.stderr.txt").write_text(stderr, encoding="utf-8")

        if run_error:
            run_results.append({
                "run": i,
                "checks": {},
                "final_decision": None,
                "errors": [f"CLI invocation failed: {run_error}"],
                "all_pass": False,
            })
            continue

        task_calls, final_text = parse_transcript(stdout)
        result = evaluate_run(task_calls, final_text, expected, raw_stdout=stdout)
        result["run"] = i
        result["returncode"] = returncode
        run_results.append(result)

    return {
        "example": example_dir.name,
        "expected": expected,
        "runs": run_results,
    }


def summarize(example_result: dict):
    runs = example_result["runs"]
    decisions = [r["final_decision"] for r in runs]
    decision_counts = Counter(decisions)
    most_common_decision, most_common_count = (decision_counts.most_common(1) or [(None, 0)])[0]
    agreement = most_common_count / len(runs) if runs else 0.0
    all_checks_pass = [r.get("all_pass", False) for r in runs]
    return {
        "decisions": decisions,
        "decision_counts": dict(decision_counts),
        "agreement_ratio": agreement,
        "runs_passed": sum(1 for p in all_checks_pass if p),
        "runs_total": len(runs),
    }


def print_report(all_results: list[dict]) -> bool:
    """Prints a markdown report to stdout. Returns True if every run of every
    example fully passed."""
    overall_ok = True
    print("# /council validation report\n")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")

    for ex in all_results:
        summary = summarize(ex)
        print(f"## {ex['example']}\n")
        print(f"Expected final_decision in: {ex['expected'].get('acceptable_final_decisions')}\n")
        print("| run | (a) all 5 roles called | (a) repeats legitimate | (b) subagent schema | (c) council schema | (d) matches expected | (e) regulatory consulted | final_decision | overall |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in ex["runs"]:
            checks = r.get("checks", {})
            def mark(v):
                if v is True:
                    return "PASS"
                if v is False:
                    return "FAIL"
                return "n/a"
            row_pass = r.get("all_pass", False)
            if not row_pass:
                overall_ok = False
            print(
                f"| {r['run']} | {mark(checks.get('a_five_invocations'))} | "
                f"{mark(checks.get('a_five_distinct_roles'))} | {mark(checks.get('b_subagent_schema_valid'))} | "
                f"{mark(checks.get('c_council_schema_valid'))} | {mark(checks.get('d_final_decision_expected'))} | "
                f"{mark(checks.get('e_regulatory_consulted_expected'))} | "
                f"{r.get('final_decision')} | {'PASS' if row_pass else 'FAIL'} |"
            )
            if r.get("errors"):
                for err in r["errors"]:
                    print(f"  - note (run {r['run']}): {err}")
        print()
        print(
            f"**Agreement across {summary['runs_total']} runs:** "
            f"{summary['decision_counts']} "
            f"({summary['agreement_ratio']*100:.0f}% agreed on the most common outcome); "
            f"{summary['runs_passed']}/{summary['runs_total']} runs fully passed all checks.\n"
        )

    print("---")
    print(f"\n**Overall: {'ALL CHECKS PASSED' if overall_ok else 'SOME CHECKS FAILED'}**\n")
    return overall_ok


# --------------------------------------------------------------------------
# Self-test (no CLI required) — proves the parsing/validation logic works
# --------------------------------------------------------------------------


def _fixture_line(obj) -> str:
    return json.dumps(obj)


def _make_task_use_block(tool_id, subagent_type, description="eval", prompt="..."):
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "Agent",
        "input": {"subagent_type": subagent_type, "description": description, "prompt": prompt},
    }


def _make_tool_result(tool_id, text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def _make_bash_tool_use_block(tool_id, command='"C:\\Users\\darai_qtguapt\\saudi-law-tracker\\.venv\\Scripts\\saudi-law-tracker.exe" query --sector labor --json'):
    """Council.md step 5's regulatory query, as it actually appears in a raw
    transcript -- used to give regulatory_bash_query_invoked something real
    to detect in self-test fixtures, rather than only ever testing against
    the CEO's self-reported field."""
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def _make_bash_disabled_error_result(tool_id):
    """The exact real tool_result confirmed live 2026-09-16 (01-clear-approve)
    when --disallowedTools "Bash" is in effect and the CEO attempts the call
    anyway -- an attempt, not an actual regulatory-data-returning call."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "<tool_use_error>Error: No such tool available: Bash. Bash is disabled for this session, in subagents as well as here.</tool_use_error>",
                "is_error": True,
            }],
        },
    }


def _good_subagent_reply(role: str) -> str:
    return json.dumps({
        "role": role,
        "recommendation": "approve",
        "confidence": "high",
        "key_risks": ["none material"],
        "key_benefits": ["closes gap"],
    })


def build_good_transcript():
    council_reply = json.dumps({
        "final_decision": "approve",
        "confidence": "high",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "approve", "confidence": "high"},
            "coo": {"recommendation": "approve", "confidence": "high"},
            "cmo": {"recommendation": "approve", "confidence": "high"},
            "cto": {"recommendation": "approve", "confidence": "high"},
            "chro": {"recommendation": "approve", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "all five agents agreed",
    })

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_extra_council_field_transcript():
    """Otherwise well-formed transcript, but the final council JSON has an
    invented extra field -- the real failure mode confirmed live 2026-09-15
    (human_review_required, conditions_for_approval,
    regulatory_sources_referenced all appeared across different runs,
    despite council.md never defining any of them). Must fail check (c)
    even though every other check would otherwise pass, proving the extra-
    key check can't silently regress."""

    council_reply = json.dumps({
        "final_decision": "approve",
        "confidence": "high",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "approve", "confidence": "high"},
            "coo": {"recommendation": "approve", "confidence": "high"},
            "cmo": {"recommendation": "approve", "confidence": "high"},
            "cto": {"recommendation": "approve", "confidence": "high"},
            "chro": {"recommendation": "approve", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "all five agents agreed",
        "human_review_required": "Yes, before acting on this decision.",
    })

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_unjustified_regulatory_consult_transcript():
    """Otherwise well-formed transcript for a no-jurisdiction-stated scenario,
    but the CEO set regulatory_context_consulted: True anyway -- the real
    failure mode confirmed live 2026-09-15 on 03-no-context across 4
    consecutive fresh 3-run batches, surviving three escalating council.md
    prompt rewordings. Must fail check (e) even though every other check
    (including (d), since insufficient_data is still reached) would
    otherwise pass -- final_decision alone can't catch this."""

    council_reply = json.dumps({
        "final_decision": "insufficient_data",
        "confidence": "high",
        "regulatory_context_consulted": True,
        "council_summary": {
            "cfo": {"recommendation": "insufficient_data", "confidence": "high"},
            "coo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cmo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cto": {"recommendation": "insufficient_data", "confidence": "high"},
            "chro": {"recommendation": "insufficient_data", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "This also plainly touches Saudi regulatory territory (labor law/Nitaqat), "
                     "so I checked the saudi-law-tracker for relevant sectors.",
    })

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    # The actual Bash invocation, not just the self-reported field -- this is
    # what regulatory_bash_query_invoked() must detect for check (e) to fire
    # on the real failure mode rather than only ever on a hand-set boolean.
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [_make_bash_tool_use_block("toolu_bash1")]}}))
    lines.append(_fixture_line(_make_tool_result("toolu_bash1", '{"rows": []}')))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_self_report_mismatch_transcript():
    """The CEO's self-reported regulatory_context_consulted: False, but the
    transcript shows the Bash query was actually invoked -- the exact
    "not just landing there by coincidence" case check (e) must catch by
    reading the transcript directly rather than trusting the self-report.
    Must fail check (e) (self-report alone would have wrongly passed it)."""

    council_reply = json.dumps({
        "final_decision": "insufficient_data",
        "confidence": "high",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "insufficient_data", "confidence": "high"},
            "coo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cmo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cto": {"recommendation": "insufficient_data", "confidence": "high"},
            "chro": {"recommendation": "insufficient_data", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "No jurisdiction was specified, so no regulatory query was run.",
    })

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    # The query actually ran, contradicting the self-report above.
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [_make_bash_tool_use_block("toolu_bash1")]}}))
    lines.append(_fixture_line(_make_tool_result("toolu_bash1", '{"rows": []}')))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_bash_attempted_but_disabled_transcript():
    """The CEO attempts the regulatory Bash call despite --disallowedTools
    "Bash" being in effect (jurisdiction: unspecified), and the tool hard-
    errors with the exact real message confirmed live 2026-09-16
    (01-clear-approve): "No such tool available: Bash. Bash is disabled for
    this session..." with is_error: true. No real data is ever returned;
    the CEO correctly self-reports regulatory_context_consulted: false and
    reaches its decision on purely operational grounds. Must pass check (e)
    -- an attempted-but-hard-errored call is not "the regulatory step ran,"
    since nothing it could have returned ever reached any subagent."""

    council_reply = json.dumps({
        "final_decision": "approve_with_conditions",
        "confidence": "medium",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "approve", "confidence": "high"},
            "coo": {"recommendation": "approve", "confidence": "high"},
            "cmo": {"recommendation": "approve", "confidence": "high"},
            "cto": {"recommendation": "approve", "confidence": "high"},
            "chro": {"recommendation": "approve_with_conditions", "confidence": "high"},
        },
        "key_disagreements": ["CHRO alone flagged operational conditions (roster fragility, fatigue risk)"],
        "reasoning": "The regulatory Bash step was attempted but the tool was unavailable for this "
                     "session (jurisdiction: unspecified); proceeded without it and reached this "
                     "decision on purely operational grounds.",
    })

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [_make_bash_tool_use_block("toolu_bash_disabled")]}}))
    lines.append(_fixture_line(_make_bash_disabled_error_result("toolu_bash_disabled")))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_broken_transcript():
    """Only 4 subagents called, one reply is malformed JSON, final reply missing a field."""
    bad_subagent_reply = "not json at all"
    ok_subagent_reply = json.dumps({
        "recommendation": "reject",
        "confidence": "high",
        "key_risks": ["legal"],
        "key_benefits": [],
    })
    council_reply = json.dumps({
        "final_decision": "reject",
        "confidence": "high",
        # missing council_summary on purpose
        "key_disagreements": [],
        "reasoning": "broken on purpose",
    })
    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "chro-agent"]:  # only 4, cto-agent missing
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for i, (agent, tid) in enumerate(tool_ids.items()):
        reply = bad_subagent_reply if i == 0 else ok_subagent_reply
        lines.append(_fixture_line(_make_tool_result(tid, reply)))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": council_reply}]}}))
    return "\n".join(lines)


def build_regulatory_followup_transcript():
    """XREF-01: cto-agent and chro-agent legitimately re-invoked once more
    with regulatory context (council.md step 4) -- 7 calls total, and the
    second chro-agent reply adds a `conditions` field when it revises its
    recommendation. This is the real shape a live run produced (confirmed
    2026-09-14) that the pre-fix harness incorrectly failed on checks (a)
    and (b); this fixture proves the fix accepts it."""

    council_reply = json.dumps({
        "final_decision": "approve_with_conditions",
        "confidence": "high",
        "regulatory_context_consulted": True,
        "council_summary": {
            "cfo": {"recommendation": "approve", "confidence": "high"},
            "coo": {"recommendation": "approve", "confidence": "high"},
            "cmo": {"recommendation": "approve", "confidence": "high"},
            "cto": {"recommendation": "approve", "confidence": "high"},
            "chro": {"recommendation": "approve_with_conditions", "confidence": "high"},
        },
        "key_disagreements": ["regulatory context changed chro's recommendation"],
        "reasoning": "regulatory context from saudi-law-tracker changed chro's recommendation",
    })

    lines = []
    tool_ids = {}

    first_batch = ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]
    assistant_content = []
    for agent in first_batch:
        tid = f"toolu_{agent}_1"
        tool_ids[(agent, 1)] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent in first_batch:
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tool_ids[(agent, 1)], _good_subagent_reply(role))))

    # The actual regulatory query (council.md step 5) runs before the
    # cto-agent/chro-agent follow-up -- its results are what gets forwarded
    # to them. Included here so regulatory_bash_query_invoked() has a real
    # invocation to detect for this genuinely regulatory-tagged run.
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [_make_bash_tool_use_block("toolu_bash1")]}}))
    lines.append(_fixture_line(_make_tool_result("toolu_bash1", '{"rows": [{"title": "Labor Law Art. 41", "source_url": "https://example.gov.sa/x"}]}')))

    followup_batch = ["cto-agent", "chro-agent"]
    assistant_content2 = []
    for agent in followup_batch:
        tid = f"toolu_{agent}_2"
        tool_ids[(agent, 2)] = tid
        assistant_content2.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content2}}))
    lines.append(_fixture_line(_make_tool_result(tool_ids[("cto-agent", 2)], _good_subagent_reply("cto"))))
    chro_followup_reply = json.dumps({
        "role": "chro",
        "recommendation": "approve_with_conditions",
        "confidence": "high",
        "key_risks": ["needs documentation"],
        "key_benefits": ["closes gap"],
        "conditions": ["document non-coercive opt-in", "confirm overtime logging"],
    })
    lines.append(_fixture_line(_make_tool_result(tool_ids[("chro-agent", 2)], chro_followup_reply)))

    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_bad_repeat_transcript():
    """cfo-agent called twice instead of cto-agent/chro-agent -- not a
    legitimate XREF-01 repeat pattern, so this must still fail check (a)."""

    council_reply = json.dumps({
        "final_decision": "approve",
        "confidence": "high",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "approve", "confidence": "high"},
            "coo": {"recommendation": "approve", "confidence": "high"},
            "cmo": {"recommendation": "approve", "confidence": "high"},
            "cto": {"recommendation": "approve", "confidence": "high"},
            "chro": {"recommendation": "approve", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "all five agents agreed",
    })

    lines = []
    agents = ["cfo-agent", "cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]
    assistant_content = []
    for i, agent in enumerate(agents):
        assistant_content.append(_make_task_use_block(f"toolu_{i}", agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for i, agent in enumerate(agents):
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(f"toolu_{i}", _good_subagent_reply(role))))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_placeholder_artifact_transcript():
    """5 real subagent calls plus a phantom `general-purpose` launch with
    description/prompt both "placeholder" and no result -- the real shape
    confirmed live 2026-09-14 (03-no-context run 2). Must still pass every
    check once _is_placeholder_launch_artifact filters the phantom out."""

    council_reply = json.dumps({
        "final_decision": "insufficient_data",
        "confidence": "high",
        "regulatory_context_consulted": False,
        "council_summary": {
            "cfo": {"recommendation": "insufficient_data", "confidence": "high"},
            "coo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cmo": {"recommendation": "insufficient_data", "confidence": "high"},
            "cto": {"recommendation": "insufficient_data", "confidence": "high"},
            "chro": {"recommendation": "insufficient_data", "confidence": "high"},
        },
        "key_disagreements": [],
        "reasoning": "all context folders empty",
    })

    lines = []
    tool_ids = {}
    real_agents = ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]
    assistant_content = []
    for agent in real_agents:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    # The phantom entry: its own tool_use block, batched alongside the real
    # ones, with no matching tool_result ever appended below.
    phantom_id = "toolu_phantom"
    assistant_content.append(_make_task_use_block(phantom_id, "general-purpose", "placeholder", "placeholder"))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))

    for agent in real_agents:
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tool_ids[agent], _good_subagent_reply(role))))
    # (deliberately no tool_result for phantom_id)

    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply}))
    return "\n".join(lines)


def build_raw_control_char_council_reply_transcript():
    """Otherwise well-formed transcript, but the final council JSON's
    `reasoning` string contains a literal, unescaped newline character
    (not the two-character `\\n` escape) -- the exact real failure mode
    confirmed live 2026-09-15 (02-compliance-red-flag run 2): the model
    wrote an actual line break right before the mandatory human-review
    reminder sentence instead of JSON-escaping it. Must still parse
    correctly (check (c) passes) once _escape_raw_control_chars_in_json_strings
    repairs it -- proves the harness recovers this specific mechanical
    mistake instead of failing the whole run on it."""

    # Built by hand, not json.dumps(), which would never reproduce this bug
    # (it always escapes control characters correctly).
    council_reply_with_raw_newline = (
        '{\n'
        '  "final_decision": "reject",\n'
        '  "confidence": "high",\n'
        '  "regulatory_context_consulted": false,\n'
        '  "council_summary": {\n'
        '    "cfo": { "recommendation": "reject", "confidence": "high" },\n'
        '    "coo": { "recommendation": "reject", "confidence": "high" },\n'
        '    "cmo": { "recommendation": "reject", "confidence": "high" },\n'
        '    "cto": { "recommendation": "reject", "confidence": "high" },\n'
        '    "chro": { "recommendation": "reject", "confidence": "high" }\n'
        '  },\n'
        '  "key_disagreements": [],\n'
        '  "reasoning": "The list must not be used without re-confirmed consent.\n'
        '\n'
        'This decision should be reviewed by a human before being acted on."\n'
        '}'
    )

    lines = []
    tool_ids = {}
    assistant_content = []
    for agent in ["cfo-agent", "coo-agent", "cmo-agent", "cto-agent", "chro-agent"]:
        tid = f"toolu_{agent}"
        tool_ids[agent] = tid
        assistant_content.append(_make_task_use_block(tid, agent))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}}))
    for agent, tid in tool_ids.items():
        role = agent[: -len("-agent")]
        lines.append(_fixture_line(_make_tool_result(tid, _good_subagent_reply(role))))
    lines.append(_fixture_line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"```json\n{council_reply_with_raw_newline}\n```"}]}}))
    lines.append(_fixture_line({"type": "result", "subtype": "success", "result": council_reply_with_raw_newline}))
    return "\n".join(lines)


def self_test() -> bool:
    ok = True

    print("Self-test 1: well-formed transcript should pass all checks...")
    task_calls, final_text = parse_transcript(build_good_transcript())
    result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["approve"]})
    if result["all_pass"]:
        print("  PASS")
    else:
        print(f"  FAIL (expected all checks to pass): {result}")
        ok = False

    print("Self-test 2: broken transcript should fail (a), (b), and (c)...")
    task_calls, final_text = parse_transcript(build_broken_transcript())
    result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["reject"]})
    expected_failures = not result["checks"]["a_five_invocations"] and not result["checks"]["b_subagent_schema_valid"] and not result["checks"]["c_council_schema_valid"]
    if expected_failures and not result["all_pass"]:
        print("  PASS (failures correctly detected)")
    else:
        print(f"  FAIL (harness did not catch the seeded defects): {result}")
        ok = False

    print("Self-test 3: wrong final_decision should fail check (d) only...")
    task_calls, final_text = parse_transcript(build_good_transcript())
    result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["reject"]})
    if result["checks"]["d_final_decision_expected"] is False and not result["all_pass"] and result["checks"]["a_five_invocations"] and result["checks"]["b_subagent_schema_valid"] and result["checks"]["c_council_schema_valid"]:
        print("  PASS")
    else:
        print(f"  FAIL: {result}")
        ok = False

    print("Self-test 4: XREF-01 regulatory follow-up (7 calls, cto/chro repeated, "
          "chro's 2nd reply has 'conditions', a real Bash query invoked) should pass every check "
          "including (e) on the genuine positive path...")
    regulatory_followup_raw = build_regulatory_followup_transcript()
    task_calls, final_text = parse_transcript(regulatory_followup_raw)
    result = evaluate_run(
        task_calls,
        final_text,
        {"acceptable_final_decisions": ["approve_with_conditions"], "expected_regulatory_context_consulted": True},
        raw_stdout=regulatory_followup_raw,
    )
    if result["all_pass"] and result["checks"]["e_regulatory_consulted_expected"] is True:
        print("  PASS")
    else:
        print(f"  FAIL (harness should accept the legitimate XREF-01 repeat pattern, "
              f"including a correctly-detected real regulatory consult): {result}")
        ok = False

    print("Self-test 5: cfo-agent called twice (not a legitimate repeat) should still fail check (a)...")
    task_calls, final_text = parse_transcript(build_bad_repeat_transcript())
    result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["approve"]})
    if result["checks"]["a_five_distinct_roles"] is False and not result["all_pass"]:
        print("  PASS (illegitimate repeat correctly detected)")
    else:
        print(f"  FAIL (harness should reject a repeat of anyone but cto-agent/chro-agent): {result}")
        ok = False

    print("Self-test 6: phantom 'general-purpose'/'placeholder' launch with no "
          "result should be filtered out, not counted as a 6th invocation...")
    task_calls, final_text = parse_transcript(build_placeholder_artifact_transcript())
    if len(task_calls) != 5:
        print(f"  FAIL (expected the phantom launch to be filtered, got {len(task_calls)} task_calls): "
              f"{[tc['subagent_type'] for tc in task_calls]}")
        ok = False
    else:
        result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["insufficient_data"]})
        if result["all_pass"]:
            print("  PASS (phantom launch filtered, real 5-agent run passes every check)")
        else:
            print(f"  FAIL (phantom launch was filtered but the run still failed a check): {result}")
            ok = False

    print("Self-test 7: council JSON with an invented extra field "
          "(human_review_required) should fail check (c)...")
    task_calls, final_text = parse_transcript(build_extra_council_field_transcript())
    result = evaluate_run(task_calls, final_text, {"acceptable_final_decisions": ["approve"]})
    if result["checks"]["c_council_schema_valid"] is False and not result["all_pass"]:
        print("  PASS (extra top-level field correctly detected)")
    else:
        print(f"  FAIL (harness should reject any council JSON field beyond COUNCIL_SCHEMA_FIELDS): {result}")
        ok = False

    print("Self-test 8: a real Bash regulatory query actually invoked on a "
          "no-jurisdiction example should fail check (e) even though (a)-(d) all pass, "
          "detected from the transcript itself (not the self-reported field)...")
    unjustified_raw = build_unjustified_regulatory_consult_transcript()
    task_calls, final_text = parse_transcript(unjustified_raw)
    result = evaluate_run(
        task_calls,
        final_text,
        {"acceptable_final_decisions": ["insufficient_data"], "expected_regulatory_context_consulted": False},
        raw_stdout=unjustified_raw,
    )
    if (
        result["checks"]["e_regulatory_consulted_expected"] is False
        and not result["all_pass"]
        and result["checks"]["a_five_invocations"]
        and result["checks"]["b_subagent_schema_valid"]
        and result["checks"]["c_council_schema_valid"]
        and result["checks"]["d_final_decision_expected"]
    ):
        print("  PASS (unjustified regulatory consult correctly detected, "
              "despite final_decision itself being the expected insufficient_data)")
    else:
        print(f"  FAIL (harness should catch a jurisdiction-unjustified regulatory consult "
              f"regardless of final_decision): {result}")
        ok = False

    print("Self-test 9: CEO self-reports regulatory_context_consulted: false, but the "
          "transcript shows the Bash query was actually invoked -- check (e) must catch this "
          "from the transcript, not trust the self-report...")
    mismatch_raw = build_self_report_mismatch_transcript()
    task_calls, final_text = parse_transcript(mismatch_raw)
    result = evaluate_run(
        task_calls,
        final_text,
        {"acceptable_final_decisions": ["insufficient_data"], "expected_regulatory_context_consulted": False},
        raw_stdout=mismatch_raw,
    )
    mismatch_flagged = any("does not match the actual Bash invocation" in e for e in result["errors"])
    if result["checks"]["e_regulatory_consulted_expected"] is False and mismatch_flagged:
        print("  PASS (self-report/transcript mismatch correctly caught -- "
              "a false self-report alone would have wrongly passed this run)")
    else:
        print(f"  FAIL (harness must not trust the self-reported field over the actual "
              f"transcript): {result}")
        ok = False

    print("Self-test 10: council JSON with a literal, unescaped newline inside the "
          "reasoning string (not the \\n escape) should still parse and pass check (c) -- "
          "the exact real 02-compliance-red-flag run 2 failure...")
    raw_control_char_raw = build_raw_control_char_council_reply_transcript()
    task_calls, final_text = parse_transcript(raw_control_char_raw)
    result = evaluate_run(
        task_calls,
        final_text,
        {"acceptable_final_decisions": ["reject"], "expected_regulatory_context_consulted": False},
        raw_stdout=raw_control_char_raw,
    )
    if result["all_pass"] and result["checks"]["c_council_schema_valid"] is True:
        print("  PASS (raw control character inside a JSON string literal repaired and parsed)")
    else:
        print(f"  FAIL (harness should repair a literal newline inside a JSON string value, "
              f"not fail the whole run on it): {result}")
        ok = False

    print("Self-test 11: jurisdiction: field extraction and the code-level Bash-gating "
          "decision it drives (compute_tool_flags) -- no CLI invocation, pure logic. "
          "Gate is --disallowedTools, not omission from --allowedTools -- confirmed live "
          "2026-09-16 that omission alone does not actually deny a tool call...")
    cases = [
        ("jurisdiction: KSA\n\nProposal: text", "KSA", True, "Agent,Task,Bash", None),
        ("jurisdiction: Saudi Arabia\n\nProposal: text", "Saudi Arabia", True, "Agent,Task,Bash", None),
        ("jurisdiction: saudi arabia\n\nProposal: text", "saudi arabia", True, "Agent,Task,Bash", None),  # case-insensitive
        ("jurisdiction: unspecified\n\nProposal: text", "unspecified", False, "Agent,Task", "Bash"),
        ("jurisdiction: EU\n\nProposal: text", "EU", False, "Agent,Task", "Bash"),
        ("Proposal: no jurisdiction line at all", None, False, "Agent,Task", "Bash"),  # missing field == unspecified
        ("Jurisdiction:   KSA  \n\nProposal: text", "KSA", True, "Agent,Task,Bash", None),  # whitespace/case in the label
    ]
    jurisdiction_ok = True
    for decision_text, expected_field, expected_recognized, expected_allowed, expected_disallowed in cases:
        field = extract_jurisdiction_field(decision_text)
        recognized = is_recognized_saudi_jurisdiction(field)
        allowed, disallowed = compute_tool_flags(decision_text)
        if (
            field != expected_field
            or recognized != expected_recognized
            or allowed != expected_allowed
            or disallowed != expected_disallowed
        ):
            jurisdiction_ok = False
            print(f"  FAIL for {decision_text[:40]!r}: got field={field!r} recognized={recognized} "
                  f"allowed={allowed!r} disallowed={disallowed!r}, expected field={expected_field!r} "
                  f"recognized={expected_recognized} allowed={expected_allowed!r} disallowed={expected_disallowed!r}")
    if jurisdiction_ok:
        print("  PASS (all extraction/gating cases correct, including case-insensitivity, "
              "whitespace tolerance, a missing field defaulting to unspecified, and Bash landing "
              "in --disallowedTools rather than merely being left out of --allowedTools)")
    else:
        ok = False

    print("Self-test 12: CEO attempts the regulatory Bash call but it hard-errors "
          "(--disallowedTools \"Bash\" in effect) -- must pass check (e), since no real data "
          "was ever returned; the exact real 01-clear-approve run 2 scenario...")
    bash_disabled_raw = build_bash_attempted_but_disabled_transcript()
    task_calls, final_text = parse_transcript(bash_disabled_raw)
    result = evaluate_run(
        task_calls,
        final_text,
        {"acceptable_final_decisions": ["approve", "approve_with_conditions"], "expected_regulatory_context_consulted": False},
        raw_stdout=bash_disabled_raw,
    )
    if result["all_pass"] and result["checks"]["e_regulatory_consulted_expected"] is True:
        print("  PASS (attempted-but-hard-errored Bash call correctly not counted as the "
              "regulatory step having run)")
    else:
        print(f"  FAIL (an attempt that returned no real data should not fail check (e)): {result}")
        ok = False

    return ok


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--example", action="append", help="Only run this example (by folder name). Can repeat. Default: all.")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per example (default: 3).")
    parser.add_argument("--claude-bin", default="claude", help="Path to the claude CLI (default: 'claude' on PATH).")
    parser.add_argument("--timeout", type=int, default=900, help="Per-run timeout in seconds (default: 900).")
    parser.add_argument("--no-skip-permissions", action="store_true", help="Do not pass --dangerously-skip-permissions to the CLI (will hang on any permission prompt in a non-interactive run).")
    parser.add_argument("--log-dir", default=None, help="Directory to write raw transcripts to (default: examples/.results/<timestamp>/).")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in fixture self-test and exit (no CLI invocation, no context staging).")
    args = parser.parse_args(argv)

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    examples = discover_examples()
    if args.example:
        wanted = set(args.example)
        examples = [e for e in examples if e.name in wanted]
        missing = wanted - {e.name for e in examples}
        if missing:
            print(f"error: unknown example(s): {sorted(missing)}", file=sys.stderr)
            sys.exit(2)
    if not examples:
        print("error: no examples found under examples/", file=sys.stderr)
        sys.exit(2)

    # On Windows a globally npm-installed CLI is a .cmd shim with no .exe;
    # subprocess.run([name, ...]) without shell=True can't launch that (fails
    # with FileNotFoundError even though the bare name works interactively).
    # shutil.which applies the same PATHEXT resolution a shell would.
    args.claude_bin = shutil.which(args.claude_bin) or args.claude_bin

    log_dir = Path(args.log_dir) if args.log_dir else SCRIPT_DIR / ".results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"Staging context/ from: {CONTEXT_ROOT}")
    print(f"Backing up current context/ before making any changes...")
    backup_dir = backup_context()

    all_results = []
    try:
        for example_dir in examples:
            print(f"\nRunning {example_dir.name} x{args.runs}...")
            result = run_example(
                example_dir,
                runs=args.runs,
                claude_bin=args.claude_bin,
                skip_permissions=not args.no_skip_permissions,
                timeout=args.timeout,
                log_dir=log_dir,
            )
            all_results.append(result)
    finally:
        print("\nRestoring original context/ contents...")
        restore_context(backup_dir)

    print(f"\nRaw transcripts written to: {log_dir}\n")
    ok = print_report(all_results)

    report_path = log_dir / "summary.json"
    report_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"Full results written to: {report_path}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
