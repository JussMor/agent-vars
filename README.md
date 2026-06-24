# Agent Vars

Agent Vars is an enterprise environment-configuration control plane for repositories with many services, many `.env` files, cloud secret managers, file-based credentials, and ephemeral workspaces.

It is not a replacement for GCP Secret Manager, Cloudflare, Doppler, Vault, AWS Secrets Manager, 1Password, Kubernetes Secrets, or CI/CD variable stores. Those systems continue to store and protect values. Agent Vars provides the missing repository-level contract, discovery, validation, materialization, provenance, and runtime coordination layer between the repo and those systems.

## Enterprise problem

In a large company, environment configuration usually lives across:

- multiple service roots in one monorepo or many repos
- many `.env`, `.env.local`, `.env.example`, `.env.qa`, `.env.stage`, `.env.uat`, and `.env.production` files
- CI/CD workflow variables and deployment manifests
- Docker Compose, Kubernetes, Helm, Terraform, and cloud outputs
- service discovery dependencies such as NATS, Redis, MongoDB, Postgres, queues, and object storage
- public frontend build variables such as `VITE_BASE_API_URL`, `VITE_MOBILE_API_URL`, or `NEXT_PUBLIC_API_URL`
- file secrets such as Google service account JSON credentials
- dynamic preview URLs from ephemeral frontend, API gateway, mobile gateway, worker, tunnel, and webhook services

The expensive failure is not only that a secret is missing. The expensive failure is that a build, test, or runtime starts with configuration that is stale, ambiguous, malformed, attached to the wrong service, or published by the wrong ephemeral instance.

## Product principles

1. **Control plane, not secret store**: Agent Vars maps and validates values from existing systems instead of copying every secret into a new database.
2. **Repo topology first**: contracts must reflect the real service roots, gateways, workers, infrastructure dependencies, framework conventions, and CI/CD environments of each repo.
3. **Generated contract, human reviewed**: start with `agent-vars scan`, produce a draft, then let platform or service owners approve it.
4. **Environment overlays**: model long-lived environments such as `dev`, `qa`, `stage`, `uat`, and `prod`; treat `preview` as an overlay, not the only environment.
5. **Values have provenance**: every resolved value should be traceable to source, provider, service, phase, sandbox, instance, status, and last check.
6. **Instances need identity**: dynamic outputs must be published by unique instances with leases so duplicate processes do not silently overwrite each other.
7. **File secrets are first-class**: JSON credentials and config files should be mounted and validated as files when SDKs expect files.

## Implementation status

Milestones 1–7 in [PLAN.md](PLAN.md) are implemented and tested:

- enterprise contract schema, overlays, phases, visibility, and typed service outputs
- single-repository and multi-repository discovery with code, CI, Compose, Kubernetes, Helm, Terraform, and credential-path evidence
- structural and resolved-value validation with actionable diagnostics
- scalar `.env` rendering plus policy-constrained, tracked file-secret mounts and safe unmount lifecycle
- scoped runtime registry with leases, replicas, TTL cleanup, tombstones, and dependency action queues
- redacted provenance, `doctor`, `trace`, `events`, and value-safe `diff`
- GCP, Vercel, Cloudflare metadata, Doppler, Vault, AWS, Kubernetes, plaintext local, and age-encrypted local providers

Milestone 8 governance—reviewer enforcement, public-secret leak policy, audit export, and CI status checks—is intentionally still pending. It is tracked separately and is not implied by the current CLI.

## Core model

Agent Vars treats configuration as a graph:

```text
repo profile
  service: frontend
    requires: VITE_BASE_API_URL <- api-gateway.primary.url
    requires: VITE_MOBILE_API_URL <- api-gateway-mobile.primary.url

  service: api-gateway
    requires: NATS_URL <- runtime.nats.url
    requires: REDIS_URL <- runtime.redis.url
    requires: GOOGLE_APPLICATION_CREDENTIALS <- file.gcp_service_account.mount.path

  service: catalog-service
    requires: MONGODB_URI <- runtime.mongodb.uri
    requires: NATS_URL <- runtime.nats.url
```

