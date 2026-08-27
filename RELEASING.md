# Releasing

Two plugins live here and they version **independently**. A release is a version bump in that
plugin's manifest plus a git tag. The tag is the durable record of which commit shipped under which
number, which is what you need the first time somebody reports a bug against a version.

## Where the version lives

`plugins/<name>/.claude-plugin/plugin.json`, and nowhere else. The marketplace entry deliberately
carries no `version`: for a git-based source Claude Code detects updates from the resolved commit,
and setting the number in both places invites them to disagree.

That does mean nothing mechanically checks the bump. `claude plugin tag` compares the manifest
against the marketplace entry, so with no entry to compare against it has nothing to catch. Getting
the number right is on the person cutting the release.

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
  **nothing here will catch it for you.** `claude plugin tag` refuses only when the marketplace
  entry names a version that disagrees; ours names none, so the tool tags whatever the manifest
  says, including a number nobody bumped. Read the manifest before you tag.
- Never move a published tag. `claude plugin tag` takes `--force` and the underlying command is
  `git tag -f` with `push --force`; that is there for a tag you created sixty seconds ago and have
  not pushed, not for one somebody may have installed against.

## History

`v1.0.0` of both plugins is the first release, cut 2026-08-27, the day this repository went public.
Nothing shipped under a version number before that, which is why neither plugin has a 0.x line.

Tags did exist earlier, while the repository was private. They were deleted rather than kept,
because they recorded numbers nobody could install against and would have made the first public
release look like a mid-series patch. That is the one situation in which discarding a tag is right,
and it does not generalise: from here every tag is a receipt somebody may have installed against,
so the rule above — never move a published tag — applies without exception.
