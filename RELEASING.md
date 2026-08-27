# Releasing

Two plugins live here and they version **independently**. A release is a version bump in that
plugin's manifest plus a git tag. The tag is the durable record of which commit shipped under which
number, which is what you need the first time somebody reports a bug against a version.

## Where the version lives

`plugins/<name>/.claude-plugin/plugin.json`. `claude plugin tag` refuses to tag unless that agrees
with the plugin's entry in `.claude-plugin/marketplace.json`, so the two cannot silently drift.

What the number means here:

| Bump | When |
| --- | --- |
| **patch** | A fix an installer does not have to know about. Docs, internals, a corrected message. |
| **minor** | Behaviour a user would notice, added: a hook or tool registered, a new option, a default relaxed. |
| **major** | Something an existing install relied on is gone or acts differently: a hook unregistered, an env var renamed, a default made **stricter**. |

Stricter is the one to watch. `tileward-guard` is a control, so tightening it starts blocking work
that used to run — that is a major bump even when the diff is one line, because the person who
upgrades is the person whose prompts stop going through.

## Cutting a release

The working tree must be clean; the tag is created at `HEAD`. Run the fail-closed sweep first. CI
runs it on every push, but a tag is a receipt for one specific commit, and you want the answer
before you write the receipt:

```bash
python3 tests/exit_codes.py
```

```bash
claude plugin tag --dry-run plugins/<name>   # what would happen
claude plugin tag --push plugins/<name>      # annotated tag + push to origin
```

That creates `<name>--v<version>` and pushes it. Tag only the plugin you actually changed — a
release of one is not a release of the other, which is the whole point of versioning them apart.

## What a tag does and does not do

It does **not** gate installs. It records. Treat merging to `main` as the thing that ships and the
tag as the receipt, so:

- Bump the version in the same commit as the change, not afterwards. A tag pointing at a commit
  whose manifest says a different number is the failure this scheme exists to prevent, and
  `claude plugin tag` will catch it only if the marketplace entry disagrees too.
- Never move a published tag. `claude plugin tag` takes `--force` and the underlying command is
  `git tag -f` with `push --force`; that is there for a tag you created sixty seconds ago and have
  not pushed, not for one somebody may have installed against.

## History

`v1.0.0` of both plugins is the first tagged release, cut 2026-08-25 — before the repository went
public and before anybody outside Tileward could install either one. Nothing shipped under a
version number before that, which is why neither plugin has a 0.x line.
