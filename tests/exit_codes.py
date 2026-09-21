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

OFFLINE ON PURPOSE. The hooks only ever talk to api.tileward.com: both replace any endpoint that is
not https on that host with it, so the sweep cannot point them anywhere else and does not try. It
stops them on this machine instead. Every subprocess gets `https_proxy` set to a closed loopback
port, urllib sends an https request through that proxy, and the connection is refused before the
request is written. So the sweep needs no network, no real API key and no policy, and it cannot be
turned green by a service that happens to be reachable. Connection refused IS the case under test:
it is what an outage looks like from here, and the correct answer to it is 2.

The exit codes cannot show any of that: a live API answering 401 and a refused connection are both
a 2. So it is checked directly. Each hook runs under an audit hook that records every name lookup
and connection and refuses any that is not the dead proxy, a case that attempts one fails, and so
does a sweep in which no case was seen reaching the proxy at all, because a check that observed
nothing would pass everything.

    python3 tests/exit_codes.py        # prints one line per case, exits 1 on any failure

Add a case whenever you add a way for either script to give up early.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from typing import Any

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "tileward-guard" / "hooks"
GUARD = HOOKS / "tileward_guard_hook.py"
PRETOOL = HOOKS / "tileward_pretooluse_hook.py"

# Refused on connect, immediately. Port 1 is conventionally unused (not
# formally IANA-reserved, but nothing binds it on any supported platform).
# If a local attacker binds it, the test gets connection refused anyway
# — the correct outcome — so the assumption is self-healing.
DEAD_HOST, DEAD_PORT = "127.0.0.1", 1
DEAD = f"http://{DEAD_HOST}:{DEAD_PORT}"
PROMPT = '{"prompt":"hi"}'
TOOL = '{"tool_name":"Bash","tool_input":{"command":"ls"}}'

BLOCK, ALLOW = 2, 0

# Environments are built from scratch rather than inherited: a TILEWARD_* variable in the shell
# that runs this file would otherwise decide the result of the case meant to test its absence.
# Proxy variables are no exception: a no_proxy in that shell cannot exempt the API host from the
# dead proxy below, because it never reaches a hook.
# The key placeholder is not a real key and does not match any known prefix pattern. No endpoint is
# set, so each hook uses its own default.
WITH_KEY = {"TILEWARD_API_KEY": "test_placeholder_not_a_real_key"}

# Added last to every environment, so that no case can replace it. https is the only scheme a hook
# will request, so it is the only proxy variable there is to set.
PROXY = {"https_proxy": DEAD}

# (name, script, stdin, env, expected exit code)
CASES = [
    ("guard: no API key",            GUARD, PROMPT, {"TILEWARD_API_KEY": ""},          BLOCK),
    ("guard: key unset entirely",    GUARD, PROMPT, {},                                BLOCK),
    ("guard: key whitespace-only",   GUARD, PROMPT, {"TILEWARD_API_KEY": "   "},       BLOCK),
    ("guard: guard unreachable",     GUARD, PROMPT, WITH_KEY,                          BLOCK),
    ("guard: unreachable + FAIL_OPEN", GUARD, PROMPT, WITH_KEY | {"TILEWARD_FAIL_OPEN": "1"}, ALLOW),
    ("guard: stdin is not JSON",     GUARD, "not json", WITH_KEY,                      BLOCK),
    ("guard: stdin is empty",        GUARD, "", WITH_KEY,                              BLOCK),
    ("guard: payload has no prompt", GUARD, "{}", WITH_KEY,                            BLOCK),
    ("guard: payload is a list",     GUARD, "[]", WITH_KEY,                            BLOCK),
    ("tool: no API key",             PRETOOL, TOOL, {"TILEWARD_API_KEY": ""},          BLOCK),
    ("tool: key whitespace-only",    PRETOOL, TOOL, {"TILEWARD_API_KEY": "   "},       BLOCK),
    ("tool: endpoint unreachable",   PRETOOL, TOOL, WITH_KEY,                          BLOCK),
    ("tool: unreachable + FAIL_OPEN", PRETOOL, TOOL, WITH_KEY | {"TILEWARD_FAIL_OPEN": "1"}, ALLOW),
    ("tool: stdin is not JSON",      PRETOOL, "not json", WITH_KEY,                    BLOCK),
    ("tool: payload has no tool_name", PRETOOL, "{}", WITH_KEY,                        BLOCK),
]

