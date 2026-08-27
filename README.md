# Tileward for Claude Code

This repository is a Claude Code plugin marketplace. It ships two plugins, which do different jobs
and are installed independently:

| Plugin | What it does | Shape |
| --- | --- | --- |
| [`tileward-guard`](plugins/tileward-guard) | Refuse off-policy prompts before the model sees them | one hook (tool-call governance is [not available yet](#tool-call-governance-is-not-available-yet)) |
| [`tileward-context`](plugins/tileward-context) | Recall the relevant slice of a long conversation instead of resending the transcript | hosted MCP server |

Both read the same `TILEWARD_API_KEY`. The rest of this document is about **tileward-guard**;
`tileward-context` has its own [README](plugins/tileward-context/README.md).

## Tileward guard as a Claude Code hook

Block off-policy prompts in Claude Code **before the model sees them**, using the policy bound to a
Tileward API key. The decision is made out of band, the model is never asked to police itself, and a
refused prompt costs zero generation tokens because nothing is generated.

One hook ships today, and it governs the **ask**.

```
you type a prompt
   -> Claude Code runs this hook first (UserPromptSubmit)
      -> POST /v1/guard  {"input": "<your prompt>"}   (the key's GOVERNANCE policy decides)
         -> allowed  : exit 0, the prompt goes to the model
         -> refused  : exit 2, the prompt NEVER reaches the model, you see why

   -> every decision lands in your Tileward audit trail
```

## Tool-call governance is not available yet

Governing the ask alone is not enough — a permitted prompt can still end in `rm -rf` — so the plan
is a second hook on `PreToolUse`, reading a separate **execution** policy bound to the same key.
**That half is not built.** Measured against production on 2026-08-25:

```
POST /v1/guard       -> 422   (exists)
POST /v1/guard/hook  -> 200   (exists — the prompt hook is real and working)
POST /v1/guard/tool  -> 404
```

There is no execution policy kind on a key either (`kind` is `governance | context`), and no
Execution page in the console to edit one. Tracked as
#189 (the policy kind),
#190 (the endpoint and hook) and
#257.

**This plugin therefore does not register a `PreToolUse` hook.** The client script
(`plugins/tileward-guard/hooks/tileward_pretooluse_hook.py`) stays in the repo for when the server
side lands, but nothing wires it up and **you should not wire it up by hand yet**: pointed at a 404,
the fail-closed default every hook here uses refuses *every* tool call with "execution policy
unreachable (HTTP 404)". The only way to stop it blocking is `TILEWARD_FAIL_OPEN=1`, which allows
everything and governs nothing. There is no setting in between, because there is nothing on the
other end.

The request shape is settled: `{tool_name, tool_input, tool_input_fidelity}`. `tool_input` carries
the real payload — a Bash command line, an Edit's contents — because that is what separates
`Bash(rm -rf /)` from `Bash(ls)`. Gating a *command* rather than a *capability* therefore means that
payload leaves the machine, and it lands in your audit trail beside the decision. That is a real
cost and it is the price of the feature.

`tool_input_fidelity` is on every request and is always one of `full`, `truncated` or `omitted`, so
a policy never has to infer from an absent field whether it saw the whole command. Long strings are
cut at `TILEWARD_TOOL_INPUT_MAX` characters (default 4096); a body still oversize after that drops
the input rather than sending a mangled half, and says so. `TILEWARD_TOOL_INPUT_MAX=0` sends the
tool name and nothing else — a capability gate, "may this key run Bash at all", and never a command
gate — for anyone unwilling to send source code. No fork, no code change.

Everything below describes the prompt hook, which is live and working.

## Why this is a hook and not an MCP server

An MCP server exposes tools the **model chooses** to call and the **user can remove**. It cannot
intercept a prompt: the MCP spec has no interceptor or gate primitive, and no way to make a tool
call mandatory. A guard shipped as an MCP tool is a conscience, not a control, and it is exactly the
"ask a model to judge the request" pattern Tileward exists to replace.

That argument is about the **guard**, not about MCP. The other plugin here,
[`tileward-context`](plugins/tileward-context), *is* an MCP server on purpose: recall is a
capability the model should reach for when it helps, and a user who removes it has slowed their
own session down rather than escaped a policy. Tools are the right shape for that, and the wrong
shape for a gate.

A hook is different. Claude Code runs it before the model, reads its exit code, and an
administrator can install it so the user cannot remove it. That is a real gate.

## Which path to use

**Install the plugin from this marketplace.** It is the only shape that is both delivered and
updated by the platform *and* fail-closed when Tileward is unreachable. Two other paths exist, and
each gives something up.

| | What you deploy | When Tileward is unreachable |
| --- | --- | --- |
| **A** — `type: "http"` at `/v1/guard/hook` | nothing; a few lines of settings | **the prompt runs** (Claude Code fails open on an unreachable HTTP hook) |
| **B** — this plugin ← **the recommendation** | nothing; the marketplace delivers and updates it | blocked |
| **B-manual** — the copied scripts | the files, on every machine, maintained by you | blocked |

The second column is the whole decision. Claude Code fails open when it cannot reach an HTTP hook,
and no response can change that, because nothing is there to answer; a local process can refuse on
its own. So A is the fewest moving parts *and* the one that lets prompts through during an outage.
If a prompt must never run unchecked, A is not for you. The full matrix is in [Fail closed, and the
exact limit of that promise](#fail-closed-and-the-exact-limit-of-that-promise).

B-manual is this plugin's own script, copied out and wired by absolute path: fail-closed, no
marketplace, no clone at runtime, and the deployment and its updates are yours to own. For
air-gapped or CI fleets, pre-populate `CLAUDE_CODE_PLUGIN_SEED_DIR` and nothing is cloned at
runtime either.

## Install (one developer)

This repository is also a Claude Code plugin marketplace, so there is nothing to copy:

```bash
claude plugin marketplace add Tileward-com/tileward-claude-marketplace
claude plugin install tileward-guard@tileward
export TILEWARD_API_KEY=tw_live_...   # a key with a policy bound to it, see below
```

That is a guardrail you chose and can `/plugin uninstall` at any time. It is not a control over
anyone, including yourself. To govern somebody else, read the next section: **a plugin the governed
party can uninstall is a suggestion.**

## Install (an organization, enforced)

Managed settings, which a user cannot override: macOS
`/Library/Application Support/ClaudeCode/managed-settings.json`, Linux
`/etc/claude-code/managed-settings.json`, Windows `C:\Program Files\ClaudeCode\managed-settings.json`,
or pushed from the claude.ai admin console. Settings precedence puts managed at the top, above
command-line arguments, local, project, and user settings.

> **Verify on one machine before rolling this out to a fleet.** A marketplace that cannot be
> cloned means the plugin never loads, and a hook that does not load is a hook that does not run —
> an organization that looks governed and is not. The clone is plain `git` against a public
> repository, so the usual causes are a proxy, an egress rule, or a runner with no network.

```json
{
  "extraKnownMarketplaces": {
    "tileward": { "source": { "source": "github", "repo": "Tileward-com/tileward-claude-marketplace" } }
  },
  "enabledPlugins": { "tileward-guard@tileward": true },
  "allowManagedHooksOnly": true,
  "strictKnownMarketplaces": true,
  "disableSideloadFlags": true
}
```

Each line closes a specific door, and dropping any one of them turns enforcement back into a
suggestion:

- **`enabledPlugins`** force-enables the plugin. Enabled from managed settings, it cannot be
  uninstalled or disabled by the user.
- **`allowManagedHooksOnly`** loads *only* managed hooks, SDK hooks, and hooks from plugins
  force-enabled in managed `enabledPlugins`. Every user, project, and other-plugin hook is blocked.
  This is what stops a developer from unhooking themselves, and it is also why the plugin has to be
  force-enabled here rather than merely installed.
- **`strictKnownMarketplaces`** stops users adding their own marketplaces.
- **`disableSideloadFlags`** rejects `--plugin-dir`, `--plugin-url`, `--agents`, and `--mcp-config`,
  which would otherwise bypass the previous line for a single run.

For container and CI images, pre-populate `CLAUDE_CODE_PLUGIN_SEED_DIR` so nothing is cloned at
runtime.

### Why the plugin rather than a copied script

An earlier version of this guide told administrators to deploy `tileward_guard_hook.py` to every
machine and monitor that it still existed, because Claude Code fails open when a hook's command is
missing: delete the file and prompts flow. The plugin makes delivery, versioning, and updates the
platform's problem. You ship a marketplace commit instead of re-copying a fleet.

The script still lives in this repo, at
`plugins/tileward-guard/hooks/tileward_guard_hook.py`, and still runs standalone if you would
rather wire it up yourself with a `type: "command"` hook pointing at an absolute path. Its
`PreToolUse` sibling sits in the same directory with no endpoint to call — see [Tool-call
governance is not available yet](#tool-call-governance-is-not-available-yet).

### Option A: no script anywhere (`type: "http"`)

Point Claude Code straight at `/v1/guard/hook`, which speaks the hook's own contract. Nothing to
install, nothing to keep updated, nothing that can be deleted from a laptop.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "https://api.tileward.com/v1/guard/hook",
            "headers": { "Authorization": "Bearer $TILEWARD_API_KEY" },
            "allowedEnvVars": ["TILEWARD_API_KEY"],
            "timeout": 10
          }
        ]
      }
    ]
  },
  "allowManagedHooksOnly": true
}
```

Do NOT point this at `/v1/guard`: that endpoint answers `{"result": {"allowed": false}}`, and Claude
Code blocks only on a body saying `{"decision": "block"}`, so it would allow every prompt while
looking installed. `/v1/guard/hook` exists precisely for this.

There is no equivalent block for tool calls. `/v1/guard/tool` does not exist, so a `PreToolUse`
`type: "http"` hook pointed at it gets a 404, which Claude Code reads as a non-blocking error and
runs the tool anyway — every tool call, unexamined, while the fleet looks governed. See [Tool-call
governance is not available yet](#tool-call-governance-is-not-available-yet).

### Option B: the plugin (recommended)

The `enabledPlugins` block shown above. The plugin carries the prompt script and registers it as a
`UserPromptSubmit` hook, so this is option A's "nothing to deploy" with a local process that can
still refuse when we are unreachable. It registers no `PreToolUse` hook — see [Tool-call governance
is not available yet](#tool-call-governance-is-not-available-yet).

If you would rather not use the plugin, the same scripts work wired by hand:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "/opt/tileward/tileward_guard_hook.py", "timeout": 15 }
        ]
      }
    ]
  },
  "allowManagedHooksOnly": true
}
```