The contract describes what each service needs, where each value comes from, when it is required, and what should happen when it changes.

## Repository-level contract

A repo can include `agent-vars.yaml` as a contract between the codebase, developers, CI, preview platforms, and cloud providers.

```yaml
version: 1
project: enterprise-platform

repo:
  topology: monorepo
  service_discovery:
    - name: frontend
      root: frontend
      kind: frontend
      framework: vite
    - name: api-gateway
      root: api-gateway
      kind: api
    - name: api-gateway-mobile
      root: api-gateway-mobile
      kind: api
    - name: microservices
      root: services
      kind: service-group
      discovery:
        dockerfile_pattern: Dockerfile.server
        service_path_pattern: "services/*"

environments:
  dev:
    provider_profile: gcp-dev
  qa:
    provider_profile: gcp-qa
  stage:
    provider_profile: gcp-stage
  uat:
    provider_profile: gcp-uat
  prod:
    provider_profile: gcp-prod

overlays:
  preview:
    inherits: dev
    ephemeral: true
    dynamic_outputs: true

providers:
  gcp-dev:
    kind: gcp
    project_id: company-dev
  gcp-prod:
    kind: gcp
    project_id: company-prod
  doppler:
    kind: doppler
    optional: true
    phase: future
  vercel-preview:
    kind: vercel
    environment: preview
    optional: true
    phase: future

runtime_dependencies:
  nats:
    source: compose.service.nats
    outputs:
      url: NATS_URL
  redis:
    source: compose.service.redis
    outputs:
      url: REDIS_URL
  mongodb:
    source: compose.service.mongodb
    outputs:
      uri: MONGODB_URI

files:
  gcp_service_account:
    source: gcp.secret_manager.service-account-json
    format: json
    mount:
      path: /app/gcp-credentials/service-account.json
      env: GOOGLE_APPLICATION_CREDENTIALS
    policy:
      mode: "0600"
      max_bytes: 65536
      required_json_keys: [type, project_id]

services:
  frontend:
    root: frontend
    framework: vite
    instance_policy:
      primary: required
      replicas: 0
    env_files:
      - frontend/.env.local
    requires:
      - name: VITE_BASE_API_URL
        source: service.api-gateway.primary.url
        visibility: public
        phase: build
        required: true
      - name: VITE_MOBILE_API_URL
        source: service.api-gateway-mobile.primary.url
        visibility: public
        phase: build
        required: false

  api-gateway:
    root: api-gateway
    instance_policy:
      primary: required
      replicas: 0
      lease_ttl_seconds: 60
    env_files:
      - api-gateway/.env
      - api-gateway/.env.preview
    outputs:
      url:
        type: url
        phase: runtime
        visibility: public
    requires:
      - name: NATS_URL
        source: runtime.nats.url
        visibility: internal
        phase: runtime
        required: true
      - name: REDIS_URL
        source: runtime.redis.url
        visibility: internal
        phase: runtime
        required: true
      - name: GOOGLE_APPLICATION_CREDENTIALS
        source: file.gcp_service_account.mount.path
        visibility: file-secret
        phase: setup
        required: true

  api-gateway-mobile:
    root: api-gateway-mobile
    instance_policy:
      primary: required
      replicas: 0
      lease_ttl_seconds: 60
    env_files:
      - api-gateway-mobile/.env
    requires:
      - name: NATS_URL
        source: runtime.nats.url
        visibility: internal
        phase: runtime
        required: true

  catalog-service:
    root: services/catalog
    discovered_from: services/*/Dockerfile.server
    instance_policy:
      primary: optional
      replicas: many
    env_files:
      - services/catalog/.env
    requires:
      - name: MONGODB_URI
        source: runtime.mongodb.uri
        visibility: secret
        phase: runtime
        required: true
      - name: NATS_URL
        source: runtime.nats.url
        visibility: internal
        phase: runtime
        required: true
```

