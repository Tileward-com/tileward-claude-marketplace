#!/usr/bin/env python3
"""Tileward execution policy as a Claude Code PreToolUse hook. NOT YET IN SERVICE.

    THE ENDPOINT THIS CALLS DOES NOT EXIST. `POST /v1/guard/tool` answers 404 (measured against
    production 2026-08-25; the API registers only /v1/guard and /v1/guard/hook). There is no
    execution policy `kind` on a key and no console page to author one.

    Consequently this script is NOT registered by the plugin — hooks.json wires up the prompt
    hook only — and you should not wire it up by hand either. It fails CLOSED, so against a 404
    it refuses every tool call in the session; the only way to stop that is TILEWARD_FAIL_OPEN=1,
    which allows everything and governs nothing. There is no setting in between, because there is
    nothing on the other end. A fail-closed gate aimed at a missing endpoint is worse than no gate,
    because it looks installed.

    It is kept here as the client half that the server work will register in one line. See
    tileward.com issue #257 (this gap), #189 (the policy kind) and #190 (the endpoint). The
    request shape it sends was SETTLED on 2026-08-25 -- `{tool_name, tool_input}`, see below --
    so #190 can be built against it.

The sibling hook (tileward_guard_hook.py) governs the ASK, and does work today. This one is meant
to govern what the agent then DOES with it: Claude Code runs a PreToolUse hook before every tool
call, hands over the tool name and its input, and honours a block. Between the two, a prompt and
every action it leads to would be decided by the same key, out of band, and the model is never
asked to police itself.

WHAT WOULD DECIDE: an `{allow, deny}` execution policy bound to your Tileward API key. No such
policy can be created yet (see above), so today every reachable answer is a 404.

WHAT IS SENT: the tool NAME **and its INPUT**. Settled 2026-08-25, reversing the name-only shape
this script originally had. Be clear about what that costs, because it is not small: the input to a
Bash call is a command line and the input to an Edit or a Write is your source code, so this hook
sends both to Tileward, and they land in the audit trail.

    The reason is that name-only cannot do the job it exists for. `Bash(rm -rf /)` and `Bash(ls)`
    are byte-identical on the wire when you send only "Bash", so a policy over names is a
    CAPABILITY gate — "may this key run Bash at all" — and can never be a COMMAND gate. Governing
    the ask and then permitting every command the ask leads to is the gap this hook was written to
    close, and name-only does not close it.

    So the trade is deliberate: to gate on what a tool actually does, the thing it does has to
    leave the machine. Anyone unwilling to make that trade sets TILEWARD_TOOL_INPUT_MAX=0 and gets
    the old capability gate back, with no code change and no fork.

FIDELITY IS ALWAYS DECLARED. `tool_input_fidelity` is on EVERY request and is one of "full",
"truncated" or "omitted". It is never inferred from a missing field, on purpose: a policy that has
to guess whether it saw the whole command is a policy that will one day approve the half it saw.
Long strings are cut at TILEWARD_TOOL_INPUT_MAX characters, and a body that is still oversize after
that drops `tool_input` entirely rather than shipping a mangled half — in both cases the server is
told, and decides for itself whether a partial view is good enough to authorize on.

FAIL-CLOSED by default: if the endpoint cannot be reached, or the payload cannot be read, the tool
call is BLOCKED. Set TILEWARD_FAIL_OPEN=1 to invert that if availability matters more to you than
the policy.

BUT BE HONEST ABOUT THE LIMIT OF THAT PROMISE, exactly as the prompt hook is. Claude Code itself
fails OPEN when a hook times out or its command is missing: the tool runs, the error is only
logged, and there is no fail-closed flag in the hook spec to change that. "Fail closed" here means:
for every failure this script can still catch, it exits 2. It cannot catch its own absence.

    What this covers:      endpoint down, DNS/TLS failure, 401/403/revoked key, slow endpoint
                           (see the timeout rule), junk response, unset key, unreadable payload,
                           unhandled crash.
    What it CANNOT cover:  the script being deleted, made non-executable, or hanging past Claude
                           Code's own hook timeout. Those fail open, outside this process.
    Therefore:             TILEWARD_TIMEOUT must stay comfortably BELOW the hook's `timeout` in
                           settings.json. At 5s vs 15s this script always answers first, so the
                           timeout that matters is ours and we block on it.

EXIT CODES ARE THE WHOLE CONTRACT, AND THEY ARE A TRAP:
    2  -> blocked. The tool does not run, and stderr is fed back to Claude, which is the right
          audience here: Claude learns the tool is unavailable and can say so or try another way.
          (The prompt hook's exit 2 shows stderr to the USER instead — same code, different
          audience, because it is a different event.)
    0  -> allowed.
    1  -> NOT a block. Claude Code treats any non-2 non-zero code as a non-blocking error, logs
          it, and runs the tool anyway. So a hook that crashes, or exits 1 on error, silently
          stops gating while still looking installed. Every path below exits 0 or 2, never 1, and
          the bare `except` at the bottom exists for exactly that reason.

WHY NOT AN HTTP HOOK POINTED STRAIGHT AT THE ENDPOINT? Once the endpoint exists, you will be able
to: it is meant to speak this contract and need no local script, and for a fleet deployed through
managed settings that is often the better trade. What it cannot do is block when Claude Code cannot
reach Tileward at all — a non-2xx, including no response, is a non-blocking error there. A local
process can, which is the only reason to run this instead. Today a `type: "http"` hook pointed at
/v1/guard/tool gets a 404 and therefore runs every tool call unexamined.

Config (environment):
    TILEWARD_API_KEY    required. The key whose BOUND EXECUTION POLICY decides.
    TILEWARD_TOOL_API   default https://api.tileward.com/v1/guard/tool — which 404s today
    TILEWARD_TIMEOUT    seconds, default 5. Keep it well under the hook's own timeout.
    TILEWARD_FAIL_OPEN  1 = allow when the endpoint is unreachable. Default 0 = block.
    TILEWARD_TOOL_INPUT_MAX
                        characters of each string inside tool_input to send. Default 4096.
                        0 sends NO tool_input at all, which is the pre-2026-08-25 capability
                        gate: private, and unable to tell `rm -rf /` from `ls`.
    TILEWARD_ACTOR      who this machine's tool calls are attributed to in the audit report.
                        Defaults to the OS username; `-` sends nothing. It is a CLAIM, not proof —
                        see tileward_guard_hook.py, which explains why at length.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("TILEWARD_TOOL_API", "https://api.tileward.com/v1/guard/tool")
KEY = os.environ.get("TILEWARD_API_KEY", "").strip()
DEFAULT_TIMEOUT = 5.0
# Ceiling on TILEWARD_TIMEOUT. hooks.json grants this script 15s; a value at or above that lets
# the harness kill it mid-request, and a killed command hook is a non-blocking error -- the gate
# would stop gating at exactly the moment the network was slowest. 14 keeps the last word here.
MAX_TIMEOUT = 14.0


def _timeout() -> float:
    """Seconds to wait for the guard before giving up. Parsed defensively, at import time.

    A bad value must not raise. Module-level code runs OUTSIDE the try/except around main(), so an
    exception escapes as a traceback and exit 1 -- the one non-zero code Claude Code reads as a
    non-blocking error, which means the prompt runs. This line used to be a bare float() and did
    exactly that: TILEWARD_TIMEOUT set to an empty string, to `5s`, or to any other unparseable
    text disabled the gate while leaving it installed and apparently healthy.

    Empty is the realistic way in, and it does not look like a mistake at the call site: `export
    TILEWARD_TIMEOUT=` in a shell profile, an empty value in a managed-settings env block, or a CI
    variable declared and never given a value. os.environ.get's default does not cover it, because
    the variable IS set -- it is set to nothing -- so the `or` below is doing the real work.

    Out-of-range values fall back to the default rather than clamping to the nearest edge. Someone
    who wrote a negative or an infinite timeout did not mean "as long as it takes"; they wrote
    something unusable, and the default is the only reading that is safe in both directions. The
    comparison also disposes of nan, which fails every ordering it is given.
    """
    try:
        value = float(os.environ.get("TILEWARD_TIMEOUT", "") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if 0 < value <= MAX_TIMEOUT else DEFAULT_TIMEOUT


TIMEOUT = _timeout()
FAIL_OPEN = os.environ.get("TILEWARD_FAIL_OPEN", "0") == "1"


def _tool_input_max() -> int:
    """Characters of each string inside tool_input to send; 0 disables sending it at all.

    Parsed defensively and at import time. A bad value must not raise here: module-level code runs
    OUTSIDE the try/except around main(), so an exception would escape as a traceback and a
    non-zero, non-2 exit -- which Claude Code reads as a non-blocking error and runs the tool
    anyway. Garbage in this variable would then silently disable the gate, which is the exact
    failure mode this file spends 60 lines refusing to have.
    """
    try:
        value = int(os.environ.get("TILEWARD_TOOL_INPUT_MAX", "4096"))
    except ValueError:
        return 4096
    return max(0, value)


TOOL_INPUT_MAX = _tool_input_max()
# Backstop on the whole serialized body. Per-string clamping bounds one value, not a payload with
# thousands of them, and a request big enough to be refused upstream fails closed on every tool
# call. 256 KiB is far above any real command line and far below anything a WAF objects to.
TOOL_BODY_MAX = 256 * 1024


def _actor() -> str:
    """WHO this decision is attributed to, sent as X-Tileward-Actor. A claim, not an attestation:
    this runs on the governed person's own machine, under their own environment. See the long note
    in tileward_guard_hook.py. Issue one key per person if you need identity that holds up."""
    a = os.environ.get("TILEWARD_ACTOR", "").strip()
    if a == "-":
        return ""
    if not a:
        try:
            a = getpass.getuser()
        except Exception:
            return ""
    return "".join(c for c in a if 0x20 <= ord(c) < 0x7f)[:128]


def block(reason: str) -> None:
    """Exit 2: the tool does not run. stderr is fed back to Claude."""
    print(f"[Tileward] {reason}", file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


def unreachable(detail: str) -> None:
    """The endpoint could not answer. Fail closed unless explicitly told otherwise."""
    if FAIL_OPEN:
        print(f"[Tileward] execution policy unreachable ({detail}); TILEWARD_FAIL_OPEN=1, "
              f"allowing.", file=sys.stderr)
        allow()
    block(f"execution policy unreachable ({detail}); blocking this tool call. "
          f"Set TILEWARD_FAIL_OPEN=1 to allow instead.")


def decision_of(data: dict) -> str | None:
    """"allow" / "deny" from the response, or None if it says neither.

    BOTH SPELLINGS, because the endpoint sends both and Claude Code has used both:
    `hookSpecificOutput.permissionDecision` is current, `decision: "block"` is the older form.
    Reading only one would make this script's answer depend on which half of the reply it
    happened to look at — and an unrecognised reply is treated as unreachable, i.e. blocked,
    rather than guessed at.
    """
    hso = data.get("hookSpecificOutput")
    if isinstance(hso, dict):
        d = hso.get("permissionDecision")
        if d in ("allow", "deny"):
            return d
        # "ask" is a real value in the contract and this script cannot render a prompt. Treated as
        # deny: the safe reading of "a human should decide" is not "proceed without one".
        if d == "ask":
            return "deny"
    legacy = data.get("decision")
    if legacy == "block":
        return "deny"
    if legacy in ("approve", "allow"):
        return "allow"
    return None


def clamp(value: object, limit: int, cut: list[bool]) -> object:
    """Copy `value`, cutting every string longer than `limit`. `cut[0]` records whether it cut.

    Structure is preserved rather than the payload being truncated as one blob, because the server
    is authorizing a decision on this: `{"command": "rm -rf /"}` truncated to valid JSON with a
    short `command` is still readable as a policy input, whereas half a JSON document is not
    readable as anything. Numbers, booleans and null pass through untouched -- clamping them would
    change their meaning rather than their length.
    """
    if isinstance(value, str):
        if len(value) > limit:
            cut[0] = True
            return value[:limit]
        return value
    if isinstance(value, list):
        return [clamp(v, limit, cut) for v in value]
    if isinstance(value, dict):
        return {k: clamp(v, limit, cut) for k, v in value.items()}
    return value


def build_body(name: str, tool_input: object) -> bytes:
    """The settled request shape: {tool_name, tool_input, tool_input_fidelity}.

    `tool_input_fidelity` is always present and always one of full/truncated/omitted. It is never
    left to be inferred from an absent `tool_input`: a server that reads "no field" as "nothing to
    see" will authorize a command it never read, and the whole argument for sending the input in
    the first place is that a gate has to see what it is authorizing.
    """
    body: dict[str, object] = {"tool_name": name}

    if TOOL_INPUT_MAX == 0 or not isinstance(tool_input, (dict, list)):
        # Either the operator turned it off, or Claude Code gave us no structured input for this
        # tool. Both are "the policy does not get to see the input", and the server is told so.
        body["tool_input_fidelity"] = "omitted"
        return json.dumps(body).encode()

    cut = [False]
    body["tool_input"] = clamp(tool_input, TOOL_INPUT_MAX, cut)
    body["tool_input_fidelity"] = "truncated" if cut[0] else "full"

    encoded = json.dumps(body).encode()
    if len(encoded) <= TOOL_BODY_MAX:
        return encoded

    # Still oversize after clamping every string. Drop the input rather than ship a mangled half:
    # a policy told "omitted" can refuse, while a policy handed an arbitrary prefix of a command
    # may approve the prefix. Truncation is only safe when the server knows it happened, and past
    # this point we can no longer describe WHAT was lost.
    body.pop("tool_input", None)
    body["tool_input_fidelity"] = "omitted"
    return json.dumps(body).encode()


def main() -> None:
    if not KEY:
        # Misconfiguration is a blocking condition, not a warning: an unconfigured gate that
        # allows is worse than no gate, because it looks like one.
        block("TILEWARD_API_KEY is not set; blocking. An unconfigured guard cannot govern "
              "anything.")

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        unreachable(f"could not parse the hook payload: {e}")
        return

    name = event.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        # The payload shape changed, or the event is not what we think it is. Either way this hook
        # is not doing its job, so it says so rather than passing silently — the same reasoning as
        # the prompt hook's empty-prompt block.
        block("no tool_name in the hook payload. Blocking, because a gate that cannot see what is "
              "being called cannot authorize it.")

    # NAME AND INPUT -- see the module docstring for why this reversed, and what it costs. The
    # decision needs the input: a policy over names alone cannot tell `rm -rf /` from `ls`.
    body = build_body(name, event.get("tool_input"))
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    # Identify ourselves. NOT cosmetic: api.tileward.com sits behind Cloudflare, which bans
    # urllib's default "Python-urllib/x.y" agent outright (403, error 1010). Without this every
    # check 403s, and since we fail closed that means every tool call in the org is blocked while
    # the error blames the API key. Verified on the sibling hook: urllib -> 403, this -> 200.
    req.add_header("User-Agent", "tileward-pretooluse-hook/1.0")
    # Ties this decision to the prompt that led to it in the audit trail. Same reasoning as the
    # prompt hook, and deliberately the SAME id: an administrator reading a refused tool call
    # wants the prompt beside it, and `cc-<prompt_id>` is what puts them on the same row group.
    # No session_id fallback -- it repeats across every prompt in a session, and the audit counts
    # rows sharing a request id to surface re-submissions.
    pid = event.get("prompt_id")
    if isinstance(pid, str) and pid:
        req.add_header("X-Request-Id", f"cc-{pid}"[:128])
    actor = _actor()
    if actor:
        req.add_header("X-Tileward-Actor", actor)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Do not guess at causes. 401 is Tileward's own "invalid or revoked key"; a 403 is NOT --
        # it comes from the CDN in front of the API, and calling it a key problem sends an admin
        # to rotate a key that was never the fault.
        #
        # Once the endpoint exists this branch is unreachable in normal operation: it is specified
        # to answer 200 even when it denies, precisely because a non-2xx would be read as "allow" by
        # an HTTP hook. Reaching it means something in front of the endpoint answered, not the
        # endpoint. TODAY it is the ONLY branch: the route does not exist and every call 404s.
        #
        # A 404 is NOT special-cased into an allow, deliberately. It is not a reliable signal for
        # "not built yet": a mistyped TILEWARD_TOOL_API, a proxy, a WAF rule, or a routing
        # regression after the endpoint ships all produce one. Mapping it to allow would turn any
        # of those into a silent, permanent bypass in a control whose whole premise is that a gate
        # which stops gating while still looking installed is the worst outcome available. The gap
        # is handled where it can be handled honestly — by not registering this hook at all.
        detail = f"HTTP {e.code}"
        if e.code == 404:
            detail += " (no such endpoint: execution policy is not implemented yet, see #257)"
        elif e.code == 401:
            detail += " (key invalid or revoked)"
        elif e.code == 403:
            detail += " (refused upstream, not by Tileward auth: check the CDN/WAF, not the key)"
        unreachable(detail)
        return
    except Exception as e:  # network, DNS, timeout, TLS
        unreachable(f"{type(e).__name__}: {e}")
        return

    if not isinstance(data, dict):
        unreachable(f"unexpected response: {str(data)[:120]}")
        return
    decision = decision_of(data)
    if decision is None:
        unreachable(f"response carried no decision: {str(data)[:120]}")
        return
    if decision == "allow":
        allow()

    hso = data.get("hookSpecificOutput")
    reason = (hso.get("permissionDecisionReason") if isinstance(hso, dict) else None) \
        or data.get("reason") \
        or "This tool is off-policy for your organization's Tileward key."
    block(f"{reason} Contact your administrator if you believe this is wrong.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # Never exit 1 here. An unhandled crash must not become a silent allow.
        print(f"[Tileward] hook error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0 if FAIL_OPEN else 2)
