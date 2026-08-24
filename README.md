# Tileward guard as a Claude Code hook

Block off-policy prompts in Claude Code **before the model sees them**, using the policy bound to a
Tileward API key. The decision is made out of band, the model is never asked to police itself, and a
refused prompt costs zero generation tokens because nothing is generated.

```
you type a prompt
   -> Claude Code runs this hook first (UserPromptSubmit)
      -> POST /v1/guard  {"input": "<your prompt>"}   (the key's own policy decides)
         -> allowed  : exit 0, the prompt goes to the model
         -> refused  : exit 2, the prompt NEVER reaches the model, you see why
   -> every decision lands in your Tileward audit trail, with the tile that refused it
```

## Why this is a hook and not an MCP server

An MCP server exposes tools the **model chooses** to call and the **user can remove**. It cannot
intercept a prompt: the MCP spec has no interceptor or gate primitive, and no way to make a tool
call mandatory. A guard shipped as an MCP tool is a conscience, not a control, and it is exactly the
"ask a model to judge the request" pattern Tileward exists to replace.

A hook is different. Claude Code runs it before the model, reads its exit code, and an
administrator can install it so the user cannot remove it. That is a real gate.

## Which path you can actually use today

**This repository is private.** Claude Code clones a marketplace with the machine's own git
credentials, so every `marketplace add` and `extraKnownMarketplaces` line below works only where
those credentials can read this repo — inside Tileward, and nowhere else. A customer's fleet fails
at the clone, before any of the settings below get a chance to matter.

| | Works while this repo is private | Fails when Tileward is unreachable |
| --- | --- | --- |
| **A** — `type: "http"` at `/v1/guard/hook` | **yes, for anyone** | **prompt runs** (Claude Code fails open on an unreachable HTTP hook) |
| **B** — this plugin | Tileward machines only | blocked |
| **B-manual** — the copied script | yes, but you deploy and monitor the file | blocked |

So there is no path that is both externally installable *and* fail-closed on an outage. That is the
whole cost of the repo being private, and it is not a security cost: nothing here is secret. The
policy lives server-side on the API key and this is a thin client. Making the repository public is
what removes the trade-off, and it is the recommendation
([#226](https://github.com/ananthasharma/tileward.com/issues/226)).

**Until then:** if you are outside Tileward, use **option A** and accept that an outage lets prompts
through. If a prompt must never run unchecked, use **B-manual** — copy
`plugins/tileward-guard/hooks/tileward_guard_hook.py` out of this repo and wire it by absolute path.
That needs no marketplace and no clone at runtime; you own the deployment. For air-gapped or CI
fleets, pre-populate `CLAUDE_CODE_PLUGIN_SEED_DIR` and nothing is cloned at runtime either.

## Install (one developer)

This repository is also a Claude Code plugin marketplace, so there is nothing to copy:

```bash
claude plugin marketplace add Tileward-com/tileward-claude-plugin
claude plugin install tileward-guard@tileward
export TILEWARD_API_KEY=tw_live_...   # a key with a policy bound to it, see below
```

That is a guardrail you chose and can `/plugin uninstall` at any time. It is not a control over
anyone, including yourself. To govern somebody else, read the next section: **a plugin the governed
party can uninstall is a suggestion.**

> **This clone needs credentials for a private repo.** It succeeds inside Tileward and fails
> everywhere else — see [Which path you can actually use
> today](#which-path-you-can-actually-use-today).

## Install (an organization, enforced)

Managed settings, which a user cannot override: macOS
`/Library/Application Support/ClaudeCode/managed-settings.json`, Linux
`/etc/claude-code/managed-settings.json`, Windows `C:\Program Files\ClaudeCode\managed-settings.json`,
or pushed from the claude.ai admin console. Settings precedence puts managed at the top, above
command-line arguments, local, project, and user settings.

> **This block needs the repository to be readable by every governed machine.** It is private
> today, so on a customer fleet the marketplace clone fails and the plugin never loads — and because
> a missing hook is a hook that does not run, the result is an organization that looks governed and
> is not. Verify on one machine before rolling it out. See [Which path you can actually use
> today](#which-path-you-can-actually-use-today).

```json
{
  "extraKnownMarketplaces": {
    "tileward": { "source": { "source": "github", "repo": "Tileward-com/tileward-claude-plugin" } }
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
rather wire it up yourself with a `type: "command"` hook pointing at an absolute path.

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

### Option B: the plugin (recommended)

The `enabledPlugins` block shown above. The plugin carries the local script and registers it as a
`UserPromptSubmit` hook, so this is option A's "nothing to deploy" with a local process that can
still refuse when we are unreachable.

If you would rather not use the plugin, the same script works wired by hand:

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

That is the variant whose file you must deploy and monitor yourself.

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

**On availability, not merit:** B is the recommendation and is currently reachable only from
machines that can read this private repository. If that is not you, the honest ordering is A for
least friction, B-manual when the outage behaviour is unacceptable, and B once the repo is public.

## The policy lives on the key

The hook sends only the prompt. It does not send topics, and it never sends your policy to a model.
Bind the policy to the key in the console (Settings, then API keys, then Governance), and the same
key governs Claude Code, your apps, and anything else that calls `/v1/guard`. Change the policy in
one place and every surface follows.

**A key with no bound policy blocks nothing.** The hook will happily allow everything and look
installed. Bind a policy first.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TILEWARD_API_KEY` | required | The key whose bound policy decides. Unset means every prompt is blocked. |
| `TILEWARD_API` | `https://api.tileward.com/v1/guard` | Endpoint. |
| `TILEWARD_TIMEOUT` | `5` | Seconds. Keep it well under the hook's own `timeout`. |
| `TILEWARD_FAIL_OPEN` | `0` | `1` allows prompts when the guard cannot be reached. |
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

| Situation | Result |
| --- | --- |
| Prompt is off-policy | blocked, with the reason |
| `TILEWARD_API_KEY` unset | blocked |
| Key invalid or revoked (401) | blocked |
| No balance (402) | blocked |
| Guard unreachable, DNS, TLS, timeout | blocked |
| Payload unparseable, or has no prompt | blocked |
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
logs it, and runs the prompt anyway. This script never exits 1, on any path. If you fork it, keep
that property, or it will quietly stop gating while still looking installed.

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