## Adapting to any repo

Do not copy the example literally. The example demonstrates the schema shape. A real contract should be generated from the actual repo.

`agent-vars scan` should inspect:

- service roots and monorepo layout
- frontend framework and public env naming conventions
- `.env*` files and `.env.example` templates
- Docker Compose runtime dependencies
- Kubernetes, Helm, Terraform, and cloud output references
- CI/CD workflow environment names and deployment matrices
- Dockerfile and build patterns such as `Dockerfile.server`
- existing GCP credential paths such as `/app/gcp-credentials` or `/app/.config/gcloud`
- duplicated variables that mean different things in different services

Then it should generate a draft contract and report uncertain mappings for human review.

The scanner supports coordinated multi-repository profiles by repeating `--repo`:

```bash
agent-vars scan --discover --repo ../frontend --repo ../api --repo ../workers --out agent-vars.workspace.json
```

Each repository keeps its own evidence while workspace service and runtime-dependency names are repository-qualified. Scanner evidence includes package build scripts, framework config references, CI environment matrices, Kubernetes secret/config references, Helm values, Terraform variables/outputs, cloud resource references, and credential paths.


## Human workflow: generated, not handwritten

A human should not maintain this entire contract by hand. In enterprise repos, variables change often because code changes, services move, CI workflows evolve, and cloud resources are renamed. Manual YAML editing would become another source of drift.

The intended workflow is:

```bash
agent-vars scan --discover --repo . --out agent-vars.yaml
# Review the draft and add provider profiles.
agent-vars resources --environment dev
agent-vars suggest --environment dev
agent-vars approve
agent-vars sync --environment dev
agent-vars validate --environment dev --overlay preview --service frontend --phase build
```

Agent Vars keeps repository discovery and approved provider-binding state synchronized from evidence:

- code references such as `process.env`, `import.meta.env`, framework config, and build scripts
- `.env.example`, `.env.template`, Docker Compose, Kubernetes, Helm, Terraform, and CI/CD workflows
- provider metadata from systems such as GCP Secret Manager
- runtime publications from ephemeral services
- service ownership rules approved by platform teams

Humans review the generated contract and approve suggested bindings. `approve` writes resolution input to `.agent-vars/approvals.json`; `sync` checks whether approved provider resources still exist. Neither command silently rewrites the contract or stores secret payloads.

## Provider guidance is automation, not magic

If a user says "you can use GCP Secret Manager", Agent Vars should use that as a provider capability and automatically guide services where confidence is high. It should not silently guess critical mappings.

The GCP adapter:

1. listing available secrets for the selected GCP project
2. matching secret names to discovered env vars by convention
3. suggesting provider bindings for each service
4. showing confidence and evidence before applying a mapping
5. reading an approved secret only during resolution or materialization

Example suggestion:

```text
service: api-gateway
required: GOOGLE_APPLICATION_CREDENTIALS
suggested_source: gcp.secret_manager.service-account-json
materialization: file mount
path: /app/gcp-credentials/service-account.json
confidence: medium
evidence:
  - env var name matches Google SDK convention
action: approve | edit | reject
```

The suggestion step does not inspect payloads or create mounts. JSON validation happens only after the contract explicitly declares a file secret and materialization fetches its approved payload. Unapproved `review.required` values remain unresolved.

## Environments and preview overlays

Enterprise repos usually have long-lived environments and temporary overlays:

```yaml
environments:
  dev: {}
  qa: {}
  stage: {}
  uat: {}
  prod: {}

overlays:
  preview:
    inherits: dev
    ephemeral: true
    dynamic_outputs: true
```

This keeps production/stage/QA contracts stable while allowing pull-request or AI-workspace previews to publish temporary URLs.

## File secrets and cloud-fed config

Some required configuration is not a scalar value. Google credentials are a common example because many SDKs expect a JSON file and an env var that points to that file.

