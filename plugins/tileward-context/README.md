# Tileward Context for Claude Code

Recall the relevant slice of a long conversation instead of resending the whole transcript. The
server holds the history; each turn asks it for the part that matters and pays for that part only.

```
long conversation
   -> tileward_remember after each exchange        (the server keeps the turn)
   -> tileward_recall before answering             (you get back the relevant slice, in a budget)
      -> the model reads the slice, not the transcript
```

This is a **hosted** MCP server at `https://context.tileward.com`. There is no local server to run,
no Python or `uv` to install, and no database on your machine.

## Why this one IS an MCP server

The other plugin in this marketplace, [`tileward-guard`](../tileward-guard), argues at length that a
guard must not be an MCP server: an MCP server exposes tools the model *chooses* to call and the user
can remove, which is exactly wrong for a control. Context is the opposite kind of thing. It is a
capability the model should reach for when it helps, and a user who removes it has slowed their own
session down rather than escaped a policy. Tools are the right shape here; a gate is not.

## Install

```bash
claude plugin marketplace add Tileward-com/tileward-claude-marketplace
claude plugin install tileward-context@tileward
export TILEWARD_API_KEY=tw_live_...
```

The **same key** as `tileward-guard` — one key, both plugins. Restart Claude Code, then `/mcp`
should list `tileward-context` with its tools.

> **You do not need this plugin to use Tileward Context.** It is an HTTP endpoint plus a bearer
> token, so
> `claude mcp add --transport http tileward-context https://context.tileward.com --header "Authorization: Bearer $TILEWARD_API_KEY"`
> gets you the same tools with nothing installed. Unlike `tileward-guard`, there is no fail-closed
> property to lose by wiring it directly — recall is a capability, not a gate — so what the plugin
> adds here is packaging and updates, not enforcement.

## Configuration

| Variable | Default | What it does |
| --- | --- | --- |
| `TILEWARD_API_KEY` | *(none)* | Required. The key whose account holds the context store. |
| `TILEWARD_CONTEXT_URL` | `https://context.tileward.com` | Override the endpoint. One string, no path to append. `https://api.tileward.com/mcp` still serves, so older configs keep working. |

Both are expanded by Claude Code when it reads [`.mcp.json`](.mcp.json). If `TILEWARD_API_KEY` is
unset, the config still loads and the header is sent with the literal text `${TILEWARD_API_KEY}`;
`claude mcp list` reports the missing variable and the server fails to connect. It does not silently
run unauthenticated.

### Why the timeout is 120000

[`.mcp.json`](.mcp.json) pins `"timeout": 120000` — two minutes, per tool call. Left unset, that
wall-clock limit falls through to `MCP_TOOL_TIMEOUT`, whose default is about **28 hours**, which is
not a limit anyone chose.

Two minutes rather than something tighter, for two reasons. Claude Code applies a second timer to
every HTTP MCP request covering the wait for the server's first response byte; it is 60 seconds
unless the per-server `timeout` is set to 60 seconds or more, and a smaller value does not shorten
it. So anything under 60000 buys nothing. And this server carries `tileward_ingest_file`, which reads
and embeds a whole file — far slower than recall, and the operation most likely to want the room.

Two minutes is also where Claude Code moves a long main-conversation tool call into a background
task, so this ceiling lands exactly where the call would stop blocking the turn anyway: it either
answers within the turn or fails, and never becomes a background task.

**Recall itself is nowhere near this.** Measured against production on 2026-08-25, `tileward_recall`
answered in 163–299 ms over five samples (against `api.tileward.com/mcp`). The budget is for the slow tools and a bad link, not
because the server is slow.

## The tools

Conversation memory: `tileward_remember`, `tileward_remember_many`, `tileward_recall`,
`tileward_context`, `tileward_pin`, `tileward_unpin`, `tileward_forget`, `tileward_reset`,
`tileward_set_topics`, `tileward_stats`.

Documents: `tileward_ingest_document`, `tileward_ingest_file`, `tileward_list_documents`,
`tileward_delete_document`.

Account: `tileward_clear_account`, `tileward_purge_account`, `tileward_close_account`. These are
destructive and account-wide — `tileward_purge_account` is not undoable.

One name misleads and is worth knowing before you rely on it: **`tileward_forget` does not delete
turns.** It drops a topic from recall. `tileward_reset` is what tombstones turns.

**If you have seen `twinkle_*` names, they still work.** These tools were `twinkle_*` — the
engine's internal name — until 2026-08-27. The server stopped listing those names, so a fresh
connection is offered only `tileward_*`, but every retired name is still answered: a client that
connected before the rename, or a `CLAUDE.md` that still says `twinkle_recall`, keeps working
untouched. Write `tileward_*` in anything new.

## Conversation scoping is a data-isolation control, not a preference

Every stateful tool takes an optional `conversation` argument, and **omitting it is not a small
quality regression**. The server falls back to a single per-key store, so recall starts returning
*other conversations'* material as if it belonged to this one.

Claude Code does not send a per-conversation header, so the model has to pass `conversation`
itself. The server's own instructions tell it to — they arrive with the tool list and say to use a
stable per-conversation id, the same value every time within a conversation and a different one
across conversations. That is the mechanism today, and it is a model-follows-instructions
mechanism, not an enforced one. If you are running one key across several projects or several
people, treat the isolation as best-effort and give each project its own key.

**Verified against production on 2026-08-26.** With the argument, a second conversation could not
see the first's material and the first could recall its own. Without it, both landed in the same
store and one recalled the other's. So the argument is real isolation rather than a hint.

**A static header is not a substitute**, and it is worth saying because a header is documented
elsewhere. `X-Tileward-Conversation` had no effect in that same test, on either endpoint. Even if
it worked, headers in an MCP server config are fixed for the life of the connection, so no value
you could put in one changes per conversation. The argument varies at the right granularity; a
header cannot.

## Getting the model to actually use it

The server ships usage instructions with its tool list, so in most sessions Claude will call
`tileward_recall` and `tileward_remember` on its own. To make it explicit for a repo, add this to your
`CLAUDE.md`:

> You have Tileward Context tools. Before answering a substantive question, call `tileward_recall`
> with the user's latest message as the query and rely on the returned context block instead of
> scrolling history. After each exchange, call `tileward_remember` on the user turn and your reply.
> Use `tileward_pin` for durable facts (the goal, hard constraints) and `tileward_forget` when a topic
> is finished. Pass the same `conversation` id on every call in a conversation.

## What leaves your machine

The conversation text you ask it to remember, and your recall queries. That is the product rather
than a side effect — the server cannot return the relevant slice of something it was never given.
Storage is per API key. If that is not acceptable for a given repository, do not enable this plugin
there; there is no local-only mode of the hosted endpoint.
