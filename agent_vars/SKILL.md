# Agent Vars Skill

Use this file when another coding agent needs package-local instructions for working with Agent Vars.

## Purpose

Agent Vars is a Python CLI for scanning, validating, resolving, materializing, publishing, and debugging repository environment configuration.

## Installed Location

This file is packaged inside the `agent_vars` Python package as:

```text
agent_vars/SKILL.md
```

An agent can locate it from Python with:

```python
from importlib.resources import files

skill_path = files("agent_vars").joinpath("SKILL.md")
print(skill_path.read_text(encoding="utf-8"))
```

## Commands

Use the installed `agent-vars` command:

```bash
agent-vars --help
agent-vars --contract agent-vars.yaml scan
agent-vars --contract agent-vars.yaml validate --environment dev
agent-vars --contract agent-vars.yaml materialize SERVICE --environment dev --dry-run
```

## Safety

- Do not print or store secret values unless the user explicitly asks for revealed output in a private terminal.
- Prefer `doctor`, `trace`, `events`, and `diff` without `--reveal` when debugging.
- Generated preview or sandbox values should be published to the Agent Vars runtime registry, not written into shared secret managers.
- File secrets should be materialized with `--mount-root` and cleaned up with `unmount`.

## Development

Run tests with:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,publish]'
.venv/bin/pytest -q
```

Build distributable artifacts with:

```bash
.venv/bin/python -m build
```
