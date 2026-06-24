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

Or print it from the CLI with:

```bash
agent-vars skill
```

## Commands

Use the installed `agent-vars` command:

```bash
agent-vars --help
agent-vars skill
agent-vars scan --discover --repo . --out agent-vars.yaml
agent-vars --contract agent-vars.yaml scan
agent-vars --contract agent-vars.yaml validate --environment dev
agent-vars --contract agent-vars.yaml materialize SERVICE --environment dev --dry-run
agent-vars --contract agent-vars.yaml materialize SERVICE --environment dev --strict --report materialize-report.json
```

When `materialize` runs without `--dry-run` and without `--out`, Agent Vars writes to the service's declared `env_files` target when one exists. Template names such as `.env.local.example` are written as `.env.local`; otherwise use `--out` for an explicit target.

## Repository Shapes

- Monolith: expect one root service with `root: "."`. Promote root `.env*` templates, Dockerfile evidence, and code env references into that service's `requires`.
- Monorepo: expect one service per nearest package or service root, such as `apps/web`, `apps/api`, `services/catalog`, or a root app plus subservices. Do not create an empty root workspace service when the root only groups child packages.
- Multi-repo workspace: scan with repeated `--repo` flags and expect repository-qualified services such as `frontend:web` or `api:api`.

Generated requirements should be immediately testable. Prefer `source: VARIABLE_NAME` for discovered env vars so `--values`, process environment values, and the active environment provider profile can resolve them. Keep `uncertain_mappings` as the human review queue for provider mapping and required/optional policy.

Generated contracts mark stable application dependencies as required by default. Common CI, E2E, Vercel metadata, local runtime metadata, and feature-toggle variables are optional by default. If a generated contract from an older Agent Vars release marks those local-only variables required, update the contract before using `--strict`.

## Env Materialization Flow

1. Run `agent-vars scan --discover --repo . --out agent-vars.yaml`.
2. Review generated services and `requires`.
3. Run `agent-vars --contract agent-vars.yaml materialize SERVICE --environment dev --dry-run`.
4. Add a local `values.json` or configure `environments.dev.provider_profile` when required values are missing.
5. Run `agent-vars --contract agent-vars.yaml materialize SERVICE --environment dev` to write the inferred env file, or pass `--out` for an explicit target.
6. Use `--strict` in CI or local setup scripts when missing values should fail the command. Add `--report materialize-report.json` when debugging; the report contains status, layer, provider, and hints but never resolved secret payloads.

For provider-backed envs, `resources` lists provider variable names and metadata only. It does not refresh local `.env` values. `materialize` resolves values through the active provider profile and writes the local env file. Vercel profiles use `vercel env ls` for names and `vercel env pull` into a temporary file for payloads. If a Vercel variable appears in `resources` but `materialize --report` marks it missing, the selected `vercel env pull` output for that environment and git branch did not include the value. Vercel pulls are reused in memory for the current command and pulled again on the next command so local env files refresh from the current Vercel state.

## Safety

- Do not print or store secret values unless the user explicitly asks for revealed output in a private terminal.
- Prefer `doctor`, `trace`, `events`, and `diff` without `--reveal` when debugging.
- Generated preview or sandbox values should be published to the Agent Vars runtime registry, not written into shared secret managers.
- File secrets should be materialized with `--mount-root` and cleaned up with `unmount`.
- Providers remain the source of values. Agent Vars reads through provider adapters, resolves mappings, and writes local runtime `.env` or mounted files; it must not mutate provider secrets for generated sandbox or preview values.
- Do not report that env files were updated from a provider unless `materialize` completed or you verified the target `.env` file contents changed. `resources` alone is not proof of materialized values.

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
