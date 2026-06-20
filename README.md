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


## Human workflow: generated, not handwritten

A human should not maintain this entire contract by hand. In enterprise repos, variables change often because code changes, services move, CI workflows evolve, and cloud resources are renamed. Manual YAML editing would become another source of drift.

The intended workflow is:

```bash
agent-vars scan
agent-vars suggest
agent-vars approve
agent-vars sync --provider gcp-dev
agent-vars validate --environment dev --overlay preview
```

Agent Vars should keep the contract synchronized from evidence in the repo and connected providers:

- code references such as `process.env`, `import.meta.env`, framework config, and build scripts
- `.env.example`, `.env.template`, Docker Compose, Kubernetes, Helm, Terraform, and CI/CD workflows
- provider metadata from systems such as GCP Secret Manager
- runtime publications from ephemeral services
- service ownership rules approved by platform teams

Humans review and approve the generated contract, but they should not have to rediscover every variable manually.

## Provider guidance is automation, not magic

If a user says "you can use GCP Secret Manager", Agent Vars should use that as a provider capability and automatically guide services where confidence is high. It should not silently guess critical mappings.

The GCP adapter can help by:

1. listing available secrets for the selected GCP project
2. matching secret names to discovered env vars by convention
3. detecting JSON secrets that should be mounted as files
4. suggesting provider bindings for each service
5. showing confidence and evidence before applying a mapping

Example suggestion:

```text
service: api-gateway
required: GOOGLE_APPLICATION_CREDENTIALS
suggested_source: gcp.secret_manager.service-account-json
materialization: file mount
path: /app/gcp-credentials/service-account.json
confidence: high
evidence:
  - env var name matches Google SDK convention
  - secret payload is valid JSON
  - repo already references /app/gcp-credentials
action: approve | edit | reject
```

For low-confidence matches, Agent Vars should stop and ask for approval instead of wiring the wrong secret into a service. The product should feel automatic when conventions are clear and safe when they are not.

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

Agent Vars should fetch the JSON from the configured provider, validate it, mount it at the path already expected by the repo, inject the pointer env var, and mark dependent services as satisfied.

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
agent-vars publish service.api-gateway.url https://api-gateway-alice-abc123.example.dev \
  --environment dev \
  --overlay preview \
  --sandbox codex-workspace-abc123 \
  --task task-789 \
  --ttl 2h
```

That scoped value can trigger only the dependent frontend in the same sandbox to rebuild:

```text
changed: service.api-gateway.primary.url
scope: sandbox codex-workspace-abc123 / task task-789
dependent: frontend.VITE_BASE_API_URL
action: rebuild frontend in same sandbox only
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

This lets Agent Vars fetch stable secrets from GCP Secret Manager while safely combining them with per-user generated values that disappear after the task or sandbox is done.

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
agent-vars trace VITE_BASE_API_URL --service frontend --environment dev --overlay preview
agent-vars events --sandbox codex-workspace-abc123
agent-vars diff qa prod --service api-gateway
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

## Initial product workflow

```bash
agent-vars scan
agent-vars suggest --provider gcp-dev
agent-vars approve
agent-vars sync --provider gcp-dev
agent-vars validate --environment dev --overlay preview
agent-vars materialize frontend --environment dev --overlay preview
agent-vars materialize api-gateway --environment dev --overlay preview
agent-vars publish service.api-gateway.url https://api-gateway-abc123.example.dev
agent-vars doctor frontend --environment dev --overlay preview
```

## What this enables

- show every required env var across an enterprise repo
- generate service-specific `.env` files from one reviewed contract
- mount required file secrets safely
- wire frontend, API gateway, mobile gateway, and microservice URLs automatically
- track env errors to service, source, provider, phase, sandbox, and instance
- detect stale or conflicting values before build/runtime
- support existing cloud secret managers instead of replacing them

## Coding-phase CLI

This repository now includes an initial Python implementation of the `agent-vars` command. It focuses on the first usable coding phase from the plan: loading the repository contract, validating declared environments and overlays, checking service requirements against runtime/file/service sources, summarizing discovered contract shape, and rendering service-specific `.env` output without embedding file-secret contents.

```bash
python -m agent_vars.cli --contract agent-vars.example.yaml scan
python -m agent_vars.cli --contract agent-vars.example.yaml validate --environment dev --overlay preview
python -m agent_vars.cli --contract agent-vars.example.yaml materialize api-gateway --dry-run
```

The materializer writes file-secret pointer variables such as `GOOGLE_APPLICATION_CREDENTIALS` to the declared mount path instead of serializing JSON credentials into generated `.env` files.

## Provider suggestions and runtime registry

The coding-phase implementation also includes provider-guided suggestions and a scoped runtime registry for preview values. `suggest` can inspect supported provider profiles (`gcp`, `doppler`, and `local`) and propose bindings with confidence/evidence instead of silently wiring low-confidence mappings.

```bash
agent-vars --contract agent-vars.example.yaml suggest --provider gcp-dev
agent-vars publish service.api-gateway.primary.url https://api-gateway-abc123.example.dev --environment dev --overlay preview --sandbox codex-workspace-abc123 --task task-789 --service api-gateway --ttl 2h
agent-vars trace service.api-gateway.primary.url --environment dev --overlay preview --sandbox codex-workspace-abc123
agent-vars events --sandbox codex-workspace-abc123
agent-vars doctor frontend --environment dev --overlay preview
agent-vars diff qa prod --service api-gateway
```

For deterministic CI and local tests, provider adapters accept JSON fixture files through `AGENT_VARS_GCP_SECRETS_FILE` and `AGENT_VARS_DOPPLER_SECRETS_FILE`. Without fixtures, the GCP adapter shells out to `gcloud secrets list`, and the Doppler adapter shells out to `doppler secrets download`.