Agent Vars fetches explicitly declared JSON from the configured provider, validates it, mounts it at the path expected by the repo, and injects the pointer env var. `validate` and `doctor` report the requirement as resolved only when the mounted file exists, satisfies policy, and matches its lifecycle manifest; pass `--mount-root` to verify it.

```yaml
files:
  gcp_service_account:
    source: gcp.secret_manager.service-account-json
    format: json
    mount:
      path: /app/gcp-credentials/service-account.json
      env: GOOGLE_APPLICATION_CREDENTIALS
```

This avoids unsafe multi-line JSON copy/paste into generic env forms.


## Ephemeral values must not mutate shared secret managers

A generated value for one developer, task, sandbox, or pull request should not be written back into a shared secret manager entry that other people or environments consume. Secret managers should remain the source for shared stable secrets. Ephemeral values should live in an Agent Vars runtime registry scoped to the workspace or task.

Use layered resolution instead of overwriting provider values:

```text
1. task override        task.<task_id>.<service>.<name>       ttl: task lifetime
2. sandbox override     sandbox.<sandbox_id>.<service>.<name> ttl: sandbox lifetime
3. preview overlay      overlay.preview.<service>.<name>      ttl: PR/workspace lifetime
4. environment value    env.dev.<service>.<name>              stable
5. provider secret      gcp/doppler/vault secret              stable shared source
6. repo default         .env.example/default                  non-secret fallback
```

When a value such as `service.api-gateway.primary.url` is generated for a user sandbox, Agent Vars should publish it only to the scoped runtime registry:

```bash
agent-vars --contract agent-vars.example.yaml publish service.api-gateway.primary.url https://api-gateway-alice-abc123.example.dev \
  --environment dev \
  --overlay preview \
  --sandbox codex-workspace-abc123 \
  --task task-789 \
  --service api-gateway \
  --ttl 2h
```

That scoped value queues an action only for the dependent frontend in the same sandbox:

```text
changed: service.api-gateway.primary.url
scope: sandbox codex-workspace-abc123 / task task-789
dependent: frontend.VITE_BASE_API_URL
queued_action: rebuild frontend in same sandbox only
not affected: qa, stage, prod, other developers, other sandboxes
```

Rules:

- never write generated preview URLs into a shared secret-manager key
- never mutate stable provider secrets for task-local values
- store ephemeral generated values in Agent Vars with owner, scope, TTL, and lease metadata
- materialize scoped values into `.env` files or process env only for the matching sandbox/task
- delete task-scoped values when the task completes
- expire sandbox-scoped values when the sandbox lease ends
- keep a tombstone/audit event so debugging remains possible without keeping the secret value forever

An external orchestrator consumes and acknowledges queued actions. The CLI does not directly rebuild or restart services.

## Validation flow

A platform should validate the contract before expensive setup, build, test, or deploy steps.

```text
clone repo
scan or read agent-vars.yaml
resolve environment and overlay
load provider mappings
resolve scalar secrets
materialize file secrets
resolve runtime dependencies
wait for required dynamic service outputs
validate required values by service and phase
inject generated env files and mounted files
run setup/build/test/deploy commands
```

Failures should be actionable:

```text
service: api-gateway
variable: GOOGLE_APPLICATION_CREDENTIALS
phase: setup
source: file.gcp_service_account.mount.path
status: failed
error: mounted file is not valid JSON
fix: check gcp.secret_manager.service-account-json for the selected provider profile
```

## Provenance and debugging

Every resolved value should carry metadata so teams can answer why a service received a specific value.

```text
value: VITE_BASE_API_URL
service: frontend
environment: dev
overlay: preview
sandbox: codex-workspace-abc123
producer_service: api-gateway
producer_instance: api-gateway.01HZZ6TJ7E7H6J7X4Y9R0J5A9M
source: service.api-gateway.primary.url
phase: build
status: resolved
last_checked_at: 2026-06-20T12:30:00Z
```

Useful commands:

