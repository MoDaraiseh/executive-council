"""Code-level jurisdiction gate for /council, run as a Claude Code hook.

Registered in .claude/settings.json for UserPromptSubmit, PreToolUse
(Bash|PowerShell), PostToolUse (Bash|PowerShell) and SessionEnd. It is
also imported by examples/validate.py, so the harness and an ordinary
interactive /council run apply the same jurisdiction rule.

What it enforces:

* When a prompt starts with ``/council``, the ``jurisdiction:`` field is
  read in code (no model involved) and stored for that session.
* While that /council turn is in progress, the only shell command allowed
  is the saudi-law-tracker query, and only when the jurisdiction is Saudi.
  For any other jurisdiction, every Bash/PowerShell call is denied. That
  includes calls made from inside a subagent: hooks see subagent tool calls
  under the parent session_id, so delegating the command does not get
  around the gate.
* Outside a /council turn, a saudi-law-tracker command is denied as well
  (fail closed). Other shell commands are left alone, so ordinary work in
  this repo is unaffected.

The state for a /council turn lasts until the next real user prompt.
Async-agent ``<task-notification>`` prompts, which the CLI injects in the
middle of a turn, leave it in place.

If ``COUNCIL_GATE_AUDIT_LOG`` is set, every decision and every executed
saudi-law-tracker call is appended to that file as JSON lines.
examples/validate.py uses this as a second signal, independent of the
transcript, for check (e).

The hook needs ``python`` (3.9+) on PATH. If it cannot run at all, Claude
Code treats that as a non-blocking hook error and the tool call goes
ahead. examples/validate.py also blocks the shell at the CLI level
(``--tools`` / ``--disallowedTools``), so harness runs are covered either
way; for interactive use, make sure ``python`` resolves.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

# --------------------------------------------------------------------------
# Jurisdiction parsing (shared with examples/validate.py)
# --------------------------------------------------------------------------

# Matched after normalization (see jurisdiction_segments). Plain "SA" is
# left out on purpose: it is just as often South Africa.
SAUDI_ALIASES = frozenset({
    "ksa",
    "saudi arabia",
    "kingdom of saudi arabia",
    "saudi",
    "السعودية",
    "المملكة العربية السعودية",
})

# [ \t]* rather than \s* around the value, so a blank "jurisdiction:" line
# can't swallow the next line as its value.
_JURISDICTION_FIELD_RE = re.compile(r"^[ \t]*jurisdiction[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_SEGMENT_SPLIT_RE = re.compile(r"[,;/|&+]|\s[-–—]\s|\band\b|\bor\b")
_COUNCIL_PROMPT_RE = re.compile(r"^\s*/council(?:-[a-z0-9-]+)?(?=\s|$)", re.IGNORECASE)
_REGULATORY_COMMAND_RE = re.compile(r"saudi[-_ ]?law[-_ ]?tracker", re.IGNORECASE)


def extract_jurisdiction_field(decision_text: str) -> str | None:
    """Value of the first ``jurisdiction:`` line, or None if there is none.

    A missing field is treated as ``unspecified`` by every caller, as
    council.md step 1 says.
    """
    m = _JURISDICTION_FIELD_RE.search(decision_text or "")
    return m.group(1) if m else None


def jurisdiction_segments(value: str) -> list[str]:
    """Normalizes a jurisdiction value into the list of places it names.

    Steps: case-fold; drop parentheticals (``KSA (Riyadh)`` -> ``KSA``);
    split lists on , ; / | & + `` - `` and/or; remove dots (``K.S.A.`` ->
    ``ksa``); turn hyphens and underscores into spaces; remove markdown
    emphasis and quotes; drop a leading "the".
    """
    v = unicodedata.normalize("NFKC", value).casefold()
    v = _PARENTHETICAL_RE.sub(" ", v)
    out = []
    for part in _SEGMENT_SPLIT_RE.split(v):
        part = part.replace(".", "")
        part = re.sub(r"[-_]", " ", part)
        part = re.sub(r"[*`'\"]", "", part)
        part = " ".join(part.split())
        part = part.removeprefix("the ")
        if part:
            out.append(part)
    return out


def is_recognized_saudi_jurisdiction(value: str | None) -> bool:
    """True if any place named in the value is Saudi Arabia.

    So ``KSA``, ``Kingdom of Saudi Arabia``, ``KSA (Riyadh)``,
    ``Riyadh, KSA`` and ``KSA, UAE`` all count. ``EU``, ``unspecified``,
    ``not KSA``, ``non-Saudi`` and a missing field do not.
    """
    if not value:
        return False
    return any(seg in SAUDI_ALIASES for seg in jurisdiction_segments(value))


def council_args(prompt: str) -> str | None:
    """The argument text if ``prompt`` invokes /council, else None.

    ``/council-<suffix>`` also counts: examples/validate.py runs a copy of
    the command under a different name so that a user-level
    ~/.claude/commands/council.md can't shadow the repo's copy.
    """
    m = _COUNCIL_PROMPT_RE.match(prompt or "")
    return prompt[m.end():].lstrip() if m else None


def is_regulatory_command(command: str) -> bool:
    return bool(_REGULATORY_COMMAND_RE.search(command or ""))


# --------------------------------------------------------------------------
# Per-session state
# --------------------------------------------------------------------------


def _state_dir() -> Path:
    override = os.environ.get("COUNCIL_GATE_STATE_DIR")
    return Path(override) if override else Path(tempfile.gettempdir()) / "executive-council-gate"


def _state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "no-session")
    return _state_dir() / f"{safe}.json"


def load_state(session_id: str) -> dict | None:
    try:
        return json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(session_id: str, state: dict) -> None:
    path = _state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def clear_state(session_id: str) -> None:
    try:
        _state_path(session_id).unlink()
    except OSError:
        pass


def _audit(entry: dict) -> None:
    log = os.environ.get("COUNCIL_GATE_AUDIT_LOG")
    if not log:
        return
    entry = {"ts": time.time(), **entry}
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Hook handlers
# --------------------------------------------------------------------------


def handle_prompt(payload: dict) -> None:
    session_id = payload.get("session_id") or ""
    prompt = payload.get("prompt") or ""
    args = council_args(prompt)
    if args is not None:
        jurisdiction = extract_jurisdiction_field(args)
        saudi = is_recognized_saudi_jurisdiction(jurisdiction)
        save_state(session_id, {"council": True, "jurisdiction": jurisdiction, "saudi": saudi})
        _audit({"event": "council_start", "session_id": session_id, "jurisdiction": jurisdiction, "saudi": saudi})
    elif prompt.lstrip().startswith("<"):
        # CLI-injected notification (e.g. <task-notification> when an async
        # subagent finishes) in the middle of the same turn. Keep the state.
        pass
    else:
        clear_state(session_id)


def pretool_decision(payload: dict) -> tuple[str | None, str]:
    """Returns (decision, reason), where decision is "deny" or None.

    None means the hook has no opinion and the normal permission flow
    applies.
    """
    command = str((payload.get("tool_input") or {}).get("command") or "")
    regulatory = is_regulatory_command(command)
    state = load_state(payload.get("session_id") or "")
    if state and state.get("council"):
        jurisdiction = state.get("jurisdiction")
        if not state.get("saudi"):
            return "deny", (
                f"Blocked by the executive-council jurisdiction gate: this /council run's jurisdiction field is "
                f"{jurisdiction!r}, which is not a recognized Saudi value, so no shell commands (and in particular "
                "no saudi-law-tracker query) may run for it. Continue without regulatory context and say so in "
                "`reasoning`."
            )
        if not regulatory:
            return "deny", (
                "Blocked by the executive-council jurisdiction gate: during /council the only shell command allowed "
                "is the saudi-law-tracker query from council.md step 5."
            )
        return None, "saudi-law-tracker query for a Saudi-jurisdiction /council run"
    if regulatory:
        return "deny", (
            "Blocked by the executive-council jurisdiction gate: in this project the saudi-law-tracker query only "
            "runs inside a /council run whose jurisdiction field is Saudi (e.g. `jurisdiction: KSA`)."
        )
    return None, "not a /council turn and not a regulatory command"


def handle_pretool(payload: dict) -> None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    try:
        decision, reason = pretool_decision(payload)
    except Exception as e:  # noqa: BLE001 -- fail closed for the regulatory command, open otherwise
        decision = "deny" if is_regulatory_command(command) else None
        reason = f"jurisdiction gate error ({type(e).__name__}: {e}); failing closed for saudi-law-tracker"
    _audit({
        "event": "pretool",
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "tool_name": payload.get("tool_name"),
        "command": command,
        "regulatory": is_regulatory_command(command),
        "decision": decision or "no_opinion",
        "reason": reason,
    })
    if decision == "deny":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }))


def handle_posttool(payload: dict) -> None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not is_regulatory_command(command):
        return
    _audit({
        "event": "posttool",
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "tool_name": payload.get("tool_name"),
        "command": command,
        "regulatory": True,
        "tool_response": str(payload.get("tool_response"))[:2000],
    })


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        handle_prompt(payload)
    elif event == "PreToolUse":
        handle_pretool(payload)
    elif event == "PostToolUse":
        handle_posttool(payload)
    elif event == "SessionEnd":
        clear_state(payload.get("session_id") or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
