# Tileward Guard

Refuse off-policy prompts in Claude Code **before the model sees them**, using the policy bound to
your Tileward API key. The decision is made out of band, the model is never asked to police itself,
and a refused prompt costs zero generation tokens because nothing is generated.

```
you type a prompt
   -> this plugin's UserPromptSubmit hook runs first
      -> POST /v1/guard  {"input": "<your prompt>"}   (the key's own policy decides)
         -> allowed  : the prompt goes to the model
         -> refused  : the prompt NEVER reaches the model, you see why
   -> every decision lands in your Tileward audit trail, with the tile that refused it
```

## Setup

```bash
export TILEWARD_API_KEY=tw_live_...   # a key with a policy bound to it
```

**A key with no bound policy blocks nothing.** The plugin will allow everything and look installed.
Bind a policy first in the console: Settings, then API keys, then Governance. The same key then
governs Claude Code, your apps, and anything else calling `/v1/guard`. Change the policy in one
place and every surface follows.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TILEWARD_API_KEY` | required | The key whose bound policy decides. Unset means every prompt is blocked. |
| `TILEWARD_API` | `https://api.tileward.com/v1/guard` | Endpoint. |
| `TILEWARD_TIMEOUT` | `5` | Seconds. Must stay below the hook's own 15s timeout. |
| `TILEWARD_FAIL_OPEN` | `0` | `1` allows prompts when the guard cannot be reached. |
| `TILEWARD_ACTOR` | the OS username | Who to attribute this machine's checks to in the audit report. `-` sends nothing. |

## What this is, and what it is not

**Installing this plugin on your own machine is a guardrail you chose.** You can `/plugin uninstall`
it whenever you like. That is the correct shape for someone who wants the policy applied to their
own work, and it is not a control over anybody.

**Governing someone else requires managed settings**, where an administrator force-enables this
plugin and the user cannot turn it off. That is a different install, documented in
[the integration guide](../../README.md#install-an-organization-enforced). If you are here to govern
employees or family, read that instead. A plugin the governed party can uninstall is a suggestion.

## Fail closed

If the guard cannot be reached, this hook **blocks**. A control that allows everything whenever its
backend is unreachable is not a control, it is a log. Set `TILEWARD_FAIL_OPEN=1` to invert that if
availability matters more to you than the policy.

The limits of that promise, the exit-code contract, and the Cloudflare user-agent trap are all
documented in [the integration guide](../../README.md).

## Cost

Metering is per token read, so a check is about 14 tokens, roughly $0.00014. Refused prompts cost
only the check, since nothing is generated.
