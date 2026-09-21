#!/usr/bin/env python3
"""Sweep every failure path in both hooks and assert none of them exits 1.

WHY THIS FILE EXISTS. Claude Code treats exit 2 as "blocked" and every other non-zero code as a
non-blocking error: it logs the failure and runs the prompt anyway. So for a gate, exit 1 is not
a loud failure, it is a silent allow -- the gate stops gating while remaining installed, enabled
and apparently healthy. Nothing in the client surfaces that. The only defence is this sweep.

It is not hypothetical. Until 2026-08-27 both scripts parsed TILEWARD_TIMEOUT with a bare float()
at module level, outside the try/except around main(), and `TILEWARD_TIMEOUT=` -- set, but empty --
raised ValueError and exited 1. An empty value reaches here the ordinary way: an `export` with
nothing after it, an empty entry in a managed-settings env block, a CI variable declared and never
given a value.

The endpoint variables carried the same flaw. Both scripts check TILEWARD_API and TILEWARD_TOOL_API
with urlparse at import time, and a malformed bracketed host such as `https://[` made urlparse raise
ValueError and the script exit 1.

HERMETIC ON PURPOSE. Every case that gets far enough to make a request points at a closed loopback
port, so the sweep needs no network, no API key and no policy, and it cannot be turned green by a
service that happens to be reachable. Connection refused IS the case under test: it is what an
outage looks like from here, and the correct answer to it is 2.

    python3 tests/exit_codes.py        # prints one line per case, exits 1 on any failure

Add a case whenever you add a way for either script to give up early.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "tileward-guard" / "hooks"
GUARD = HOOKS / "tileward_guard_hook.py"
PRETOOL = HOOKS / "tileward_pretooluse_hook.py"

# Refused on connect, immediately. Port 1 is conventionally unused (not
# formally IANA-reserved, but nothing binds it on any supported platform).
# If a local attacker binds it, the test gets connection refused anyway
# — the correct outcome — so the assumption is self-healing.
DEAD = "http://127.0.0.1:1"
PROMPT = '{"prompt":"hi"}'
TOOL = '{"tool_name":"Bash","tool_input":{"command":"ls"}}'

BLOCK, ALLOW = 2, 0

# Environments are built from scratch rather than inherited: a TILEWARD_* variable in the shell
# that runs this file would otherwise decide the result of the case meant to test its absence.
# The key placeholder is not a real key and does not match any known prefix pattern.
GUARD_ENV = {"TILEWARD_API_KEY": "test_placeholder_not_a_real_key", "TILEWARD_API": DEAD + "/v1/guard"}
TOOL_ENV = {"TILEWARD_API_KEY": "test_placeholder_not_a_real_key", "TILEWARD_TOOL_API": DEAD + "/v1/guard/tool"}

# (name, script, stdin, env, expected exit code)
CASES = [
    ("guard: no API key",            GUARD, PROMPT, {"TILEWARD_API_KEY": ""},          BLOCK),
    ("guard: key unset entirely",    GUARD, PROMPT, {},                                BLOCK),
    ("guard: key whitespace-only",   GUARD, PROMPT, {"TILEWARD_API_KEY": "   "},       BLOCK),
    ("guard: guard unreachable",     GUARD, PROMPT, GUARD_ENV,                         BLOCK),
    ("guard: unreachable + FAIL_OPEN", GUARD, PROMPT, GUARD_ENV | {"TILEWARD_FAIL_OPEN": "1"}, ALLOW),
    ("guard: stdin is not JSON",     GUARD, "not json", GUARD_ENV,                     BLOCK),
    ("guard: stdin is empty",        GUARD, "", GUARD_ENV,                             BLOCK),
    ("guard: payload has no prompt", GUARD, "{}", GUARD_ENV,                           BLOCK),
    ("guard: payload is a list",     GUARD, "[]", GUARD_ENV,                           BLOCK),
    ("tool: no API key",             PRETOOL, TOOL, {"TILEWARD_API_KEY": ""},          BLOCK),
    ("tool: key whitespace-only",    PRETOOL, TOOL, {"TILEWARD_API_KEY": "   "},       BLOCK),
    ("tool: endpoint unreachable",   PRETOOL, TOOL, TOOL_ENV,                          BLOCK),
    ("tool: unreachable + FAIL_OPEN", PRETOOL, TOOL, TOOL_ENV | {"TILEWARD_FAIL_OPEN": "1"}, ALLOW),
    ("tool: stdin is not JSON",      PRETOOL, "not json", TOOL_ENV,                    BLOCK),
    ("tool: payload has no tool_name", PRETOOL, "{}", TOOL_ENV,                        BLOCK),
]

# Values that must never reach a bare float()/int() at module level. Each is swept against both
# scripts on top of the "unreachable" environment, so the expected answer is always BLOCK.
# "" and " " are the ones that actually happened; the rest are the shapes a typo takes.
JUNK = ["", " ", "5s", "abc", "-1", "0", "nan", "inf", "-inf", "infinity", "1e999", "5,0", "٥"]
for value in JUNK:
    CASES.append((f"guard: TILEWARD_TIMEOUT={value!r}", GUARD, PROMPT,
                  GUARD_ENV | {"TILEWARD_TIMEOUT": value}, BLOCK))
    CASES.append((f"tool: TILEWARD_TIMEOUT={value!r}", PRETOOL, TOOL,
                  TOOL_ENV | {"TILEWARD_TIMEOUT": value}, BLOCK))
    CASES.append((f"tool: TILEWARD_TOOL_INPUT_MAX={value!r}", PRETOOL, TOOL,
                  TOOL_ENV | {"TILEWARD_TOOL_INPUT_MAX": value}, BLOCK))

# The endpoint variables get the same treatment. Each script runs its endpoint through urlparse at
# import time, outside the try/except around main(), and urlparse raises ValueError on a malformed
# bracketed host. The first four values below do that. The last three parse without complaint and
# are here so the line between the two cannot move unnoticed: the first of them is one `.port` read
# away from raising. A script that cannot use its endpoint falls back to its default, where the
# sweep gets no decision, so the answer to every one of them is BLOCK.
ENDPOINTS = ["https://[", "http://[", "https://[::1", "https://[foo]/",
             "https://api.tileward.com:99999/", "not a url", ""]
for endpoint in ENDPOINTS:
    CASES.append((f"guard: TILEWARD_API={endpoint!r}", GUARD, PROMPT,
                  GUARD_ENV | {"TILEWARD_API": endpoint}, BLOCK))
    CASES.append((f"tool: TILEWARD_TOOL_API={endpoint!r}", PRETOOL, TOOL,
                  TOOL_ENV | {"TILEWARD_TOOL_API": endpoint}, BLOCK))


def run(script: pathlib.Path, stdin: str, env: dict[str, str]) -> tuple[int, str]:
    """Run *script* with *stdin* and *env*, return (returncode, stderr)."""
    # PATH is kept so `#!/usr/bin/env python3` resolves; nothing else is
    # inherited.  PYTHONPATH is cleared to prevent package shadowing on the
    # host machine from affecting the subprocess.
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": ""} | env
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            input=stdin, text=True, capture_output=True,
            env=env, timeout=30,
        )
        return result.returncode, result.stderr
    except subprocess.TimeoutExpired:
        # A killed process (SIGKILL / SIGTERM) does NOT exit 1 — it exits -9
        # or -15, which is NOT in (0, 2) and is caught by the silent_allow
        # check below.  Return -1 explicitly and an empty stderr stub.
        return -1, ""


def main() -> int:
    failures = 0
    for name, script, stdin, env, expected in CASES:
        code, stderr = run(script, stdin, env)
        # Two assertions, and the second is the one that matters. A case may legitimately change
        # which of 0 or 2 it returns as behaviour evolves; exit 1 is never legitimate, from any
        # path, in either script.
        ok = code == expected
        silent_allow = code not in (ALLOW, BLOCK)
        if not ok or silent_allow:
            failures += 1
            note = "  <-- EXIT 1 IS A SILENT ALLOW" if code == 1 else ""
            print(f"FAIL  {name}: expected {expected}, got {code}{note}")
            if stderr:
                print(f"        stderr: {stderr.strip()[:200]}")
        else:
            print(f"ok    {name}: {code}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