That is the variant whose files you must deploy and monitor yourself.

### Which one

The difference is what happens when Tileward cannot be reached at all.

| | A: HTTP endpoint | B: plugin | B-manual: copied script |
| --- | --- | --- | --- |
| Anything to deploy | nothing | nothing | the script, on every machine |
| Off-policy prompt | blocked | blocked | blocked |
| Bad or revoked key | blocked | blocked | blocked |
| Empty balance | blocked | blocked | blocked |
| **Tileward unreachable** (network, DNS, our outage) | **prompt runs** | **blocked** | **blocked** |
| Script deleted or made non-executable | not applicable | plugin cache is managed | prompt runs |

Claude Code fails open when it cannot reach an HTTP hook, and no response can change that, because
nothing is there to answer. A local process can refuse on its own. The plugin gets you that without
a deployment to babysit, which is why it is the recommendation. Option A is still the least moving
parts if you would rather accept the outage behaviour than run any local code.

## The policy lives on the key

The prompt hook sends only the prompt. It does not send topics and does not send your policy to a
model. Bind the policy to the key in the console and the same key governs Claude Code, your apps,
and anything else calling Tileward. Change the policy in one place and every surface follows.

| Hook | Console page | Config | Decides |
| --- | --- | --- | --- |
| `UserPromptSubmit` | Governance | topics (`{mode, allow, refuse}`) | whether a prompt reaches the model |