```bash
agent-vars doctor api-gateway --environment dev --overlay preview
agent-vars trace service.api-gateway.primary.url --environment dev --overlay preview --sandbox codex-workspace-abc123
agent-vars events --sandbox codex-workspace-abc123 --type publish
agent-vars diff qa prod --service api-gateway --left-values qa-values.json --right-values prod-values.json
```

## Duplicate instances and leases

A sandbox can accidentally run two copies of the same service. Agent Vars should prevent ambiguous output publishing with instance identity and leases.

```text
sandbox: codex-workspace-abc123
service: api-gateway
instance: api-gateway.01HZZ6TJ7E7H6J7X4Y9R0J5A9M
slot: primary
lease_ttl: 60s
output: url=https://api-gateway-abc123.example.dev
```

Rules:

- every publisher gets a unique `instance_id`
- only one instance can hold the `primary` lease unless replicas are allowed
- dependents read from `service.<name>.primary.url`, not from a mutable global key
- second instances become `candidate` or `replica` unless they explicitly take over the primary lease
- expired leases mark outputs stale and block rebuilds unless policy allows stale reads
- every trace includes the instance that published the value

Lease and lifecycle commands:

```bash
agent-vars renew api-gateway.01HZZ6TJ7E7H6J7X4Y9R0J5A9M \
  --environment dev --overlay preview --sandbox codex-workspace-abc123 --task task-789 --service api-gateway --ttl 60s

agent-vars actions --environment dev --overlay preview --sandbox codex-workspace-abc123 --task task-789
agent-vars ack-actions 0
agent-vars expire
agent-vars cleanup --environment dev --sandbox codex-workspace-abc123 --task task-789
```

Replica publications use `--slot replica` and are checked against the service's `instance_policy.replicas`; `--max-replicas` is available as an explicit runtime override. Publications queue dependency actions from requirement phase: `build` becomes `rebuild`, `hot` becomes `hot-refresh`, and setup/runtime become `restart`. Every queued action carries the exact environment, overlay, sandbox, and task scope and remains available until its publication index is acknowledged. Expiry marks outputs stale, removes scoped payloads, and retains value-free audit tombstones.

Every publication requires a declared publisher service and a key shaped as `service.<service>.<primary|replica>.<output>`. The output must be declared under `services.<service>.outputs`; supported schema types are `string`, `url`, `integer`, `number`, `boolean`, and `json`, with optional enum, regex, and length constraints.

## CLI quickstart

```bash
agent-vars scan --discover --repo . --out agent-vars.yaml
# Review the generated contract and add provider profiles before continuing.
agent-vars resources --environment dev
agent-vars suggest --environment dev
agent-vars approve
agent-vars sync --environment dev
agent-vars validate --environment dev --overlay preview --service api-gateway --phase runtime
agent-vars materialize api-gateway --environment dev --overlay preview --strict --mount-root .agent-vars/mounts
agent-vars publish service.api-gateway.primary.url https://api-gateway-abc123.example.dev --environment dev --overlay preview --sandbox workspace-123 --service api-gateway
agent-vars doctor frontend --environment dev --overlay preview --sandbox workspace-123
```

## Implemented capabilities

- show every required env var across an enterprise repo
- generate service-specific `.env` files from one reviewed contract
- mount required file secrets safely
- resolve scoped service URLs and queue dependent rebuild/restart actions
- track env errors to service, source, provider, phase, sandbox, and instance
- detect stale or conflicting values before build/runtime
- support existing cloud secret managers instead of replacing them
- validate typed dynamic outputs before publication
- track and safely remove mounted file-secret payloads

## Installation and core commands

Agent Vars is an installable Python CLI for scanning, validating, resolving, materializing, publishing, and debugging repository configuration.

For local development, install it in editable mode:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

.venv/bin/agent-vars --contract agent-vars.example.yaml scan
.venv/bin/agent-vars --contract agent-vars.example.yaml validate --environment dev --overlay preview
.venv/bin/agent-vars --contract agent-vars.example.yaml materialize api-gateway --dry-run
```

To build a wheel and install it as a normal command on your computer:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[publish]'
.venv/bin/python -m build

python3 -m pip install --user --force-reinstall dist/agent_vars-0.1.0-py3-none-any.whl
agent-vars --help
```

