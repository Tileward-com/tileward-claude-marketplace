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

## Install (one developer)

```bash
export TILEWARD_API_KEY=tw_live_...   # a key with a policy bound to it, see below
```

`.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/tileward_guard_hook.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

`UserPromptSubmit` takes no matchers: it fires on every prompt, which is the point.

## Install (an organization, enforced)

Managed settings, which a user cannot override. macOS
`/Library/Application Support/ClaudeCode/managed-settings.json`, Linux
`/etc/claude-code/managed-settings.json`, Windows `C:\Program Files\ClaudeCode\managed-settings.json`,
or push it from the claude.ai admin console.

`allowManagedHooksOnly` is what makes either option below enforcement rather than a suggestion: with
it set, hooks from the user's own or the project's settings are ignored, so a developer cannot
unhook themselves.

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

### Option B: the local script (`type: "command"`)

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

### Which one

The difference is what happens when Tileward cannot be reached at all.

| | A: HTTP endpoint | B: local script |
| --- | --- | --- |
| Anything to install | nothing | the script, on every machine |
| Off-policy prompt | blocked | blocked |
| Bad or revoked key | blocked | blocked |
| Empty balance | blocked | blocked |
| **Tileward unreachable** (network, DNS, our outage) | **prompt runs** | **blocked** |
| Script deleted or made non-executable | not applicable | prompt runs |

Claude Code fails open when it cannot reach an HTTP hook, and no response can change that because
nothing answers. The script is a local process, so it can refuse on its own. Option A is easier to
run; option B is the one that holds when we are down. Pick with that sentence in mind, not the
install cost.

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
cc-p-129161755  client=True  rejected  reason=guard:commodity_trading  tok=8   cost=80
cc-p-252122787  client=True  rejected  reason=guard:options_pricing    tok=14  cost=140
cc-p-233942517  client=True  allowed                                   tok=9   cost=90
```

The response the hook reads is only the decision and the cost. The tile that refused a prompt is
recorded in the audit trail, not returned to the caller. Metering is per token read, so a check is
about 14 tokens, roughly $0.00014. Read the report in the console under Settings, then Audit, or
export it as CSV or JSON.

## Test it before you trust it

```bash
export TILEWARD_API_KEY=tw_live_...
echo '{"hook_event_name":"UserPromptSubmit","prompt_id":"p1","prompt":"hello"}' \
  | ./tileward_guard_hook.py; echo "exit=$?"      # expect 0

echo '{"hook_event_name":"UserPromptSubmit","prompt_id":"p2","prompt":"<something your policy blocks>"}' \
  | ./tileward_guard_hook.py; echo "exit=$?"      # expect 2
```

Claude Code does nothing more than this: it pipes that JSON to the script and reads the exit code.
If those two cases behave, the hook behaves.
