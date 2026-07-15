#!/usr/bin/env python3
"""Tileward guard as a Claude Code UserPromptSubmit hook.

Claude Code runs this BEFORE the model sees a prompt: it pipes the event to stdin as JSON and
reads the exit code. Exit 2 blocks the prompt. That is what makes this a gate rather than advice.
The model is never asked to police itself, the policy is never sent to a model, and an off-policy
prompt costs zero generation tokens because nothing is generated.

This is deliberately NOT an MCP server. An MCP server exposes tools the model may choose to call
and the user may remove; it cannot intercept a prompt. A hook can, and with `allowManagedHooksOnly`
in managed settings an admin can install this so the user cannot remove it. See README.md.

FAIL-CLOSED by default: if the guard cannot be reached, or the payload cannot be read, the prompt
is BLOCKED. A control that allows everything whenever its backend is unreachable is not a control.
Set TILEWARD_FAIL_OPEN=1 to invert that if availability matters more to you than the policy.

BUT BE HONEST ABOUT THE LIMIT OF THAT PROMISE. Claude Code itself fails OPEN when a hook times out
or its command is missing: the prompt is sent, the error is only logged, and there is no
fail-closed flag in the hook spec to change that. So "fail closed" here means: for every failure
this script can still catch, it exits 2. It cannot catch its own absence.

    What this covers:      guard down, DNS/TLS failure, 401/403/revoked key, 402 no balance,
                           slow guard (see the timeout rule below), junk response, unset key,
                           unreadable payload, unhandled crash.
    What it CANNOT cover:  the script being deleted, made non-executable, or hanging past Claude
                           Code's own hook timeout. Those fail open, outside this process.
    Therefore:             TILEWARD_TIMEOUT must stay comfortably BELOW the hook's `timeout` in
                           settings.json (default 30s for UserPromptSubmit). At 5s vs 30s this
                           script always answers first, so the timeout that matters is ours and we
                           block on it. If you raise TILEWARD_TIMEOUT above the hook timeout, Claude
                           Code kills the script and allows the prompt: the gate quietly inverts.
                           Deploy via managed settings and monitor that the file exists.

EXIT CODES ARE THE WHOLE CONTRACT, AND THEY ARE A TRAP:
    2  -> blocked. The prompt does not reach the model, and stderr is shown to the USER (not fed
          to the model). This is the documented path for gates that must block deterministically.
    0  -> allowed.
    1  -> NOT a block. Claude Code treats any non-2 non-zero code as a non-blocking error, logs it,
          and runs the prompt anyway. So a hook that crashes, or that exits 1 on error, silently
          stops gating while still looking installed. Every path below exits 0 or 2, never 1, and
          the bare `except` at the bottom exists for exactly that reason.

WHY NOT AN HTTP HOOK POINTED STRAIGHT AT THE GUARD? Claude Code supports `type: "http"`, which
would need no local script. But it blocks only on a 2xx response whose body says
{"decision": "block"}. /v1/guard answers {"cost_micros": N, "result": {"allowed": false}}, so
Claude Code would read 2xx, find no `decision`, and ALLOW every prompt while looking installed.
Non-2xx fails open too. An HTTP hook needs a Tileward endpoint that speaks the hook's own contract;
until that exists, use this script.

Config (environment):
    TILEWARD_API_KEY    required. The key whose BOUND POLICY decides. A key with no policy blocks
                        nothing, so bind one in the console first (Settings -> API keys).
    TILEWARD_API        default https://api.tileward.com/v1/guard
    TILEWARD_TIMEOUT    seconds, default 5. Keep it well under the hook's own timeout.
    TILEWARD_FAIL_OPEN  1 = allow when the guard is unreachable. Default 0 = block.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("TILEWARD_API", "https://api.tileward.com/v1/guard")
KEY = os.environ.get("TILEWARD_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("TILEWARD_TIMEOUT", "5"))
FAIL_OPEN = os.environ.get("TILEWARD_FAIL_OPEN", "0") == "1"

# Fields the user's text might arrive under. `prompt` is the documented one; the rest are
# defensive. If NONE of them yield text we block rather than guess, because a guard that reads the
# wrong field sees an empty string, gets told "allowed", and waves everything through forever while
# appearing to work. That failure is invisible, which is what makes it worth the paranoia.
PROMPT_FIELDS = ("prompt", "user_prompt", "userPrompt", "message", "text", "input")


def block(reason: str) -> None:
    """Exit 2: the prompt is refused and never reaches the model. stderr carries the reason."""
    print(f"[Tileward] {reason}", file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


def unreachable(detail: str) -> None:
    """The guard could not answer. Fail closed unless explicitly told otherwise."""
    if FAIL_OPEN:
        print(f"[Tileward] guard unreachable ({detail}); TILEWARD_FAIL_OPEN=1, allowing.",
              file=sys.stderr)
        allow()
    block(f"guard unreachable ({detail}); blocking. Set TILEWARD_FAIL_OPEN=1 to allow instead.")


def extract_prompt(event: dict) -> str:
    for f in PROMPT_FIELDS:
        v = event.get(f)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def main() -> None:
    if not KEY:
        # Misconfiguration is a blocking condition, not a warning: an unconfigured gate that allows
        # is worse than no gate, because it looks like one.
        block("TILEWARD_API_KEY is not set; blocking. An unconfigured guard cannot govern anything.")

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        unreachable(f"could not parse the hook payload: {e}")
        return

    prompt = extract_prompt(event)
    if not prompt:
        # Nothing to check. Either the payload shape changed or the prompt is genuinely empty;
        # both mean this hook is not doing its job, so say so rather than pass silently.
        block(f"no prompt found in the hook payload (looked for: {', '.join(PROMPT_FIELDS)}). "
              "Blocking, because a guard that cannot see the prompt cannot govern it.")

    body = json.dumps({"input": prompt}).encode()
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    # Identify ourselves. This is NOT cosmetic: api.tileward.com sits behind Cloudflare, which bans
    # urllib's default "Python-urllib/x.y" agent outright (403, Cloudflare error 1010, "banned based
    # on your browser's signature"). Without this header every check 403s, and since we fail closed
    # that means every prompt in the org is blocked, while the error blames the API key. Verified:
    # Python-urllib/3.13 -> 403, tileward-guard-hook/1.0 -> 200, same key, same body.
    req.add_header("User-Agent", "tileward-guard-hook/1.0")
    # Tie this decision to the exact prompt in Tileward's audit trail. prompt_id is unique per
    # submission; session_id repeats for every prompt in a session, and a request id that repeats
    # is one the audit report will show as a duplicate submission (request_id_seen > 1) rather than
    # as distinct checks. Fall back to session_id only if prompt_id is absent.
    rid = event.get("prompt_id") or event.get("session_id")
    if rid:
        req.add_header("X-Request-Id", f"cc-{rid}"[:128])

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Do not guess at causes. 401 is Tileward's own "invalid or revoked key". A 403 is NOT:
        # it comes from the CDN in front of the API (Cloudflare 1010 bans unknown agents), and
        # calling it a key problem sends an admin to rotate a key that was never the fault.
        detail = f"HTTP {e.code}"
        if e.code == 401:
            detail += " (key invalid or revoked)"
        elif e.code == 402:
            detail += " (insufficient balance; top up)"
        elif e.code == 403:
            detail += " (refused upstream, not by Tileward auth: check the CDN/WAF, not the key)"
        unreachable(detail)
        return
    except Exception as e:  # network, DNS, timeout, TLS
        unreachable(f"{type(e).__name__}: {e}")
        return

    result = data.get("result") or {}
    if not isinstance(result, dict) or "allowed" not in result:
        unreachable(f"unexpected guard response: {str(data)[:120]}")
        return

    if result.get("allowed"):
        allow()
    block("This prompt is off-policy for your organization's Tileward key and was not sent to the "
          "model. Contact your administrator if you believe this is wrong.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # Never exit 1 here. An unhandled crash must not become a silent allow.
        print(f"[Tileward] hook error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0 if FAIL_OPEN else 2)