If you use `pipx` for command-line tools, install the built wheel with:

```bash
pipx install --force dist/agent_vars-0.1.0-py3-none-any.whl
agent-vars --help
```

To upload the package to a Python package index, configure an API token for that index and run:

```bash
.venv/bin/python -m twine check dist/*
.venv/bin/python -m twine upload dist/*
```

GitHub Actions can build and publish the package for you when you publish a GitHub release. This repo includes `.github/workflows/publish-python.yml`, which runs tests, builds the distributions, validates them, and uploads them to PyPI using Trusted Publishing.

Configure PyPI once with a pending or existing Trusted Publisher:

```text
PyPI project: agent-vars
Owner: JussMor
Repository name: agent-vars
Workflow name: publish-python.yml
Environment name: pypi
```

After that, publish a GitHub release for a new version and the workflow will upload the package. PyPI does not allow replacing an already-published version, so update `version` in `pyproject.toml` and `__version__` in `agent_vars/__init__.py` before each release.

The wheel includes an agent instruction file at `agent_vars/SKILL.md`. Another agent can locate the installed file with:

```bash
python3 -c 'from importlib.resources import files; print(files("agent_vars").joinpath("SKILL.md"))'
```

The materializer writes file-secret pointer variables such as `GOOGLE_APPLICATION_CREDENTIALS` to the declared mount path instead of serializing JSON credentials into generated `.env` files.

Resolve actual values and fail when required values are missing:

```bash
agent-vars --contract agent-vars.example.yaml validate \
  --environment dev --overlay preview --service api-gateway --phase runtime \
  --values values.json

agent-vars --contract agent-vars.example.yaml materialize api-gateway \
  --environment dev --overlay preview --values values.json --strict --dry-run
```

The values file uses the documented precedence layers. Values may be global or nested under a service name:

```json
{
  "task_override": {"NATS_URL": "nats://task"},
  "sandbox_override": {},
  "overlay_value": {},
  "environment_value": {"api-gateway": {"REDIS_URL": "redis://dev"}},
  "provider_secret": {},
  "repo_default": {}
}
```

File secrets can be fetched from the environment's provider profile or supplied through `--file-values` for local automation. Declared absolute paths are mapped beneath `--mount-root`, confined to that root, written atomically, checked against size/JSON-shape policies, and assigned owner-only `0400` or `0600` permissions:

```bash
agent-vars --contract agent-vars.example.yaml materialize api-gateway \
  --environment dev --overlay preview --values values.json --strict \
  --file-values file-values.json --mount-root .agent-vars/mounts --out .agent-vars/api-gateway.env

agent-vars --contract agent-vars.example.yaml unmount api-gateway \
  --mount-root .agent-vars/mounts
```

Mount manifests contain paths, hashes, sizes, modes, service/environment scope, and timestamps—but never secret payloads. Unmount refuses to delete a modified file unless `--force` is explicit.

Verify an already mounted file without rematerializing it:

```bash
agent-vars --contract agent-vars.example.yaml validate \
  --environment dev --overlay preview --service api-gateway --phase setup \
  --mount-root .agent-vars/mounts

agent-vars --contract agent-vars.example.yaml doctor api-gateway \
  --environment dev --overlay preview --mount-root .agent-vars/mounts
```

Install development dependencies and run all unit and provider-emulator integration tests with:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

## Provider adapters and guided mapping

The implementation includes provider-guided suggestions and a scoped runtime registry for preview values. Provider kinds are `gcp`, `vercel`, `cloudflare`, `doppler`, `vault`, `aws`, `kubernetes`, `local`, and `local-encrypted`. Cloudflare supports metadata discovery only because deployed Worker secret payloads are write-only.

