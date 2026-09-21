#!/usr/bin/env python3
"""Check every manifest against a schema, not just against the JSON grammar.

WHY THIS FILE EXISTS. A manifest that parses can still be wrong in the ways that hurt. A plugin.json
with no version leaves nothing to tag. A marketplace entry that names a directory which does not
exist breaks `plugin marketplace add` for everyone who has already installed from here. A hooks.json
that points at a renamed script is a hook that cannot start, which Claude Code reads as a
non-blocking error, so the prompt runs. The "every JSON file parses" step in CI catches none of
these.

It also enforces the one rule RELEASING.md says nothing else checks: the version lives in plugin.json
and a marketplace entry carries none.

CI-ONLY DEPENDENCY. voluptuous is in requirements.txt at the repository root and is imported here
and nowhere else. The hooks stay standard-library only; see the note in requirements.txt.

    python3 tests/manifest_schema.py   # one line per file and per control, exits 1 on any failure

THE CONTROLS ARE THE CHECK OF THE CHECK. Each one plants a single fault in a copy of a real manifest
and requires the schema to refuse it. A schema that accepts everything passes every real file, so
without them a green run would prove nothing. Add a control whenever you add a rule.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
from collections.abc import Callable
from typing import Any

import voluptuous as v

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Named to the existing convention: lowercase words joined by hyphens.
SLUG = v.Match(r"^[a-z0-9]+(-[a-z0-9]+)*$", msg="expected lowercase words joined by hyphens")
SEMVER = v.Match(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$", msg="expected MAJOR.MINOR.PATCH")
TEXT = v.All(str, v.Match(r"\S", msg="expected non-empty text"))
RELATIVE = v.All(TEXT, v.Match(r"^\./", msg="expected a path beginning ./"))


def _positive_number(value: object) -> object:
    # bool is an int subclass, so `"timeout": true` would otherwise pass as one second.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise v.Invalid("expected a positive number")
    return value


def _no_version(_: object) -> object:
    raise v.Invalid("the version lives in plugin.json only (see RELEASING.md)")


def _command_needs_command(handler: dict[str, Any]) -> dict[str, Any]:
    if handler["type"] == "command" and "command" not in handler:
        raise v.Invalid("a command hook needs a command")
    return handler


def _server_needs_target(server: dict[str, Any]) -> dict[str, Any]:
    if "url" not in server and "command" not in server:
        raise v.Invalid("an MCP server needs a url or a command")
    return server


# Unknown keys are allowed throughout: Claude Code adds manifest fields, and a schema stricter than
# the client would fail CI on a valid file. What is pinned is what this repo depends on.
def _schema(spec: object) -> v.Schema:
    return v.Schema(spec, extra=v.ALLOW_EXTRA)


PARTY = {v.Required("name"): TEXT, v.Optional("url"): v.Url()}

MARKETPLACE = _schema({
    v.Required("name"): SLUG,
    v.Required("description"): TEXT,
    v.Required("owner"): PARTY,
    v.Required("plugins"): v.All([{
        v.Required("name"): SLUG,
        v.Required("source"): v.Any(RELATIVE, {v.Required("source"): TEXT}),
        v.Required("description"): TEXT,
        v.Optional("version"): _no_version,
    }], v.Length(min=1)),
})

PLUGIN = _schema({
    v.Required("name"): SLUG,
    v.Required("version"): SEMVER,
    v.Required("description"): TEXT,
    v.Optional("displayName"): TEXT,
    v.Optional("author"): PARTY,
    v.Optional("homepage"): v.Url(),
    v.Optional("repository"): v.Url(),
    v.Optional("license"): TEXT,
    v.Optional("keywords"): [TEXT],
    v.Optional("mcpServers"): v.Any(RELATIVE, dict),
})

HOOKS = _schema({
    v.Required("hooks"): v.All({
        str: [{
            v.Optional("matcher"): str,
            v.Required("hooks"): v.All([v.All(_schema({
                v.Required("type"): TEXT,
                v.Optional("command"): TEXT,
                v.Optional("timeout"): _positive_number,
            }), _command_needs_command)], v.Length(min=1)),
        }],
    }, v.Length(min=1)),
})

MCP = _schema({
    v.Required("mcpServers"): v.All({
        str: v.All(_schema({
            v.Optional("type"): v.In(["http", "sse", "stdio"]),
            v.Optional("url"): TEXT,
            v.Optional("command"): TEXT,
            v.Optional("headers"): {str: str},
            v.Optional("timeout"): _positive_number,
        }), _server_needs_target),
    }, v.Length(min=1)),
})

# (kind, glob relative to the repository root, schema)
KINDS = [
    ("marketplace", ".claude-plugin/marketplace.json", MARKETPLACE),
    ("plugin", "plugins/*/.claude-plugin/plugin.json", PLUGIN),
    ("hooks", "plugins/*/hooks/hooks.json", HOOKS),
    ("mcp", "plugins/*/.mcp.json", MCP),
]
SCHEMAS = {kind: schema for kind, _, schema in KINDS}

# `"${CLAUDE_PLUGIN_ROOT}"/hooks/x.py` -> hooks/x.py
PLUGIN_ROOT_PATH = re.compile(r'^"?\$\{CLAUDE_PLUGIN_ROOT\}"?/(\S+)')


def schema_errors(kind: str, doc: object, label: str) -> list[str]:
    try:
        SCHEMAS[kind](doc)
    except v.MultipleInvalid as exc:
        return [f"{label}: {'.'.join(map(str, e.path)) or '<root>'}: {e.msg}" for e in exc.errors]
    return []


def marketplace_links(market: dict[str, Any], plugins: dict[pathlib.Path, dict[str, Any]]) -> list[str]:
    """Each entry must resolve to a plugin directory whose manifest carries the same name."""
    errors = []
    for entry in market["plugins"]:
        source = entry["source"]
        if not isinstance(source, str):
            continue
        manifest = ROOT / source / ".claude-plugin" / "plugin.json"
        if manifest not in plugins:
            errors.append(f"{entry['name']}: {source} has no .claude-plugin/plugin.json")
        elif plugins[manifest]["name"] != entry["name"]:
            errors.append(f"{source}: the marketplace calls it {entry['name']!r} but plugin.json "
                          f"says {plugins[manifest]['name']!r}")
    return errors


def mcp_links(plugin_dir: pathlib.Path, plugin: dict[str, Any]) -> list[str]:
    """A plugin.json that names its MCP file must name one that exists."""
    target = plugin.get("mcpServers")
    if isinstance(target, str) and not (plugin_dir / target).is_file():
        return [f"{plugin['name']}: mcpServers points at {target}, which does not exist"]
    return []


def hook_links(plugin_dir: pathlib.Path, plugin: dict[str, Any], hooks: dict[str, Any]) -> list[str]:
    """A hook command that runs a file from the plugin must name one that exists."""
    errors = []
    for groups in hooks["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                found = PLUGIN_ROOT_PATH.match(handler.get("command", ""))
                if found and not (plugin_dir / found.group(1)).is_file():
                    errors.append(f"{plugin['name']}: hook command points at {found.group(1)}, "
                                  "which does not exist")
    return errors


def link_errors(
    kind: str, path: pathlib.Path, doc: Any, plugins: dict[pathlib.Path, dict[str, Any]]
) -> list[str]:
    """The cross-file checks that apply to a manifest of this kind."""
    if kind == "marketplace":
        return marketplace_links(doc, plugins)
    # plugins/<name>/.claude-plugin/plugin.json and plugins/<name>/hooks/hooks.json both sit two
    # levels below the plugin directory.
    plugin_dir = path.parent.parent
    if kind == "plugin":
        return mcp_links(plugin_dir, doc)
    if kind == "hooks":
        plugin = plugins.get(plugin_dir / ".claude-plugin" / "plugin.json")
        if plugin is None:
            return [f"{path.relative_to(ROOT)}: no plugin.json beside this hooks directory"]
        return hook_links(plugin_dir, plugin, doc)
    return []


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# (name, kind, plant a fault in place, which layer must refuse it: "schema" or "links")
Control = tuple[str, str, Callable[[Any], object], str]


def _first_handler(doc: Any) -> dict[str, Any]:
    return next(iter(doc["hooks"].values()))[0]["hooks"][0]


CONTROLS: list[Control] = [
    ("plugin: no version",              "plugin", lambda d: d.pop("version"),                                "schema"),
    ("plugin: version is not semver",   "plugin", lambda d: d.update(version="1.0"),                         "schema"),
    ("plugin: name has capitals",       "plugin", lambda d: d.update(name="Tileward Guard"),                 "schema"),
    ("plugin: homepage is not a URL",   "plugin", lambda d: d.update(homepage="tileward"),                   "schema"),
    ("plugin: keywords is a string",    "plugin", lambda d: d.update(keywords="context"),                    "schema"),
    ("plugin: mcpServers file missing", "plugin", lambda d: d.update(mcpServers="./does-not-exist.json"),   "links"),
    ("marketplace: entry has a version", "marketplace", lambda d: d["plugins"][0].update(version="1.0.0"),   "schema"),
    ("marketplace: no plugins",         "marketplace", lambda d: d.update(plugins=[]),                       "schema"),
    ("marketplace: entry has no source", "marketplace", lambda d: d["plugins"][0].pop("source"),            "schema"),
    ("marketplace: source not relative", "marketplace", lambda d: d["plugins"][0].update(source="plugins/x"), "schema"),
    ("marketplace: source directory missing", "marketplace",
     lambda d: d["plugins"][0].update(source="./plugins/does-not-exist"),                                    "links"),
    ("marketplace: name differs from plugin.json", "marketplace",
     lambda d: d["plugins"][0].update(name="renamed"),                                                       "links"),
    ("hooks: command hook has no command", "hooks", lambda d: _first_handler(d).pop("command"),             "schema"),
    ("hooks: timeout is a boolean",     "hooks", lambda d: _first_handler(d).update(timeout=True),           "schema"),
    ("hooks: timeout is zero",          "hooks", lambda d: _first_handler(d).update(timeout=0),              "schema"),
    ("hooks: event has no handlers",    "hooks", lambda d: next(iter(d["hooks"].values()))[0].update(hooks=[]), "schema"),
    ("hooks: script does not exist",    "hooks",
     lambda d: _first_handler(d).update(command='"${CLAUDE_PLUGIN_ROOT}"/hooks/does_not_exist.py'),         "links"),
    ("mcp: server has neither url nor command", "mcp",
     lambda d: next(iter(d["mcpServers"].values())).pop("url"),                                              "schema"),
    ("mcp: unknown transport",          "mcp",
     lambda d: next(iter(d["mcpServers"].values())).update(type="carrier-pigeon"),                          "schema"),
    ("mcp: headers value is not text",  "mcp",
     lambda d: next(iter(d["mcpServers"].values())).update(headers={"Authorization": 1}),                   "schema"),
]


def main() -> int:
    failures = 0
    docs: dict[str, dict[pathlib.Path, Any]] = {kind: {} for kind, _, _ in KINDS}
    matched: set[pathlib.Path] = set()

    for kind, pattern, _ in KINDS:
        for path in sorted(ROOT.glob(pattern)):
            matched.add(path)
            label = str(path.relative_to(ROOT))
            try:
                doc = load(path)
            except (OSError, ValueError) as exc:
                print(f"FAIL  {label}: {exc}")
                failures += 1
                continue
            docs[kind][path] = doc
            problems = schema_errors(kind, doc, label)
            print("\n".join(f"FAIL  {p}" for p in problems) if problems else f"ok    {label}")
            failures += len(problems)

    # A manifest with no schema is one nothing checks, so a new kind of file has to be added above.
    for path in sorted({*(ROOT / ".claude-plugin").rglob("*.json"), *(ROOT / "plugins").rglob("*.json")}):
        if path not in matched:
            print(f"FAIL  {path.relative_to(ROOT)}: no schema covers this file")
            failures += 1

    plugins = docs["plugin"]
    for kind in ("marketplace", "plugin", "hooks"):
        for path, doc in docs[kind].items():
            problems = link_errors(kind, path, doc, plugins)
            print("\n".join(f"FAIL  {p}" for p in problems) if problems
                  else f"ok    {path.relative_to(ROOT)} links resolve")
            failures += len(problems)

    for name, kind, plant, layer in CONTROLS:
        if not docs[kind]:
            print(f"skip  control {name}: no {kind} manifest to plant it in")
            continue
        path = next(iter(docs[kind]))
        doc = copy.deepcopy(docs[kind][path])
        plant(doc)
        if layer == "schema":
            refused = bool(schema_errors(kind, doc, name))
        else:
            refused = bool(link_errors(kind, path, doc, plugins))
        if refused:
            print(f"ok    control refused: {name}")
        else:
            print(f"FAIL  control accepted a fault it must refuse: {name}")
            failures += 1

    print(f"\n{'FAILED' if failures else 'passed'}: {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