**A key with no bound policy blocks nothing.** It installs cleanly, answers instantly and governs
nothing, so bind a governance policy before you rely on it.

A second row belongs in that table — `PreToolUse`, an execution policy, tool calls — and does not
exist yet: no execution policy kind on a key, no Execution page, no endpoint. Until it lands, a
governed key governs what you ask and permits every tool the agent then reaches for. That is the
gap, stated plainly rather than papered over; see [Tool-call governance is not available
yet](#tool-call-governance-is-not-available-yet).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TILEWARD_API_KEY` | required | The key whose bound policy decides. Unset means every prompt is blocked. |
| `TILEWARD_API` | `https://api.tileward.com/v1/guard` | Prompt endpoint, used by the UserPromptSubmit hook. |
| `TILEWARD_TIMEOUT` | `5` | Seconds, greater than 0 and at most 14. Keep it well under the hook's own `timeout`. Empty, unparseable or out of range falls back to the default rather than raising. |
| `TILEWARD_FAIL_OPEN` | `0` | `1` allows a prompt when Tileward cannot be reached. |
| `TILEWARD_ACTOR` | the OS username | Who to attribute this machine's checks to in the audit report. `-` sends nothing. Read the section below before relying on it. |

## Who was refused: `X-Tileward-Actor`

A shared key tells you an organization was refused. It does not tell you who. The hook sends
`X-Tileward-Actor` (the OS username by default, `TILEWARD_ACTOR` to override), and it lands in its
own column in the audit report and the export.

It is a **claim, not an attestation**, and that governs what you may do with it. The hook runs on the
governed person's machine, under their environment, so anyone who can set `TILEWARD_ACTOR` can type
a colleague's name into it. It is an attribution aid for whoever reads the report. It is not
evidence, and it should not be the basis of a conversation with an employee.

**If you need identity that holds, issue one key per person.** Only an admin can mint a key, the
audit already records `key_id` on every row, and revoking one person costs one click. That works
today with no header at all. The actor header earns its place when one key legitimately serves many
people, such as a shared gateway.

**Prefer an internal id to an email.** The audit is exportable, and `u-8842` identifies a person
against your own directory just as well as their address does, without turning the report into a
file of personal data.

It is deliberately a **separate header from `X-Request-Id`**, which the hook already sends. A request
id identifies one submission; the audit counts rows sharing one so a re-submitted id shows up as
`×N`. A person recurs on every prompt they ever send, so putting them in that field would badge
their entire history as duplicate submissions and destroy the duplicate check to gain identity.
Two questions, two headers.

For option A (`type: "http"`), pass it the same way as the key:

```json
"headers": {
  "Authorization": "Bearer $TILEWARD_API_KEY",
  "X-Tileward-Actor": "$TILEWARD_ACTOR"
},
"allowedEnvVars": ["TILEWARD_API_KEY", "TILEWARD_ACTOR"]
```

## Fail closed, and the exact limit of that promise

This hook fails **closed**: if it cannot get an answer, it blocks. A control that allows everything
whenever its backend is unreachable is not a control, it is a log.

Verified against the live guard, every one of these exits 2 and blocks:

| Situation | Prompt hook |
| --- | --- |
| Off-policy prompt | blocked, with the reason |
| `TILEWARD_API_KEY` unset | blocked |
| Key invalid or revoked (401) | blocked |
| No balance (402) | blocked |
| Tileward unreachable, DNS, TLS, timeout | blocked |
| Payload unparseable | blocked |
| Payload has no prompt | blocked |
| Unexpected response shape | blocked |
| Unhandled crash | blocked |

**What it cannot cover.** Claude Code itself fails **open** when a hook times out or its command is
missing: the prompt is sent, the error is only logged, and the hook spec has no fail-closed flag to
change that. So:

- **Keep `TILEWARD_TIMEOUT` well below the hook's `timeout`.** At 5s against a 15s hook timeout this
  script always answers first, so the timeout that fires is ours and we block on it. Invert them and
  Claude Code kills the script and allows the prompt: the gate silently becomes a no-op.
- **Monitor that the file exists and is executable.** If it is deleted, prompts flow. Deploy it
  through the same managed channel as the settings, and alert on its absence.
- The guard answers in roughly 200ms, so timeouts should be rare, but design for the day they are not.

`exit 1 does not block.` Claude Code treats any non-zero code other than 2 as a non-blocking error,
logs it, and runs the prompt anyway. For a gate that makes exit 1 a *silent allow*, not a loud
failure: it stops gating while remaining installed, enabled and apparently healthy.

`tests/exit_codes.py` sweeps every failure path in both scripts and asserts none of them lands on
1. It needs no network, no key and no policy -- every case that gets far enough to make a request
points at a closed loopback port, because connection refused is what an outage looks like from
here and the correct answer to it is 2.

```bash
python3 tests/exit_codes.py
```

Run it after any change to either script, and if you fork one, keep the sweep with it. This is not
a hypothetical property: before the first release both scripts parsed `TILEWARD_TIMEOUT` with a bare
`float()` at module level, outside the `try/except` around `main()`, and setting that variable to
an empty string exited 1. An empty value arrives the ordinary way -- an `export` with nothing after
it, an empty entry in a managed-settings env block, a CI variable declared and never given one.

## Two traps worth knowing

**Do not point a `type: "http"` hook straight at `/v1/guard`.** Claude Code blocks only on a 2xx
response whose body says `{"decision": "block"}`. `/v1/guard` answers
`{"cost_micros": N, "result": {"allowed": false}}`, so Claude Code would read 2xx, find no
`decision` field, and allow every prompt while looking installed. Non-2xx fails open as well. Use
`/v1/guard/hook` (option A above), which speaks the hook's contract, or this script.

**`api.tileward.com` is behind Cloudflare, which bans urllib's default agent** (403, Cloudflare error
1010, "banned based on your browser's signature"). This script sends its own `User-Agent` for that
reason. Strip it and every check 403s, which, because we fail closed, blocks every prompt in the
organization while the error looks like an authentication problem. Verified: `Python-urllib/3.13`
gets 403, `tileward-guard-hook/1.0` gets 200, same key, same body.