```yaml
providers:
  worker:
    kind: cloudflare
    name: api-worker
  platform-vault:
    kind: vault
    path: secret/platform
  aws-prod:
    kind: aws
    region: us-east-1
  cluster:
    kind: kubernetes
    namespace: platform
  vercel-preview:
    kind: vercel
    environment: preview
    git_branch: feature-x
    team: platform-team
  encrypted-dev:
    kind: local-encrypted
    path: .secrets/dev.json.age
    identity: ~/.config/age/keys.txt
    format: json
```

```bash
agent-vars --contract agent-vars.example.yaml suggest --provider gcp-dev
agent-vars --contract agent-vars.example.yaml resources --environment dev
agent-vars --contract agent-vars.example.yaml suggest --environment dev
agent-vars --contract agent-vars.example.yaml approve
agent-vars --contract agent-vars.example.yaml sync --provider gcp-dev
agent-vars --contract agent-vars.example.yaml publish service.api-gateway.primary.url https://api-gateway-abc123.example.dev --environment dev --overlay preview --sandbox codex-workspace-abc123 --task task-789 --service api-gateway --ttl 2h
agent-vars --contract agent-vars.example.yaml trace service.api-gateway.primary.url --environment dev --overlay preview --sandbox codex-workspace-abc123
agent-vars --contract agent-vars.example.yaml events --sandbox codex-workspace-abc123 --type publish
agent-vars --contract agent-vars.example.yaml doctor frontend --environment dev --overlay preview
agent-vars --contract agent-vars.example.yaml diff qa prod --service api-gateway --left-values qa-values.json --right-values prod-values.json
```

Provider adapters use their standard CLIs: `gcloud`, `vercel`, `wrangler`, `doppler`, `vault`, `aws`, `kubectl`, and `age`. A provider profile can set `executable` to override the binary. Vercel profiles use `vercel env ls` for names and `vercel env pull` into a temporary file for payload reads; set `environment` to `development`, `preview`, `production`, or a custom environment, and optionally set `git_branch`, `cwd`, `scope`, `team`, or `token`. GCP and Doppler retain their environment-variable fixtures; every provider also accepts a JSON-object `fixture` path in its contract profile for deterministic tests. Payloads are consumed in memory and are not written to suggestion, approval, or sync state.

Suggestions are written to `.agent-vars/suggestions.json` with confidence, evidence, environment, and production impact. `approve` accepts high-confidence non-production mappings by default and writes approved sources to `.agent-vars/approvals.json`, which `validate`, `materialize`, and `doctor` consume automatically. Use `--bindings` to select another approval file. Low-confidence mappings require `--all`; production mappings independently require `--allow-production`. `sync` writes provider resource metadata and binding availability to `.agent-vars/sync-<provider>.json`; it does not persist secret payloads or alter the contract.

`doctor`, `trace`, and `events` redact values by default. Use `--reveal` only in a private terminal. Diagnostics distinguish missing, malformed, stale, provider-error, and conflicting values and include remediation hints. `diff --service` compares value fingerprints and provenance without emitting secret values.

Publishing a second primary instance in the same scope fails while the first lease is active. Use `--takeover` only when replacing that primary is intentional.

The complete roadmap and the explicit Milestone 8 governance boundary are tracked in [PLAN.md](PLAN.md).

### Real GCP Secret Manager verification

The regular suite uses deterministic fixtures and CLI emulators. A separate test performs real read-only Secret Manager list/access calls and never prints or persists the payload:

```bash
gcloud auth login
AGENT_VARS_GCP_INTEGRATION_PROJECT=my-nonprod-project \
AGENT_VARS_GCP_INTEGRATION_SECRET=my-test-secret \
.venv/bin/pytest -q tests/integration/test_gcp_secret_manager_real.py
```

Set `AGENT_VARS_GCP_INTEGRATION_EXPECT_JSON=1` when the test secret must contain a JSON object. Use a dedicated non-production secret with least-privilege `secretmanager.versions.access` and secret-list permissions.