# Values that must never reach a bare float()/int() at module level. Each is swept against both
# scripts on top of the "unreachable" environment, so the expected answer is always BLOCK.
# "" and " " are the ones that actually happened; the rest are the shapes a typo takes.
JUNK = ["", " ", "5s", "abc", "-1", "0", "nan", "inf", "-inf", "infinity", "1e999", "5,0", "٥"]
for value in JUNK:
    CASES.append((f"guard: TILEWARD_TIMEOUT={value!r}", GUARD, PROMPT,
                  WITH_KEY | {"TILEWARD_TIMEOUT": value}, BLOCK))
    CASES.append((f"tool: TILEWARD_TIMEOUT={value!r}", PRETOOL, TOOL,
                  WITH_KEY | {"TILEWARD_TIMEOUT": value}, BLOCK))
    CASES.append((f"tool: TILEWARD_TOOL_INPUT_MAX={value!r}", PRETOOL, TOOL,
                  WITH_KEY | {"TILEWARD_TOOL_INPUT_MAX": value}, BLOCK))


# What a hook runs inside. Python raises an audit event for every name lookup and every connection
# made anywhere in the process, whichever library makes it, so nothing a hook imports can route
# around this. Anything but the dead proxy is refused on the spot, so a lapse in the proxy setting
# fails the case instead of sending the placeholder key somewhere, and is reported on stderr,
# because the exit code cannot say. To the hook a refusal here is an ordinary refused connection.
WATCH = """
import json, runpy, sys
host, port, script = sys.argv[1], int(sys.argv[2]), sys.argv[3]
seen = {"proxied": 0, "refused": []}
def audit(event, args):
    if event in ("socket.getaddrinfo", "socket.gethostbyname"):
        target, ok = "lookup %s" % (args[0],), args[0] == host
    elif event == "socket.connect":
        target, ok = "connect %s" % (args[1],), args[1] == (host, port)
        seen["proxied"] += ok
    else:
        return
    if not ok:
        seen["refused"].append(target)
        raise ConnectionRefusedError(target)
sys.addaudithook(audit)
try:
    runpy.run_path(script, run_name="__main__")
finally:
    print("NETWORK " + json.dumps(seen), file=sys.stderr)
"""
REPORT = re.compile(r"^NETWORK (\{.*\})\n?", re.MULTILINE)


def run(script: pathlib.Path, stdin: str,
        env: dict[str, str]) -> tuple[int, str, dict[str, Any] | None]:
    """Run *script* with *stdin* and *env*, return (returncode, stderr, network report).

    The report is what WATCH saw the process attempt: `proxied` counts connections to the dead
    proxy and `refused` lists everything else it tried to reach. It is None when WATCH never got to
    report, which the caller counts as a failure.
    """
    # PATH is kept so `#!/usr/bin/env python3` resolves; nothing else is
    # inherited.  PYTHONPATH is cleared to prevent package shadowing on the
    # host machine from affecting the subprocess.
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": ""} | env | PROXY
    try:
        # The working directory is the hooks directory because sys.path starts with the script's
        # own directory when a script is run directly, and with the working directory under `-c`.
        result = subprocess.run(
            [sys.executable, "-c", WATCH, DEAD_HOST, str(DEAD_PORT), str(script)],
            input=stdin, text=True, capture_output=True,
            env=env, cwd=HOOKS, timeout=30,
        )
        report = REPORT.search(result.stderr)
        network = json.loads(report.group(1)) if report else None
        return result.returncode, REPORT.sub("", result.stderr), network
    except subprocess.TimeoutExpired:
        # A killed process (SIGKILL / SIGTERM) does NOT exit 1 — it exits -9
        # or -15, which is NOT in (0, 2) and is caught by the silent_allow
        # check below.  Return -1 explicitly and an empty stderr stub.
        return -1, "", None


def main() -> int:
    failures = 0
    proxied = 0
    for name, script, stdin, env, expected in CASES:
        code, stderr, network = run(script, stdin, env)
        # Two assertions on the exit code, and the second is the one that matters. A case may
        # legitimately change which of 0 or 2 it returns as behaviour evolves; exit 1 is never
        # legitimate, from any path, in either script.
        ok = code == expected
        silent_allow = code not in (ALLOW, BLOCK)
        # And a third, about where the request went rather than how the hook answered it. The exit
        # codes cannot see that: a live API answering 401 and a refused connection are both a 2.
        if network is None:
            strays = ["no network report"]
        else:
            strays = network["refused"]
            proxied += network["proxied"]
        if not ok or silent_allow or strays:
            failures += 1
            if not ok or silent_allow:
                note = "  <-- EXIT 1 IS A SILENT ALLOW" if code == 1 else ""
                print(f"FAIL  {name}: expected {expected}, got {code}{note}")
            if strays:
                print(f"FAIL  {name}: reached past the dead proxy: {', '.join(strays)}")
            if stderr:
                print(f"        stderr: {stderr.strip()[:200]}")
        else:
            print(f"ok    {name}: {code}")
    # An audit that observed nothing would pass every case above. Several cases get as far as making
    # a request, so if none was seen reaching the proxy, either the audit is not looking or the
    # requests are not going through it.
    blind = proxied == 0
    if blind:
        print("FAIL  network audit: no case was seen reaching the dead proxy; either the audit is "
              "not looking or the requests are not going through it")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures or blind else 0


if __name__ == "__main__":
    raise SystemExit(main())