## What lands in the audit trail

Each check writes one row, keyed by `X-Request-Id: cc-<prompt_id>`, so a refusal here is findable
next to the prompt that caused it:

```
cc-e7e2ce9d-b1c3-4e4e-8c37-e3feab939c52  client=True  actor=u-8842  rejected  reason=guard:options_pricing  tok=14  cost=140
cc-35bd27ff-2b2f-4e2f-be60-3ce7fc13701a  client=True  actor=u-8842  allowed                                 tok=9   cost=90
```

`prompt_id` is a UUID unique to one submission, and it requires **Claude Code 2.1.196 or later**. On
an older version the field is absent, the hook sends no request id, and Tileward mints its own
`req_...` per check. Those rows are still complete and still per-check; you just cannot trace one
back to a specific prompt. The hook deliberately does not fall back to `session_id`, which is shared
by every prompt in a session and would make the report show a whole session as one request
re-submitted `×N`.

The response the hook reads is only the decision and the cost. The tile that refused a prompt is
recorded in the audit trail, not returned to the caller. Metering is per token read, so a check is
about 14 tokens, roughly $0.00014. Read the report in the console under Settings, then Audit, or
export it as CSV or JSON.

## Test it before you trust it

```bash
export TILEWARD_API_KEY=tw_live_...
H=plugins/tileward-guard/hooks/tileward_guard_hook.py

echo '{"hook_event_name":"UserPromptSubmit","prompt_id":"p1","prompt":"hello"}' \
  | $H; echo "exit=$?"      # expect 0

echo '{"hook_event_name":"UserPromptSubmit","prompt_id":"p2","prompt":"<something your policy blocks>"}' \
  | $H; echo "exit=$?"      # expect 2
```

Claude Code does nothing more than this: it pipes that JSON to the script and reads the exit code.
If those two cases behave, the hook behaves.

Those two need a key, a policy and the network. The sweep in `tests/exit_codes.py` needs none of
them — it covers the other direction, every way the hook can give up early, and asserts that none
of them lands on the exit code that would let the prompt through:

```bash
python3 tests/exit_codes.py
```

`.github/workflows/checks.yml` runs it on every push and pull request, against Python 3.9, 3.11 and
3.13, because `#!/usr/bin/env python3` means the interpreter is whatever the governed machine
happens to have.

---

Bare `#NNN` references point at issues in Tileward's internal tracker, which is not public.
They are kept so the reasoning here stays traceable for us; you are not missing a link.
