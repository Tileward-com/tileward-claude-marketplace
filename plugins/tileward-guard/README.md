# Tileward Guard

Refuse off-policy prompts in Claude Code **before the model sees them**, using the policy bound to
your Tileward API key. The decision is made out of band, the model is never asked to police itself,
and a refused prompt costs zero generation tokens because nothing is generated.

One hook, governing the **ask**.

```
you type a prompt
   -> UserPromptSubmit hook runs first
      -> POST /v1/guard  {"input": "<your prompt>"}   (the key's GOVERNANCE policy decides)
         -> allowed  : the prompt goes to the model
         -> refused  : the prompt NEVER reaches the model, you see why

   -> every decision lands in your Tileward audit trail
```

**Tool calls are not governed yet.** Governing the ask alone is not enough — a permitted prompt can
still lead to `rm -rf` — so a second hook on `PreToolUse` is planned, reading a separate execution
policy. It is not built: `/v1/guard/tool` returns 404, keys have no execution policy kind, and the
console has no page to edit one. This plugin therefore registers no `PreToolUse` hook, and the
script beside this README (`hooks/tileward_pretooluse_hook.py`) is inert until that lands — do not
wire it up by hand, because pointed at a 404 it fails closed and refuses every tool call.

What it *will* send is settled: `{tool_name, tool_input}` plus a `tool_input_fidelity` of `full`,
`truncated` or `omitted`. Sending the input is what makes it a command gate rather than a
capability gate, and it means Bash command lines and edited source reach Tileward — set
`TILEWARD_TOOL_INPUT_MAX=0` if that trade is not one you want to make. See
[the integration guide](../../README.md#tool-call-governance-is-not-available-yet) and
#257.

## Setup

```bash
export TILEWARD_API_KEY=tw_live_...   # a key with a policy bound to it
```

**A key with no bound policy blocks nothing.** The plugin will allow everything and look installed.
Bind a policy first in the console: Settings, then API keys, then Governance. The same key then
governs Claude Code, your apps, and anything else calling `/v1/guard`. Change the policy in one
place and every surface follows.

A governance policy decides prompts, and that is all a key can carry today. It governs what you
ask and permits every tool the agent then reaches for — the gap the planned second hook is meant to
close.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TILEWARD_API_KEY` | required | The key whose bound policy decides. Unset means every prompt is blocked. |
| `TILEWARD_API` | `https://api.tileward.com/v1/guard` | Prompt endpoint (UserPromptSubmit). |
| `TILEWARD_TIMEOUT` | `5` | Seconds. Must stay below the hook's own 15s timeout. |
| `TILEWARD_FAIL_OPEN` | `0` | `1` allows a prompt when Tileward cannot be reached. |
| `TILEWARD_ACTOR` | the OS username | Who to attribute this machine's checks to in the audit report. `-` sends nothing. |
| `TILEWARD_TOOL_INPUT_MAX` | `4096` | Characters of each string inside `tool_input` the **tool** hook sends. `0` sends none, which is a capability gate that cannot tell `rm -rf /` from `ls`. Unused until that hook is in service. |

## What this is, and what it is not

**Installing this plugin on your own machine is a guardrail you chose.** You can `/plugin uninstall`
it whenever you like. That is the correct shape for someone who wants the policy applied to their
own work, and it is not a control over anybody.

**Governing someone else requires managed settings**, where an administrator force-enables this
plugin and the user cannot turn it off. That is a different install, documented in
[the integration guide](../../README.md#install-an-organization-enforced). If you are here to govern
employees or family, read that instead. A plugin the governed party can uninstall is a suggestion.

## Fail closed

If Tileward cannot be reached, the hook **blocks**. A control that allows everything whenever its
backend is unreachable is not a control, it is a log. Set `TILEWARD_FAIL_OPEN=1` to invert that if
availability matters more to you than the policy — it turns an unreachable Tileward into an allow,
and never turns an actual refusal into one.

The same rule holds on the server: a policy Tileward cannot READ is a refusal, not a default.
`/v1/guard/hook` answers `200` with a deny rather than an honest error status, because Claude Code
treats any non-2xx as a non-blocking error and lets the prompt through.

The limits of that promise, the exit-code contract, and the Cloudflare user-agent trap are all
documented in [the integration guide](../../README.md).

## Cost

Metering is per token read, so a **prompt** check is about 14 tokens, roughly $0.00014. Refused
prompts cost only the check, since nothing is generated.
