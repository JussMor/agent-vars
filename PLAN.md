# Agent Vars Enterprise Plan

## Objective

Build a service and CLI that let enterprise repositories declare, validate, materialize, and trace environment configuration across many services, environments, cloud providers, file secrets, runtime dependencies, and ephemeral workspaces.

## Implementation status (2026-06-19)

The roadmap below describes the complete product. Each milestone item is marked individually. `[x]` means implemented and tested; `[ ]` means incomplete, with partial implementations called out explicitly.

- Milestone 1: complete foundation
- Milestone 2: partial
- Milestone 3: complete
- Milestone 4: complete
- Milestone 5: partial
- Milestone 6: partial
- Milestone 7: partial
- Milestone 8: pending

### Pending production work

- [x] Resolve and inject actual scalar values using documented task/sandbox/overlay/environment/provider/default precedence
- [x] Fetch provider secret payloads for implemented adapters without persisting them in workflow metadata
- [ ] Materialize file-secret payloads atomically, validate formats/policies, set permissions, and manage mount lifecycle — partial: atomic writes, JSON validation, mount-root confinement, and `0600` permissions are implemented; policy and lifecycle cleanup remain
- [ ] Expand scanner parsing for Kubernetes, Helm, Terraform outputs, cloud outputs, and CI matrices beyond evidence detection
- [ ] Add renewable leases, replica policy enforcement, service-output schemas, tombstones, and dependent rebuild/restart/hot-refresh hooks
- [ ] Add complete provenance to every resolved value and value-aware environment diffs
- [ ] Add Vault, AWS Secrets Manager, Kubernetes Secrets, Cloudflare secrets, and encrypted-local provider adapters
- [ ] Add approval policies, reviewer enforcement, public-secret leak detection, audit export, and CI status checks
- [ ] Add integration/end-to-end tests against provider emulators or dedicated test projects

## Scope

Agent Vars should manage:

1. scalar secrets such as API keys and database URLs
2. public build configuration such as `VITE_BASE_API_URL` or `NEXT_PUBLIC_API_URL`
3. internal runtime configuration such as NATS, Redis, MongoDB, queues, and service discovery URLs
4. file secrets such as Google service account JSON credentials
5. dynamic service outputs such as preview URLs from ephemeral containers
6. environment overlays such as pull-request preview environments layered on top of `dev`
7. task-scoped and sandbox-scoped ephemeral values that must not mutate shared secret stores
8. provenance for every resolved value
9. instance identity and leases for duplicate-service protection

## Milestones

### Milestone 1: Enterprise contract format

- [x] define `agent-vars.yaml`
- [x] support `repo`, `environments`, `overlays`, `providers`, `runtime_dependencies`, `files`, and `services`
- [x] support service-level `env_files`, `requires`, `instance_policy`, `framework`, and `discovered_from`
- [x] distinguish `secret`, `file-secret`, `public`, and `internal` visibility
- [x] distinguish `setup`, `build`, `runtime`, and `hot` phases

### Milestone 2: Repo scanner and profile generator

- [ ] detect service roots in monorepos and multi-repo workspaces — partial: monorepo roots are discovered; coordinated multi-repo workspace profiles are not implemented
- [x] detect frontend frameworks and public env prefixes such as `VITE_` and `NEXT_PUBLIC_`
- [ ] detect env usage in code, including `process.env`, `import.meta.env`, framework config, and build scripts — partial: direct JavaScript/TypeScript/Python references are detected; framework config and build-script parsing remain
- [x] detect existing `.env*` files and templates
- [x] detect Docker Compose runtime dependencies such as NATS, Redis, MongoDB, and Postgres
- [ ] detect Kubernetes, Helm, Terraform, and cloud output references — partial: manifest files are inventoried, but references and outputs are not parsed
- [ ] detect CI/CD environment matrices such as `dev`, `qa`, `stage`, `uat`, and `prod` — partial: environment-name evidence is detected; matrix structure is not parsed
- [x] detect Dockerfile patterns such as `Dockerfile.server`
- [ ] detect existing credential paths such as `/app/gcp-credentials` and `/app/.config/gcloud`
- [x] produce a draft contract with uncertain mappings marked for review

### Milestone 3: Contract validator

- [x] resolve environment and overlay inheritance
- [x] resolve provider profiles by environment
- [x] validate required values by service and phase when `--service` is supplied
- [x] validate runtime dependency outputs
- [x] validate file-secret shape, including JSON parsing for Google credentials during materialization
- [x] detect duplicate variables with conflicting source or meaning
- [x] produce actionable errors before setup, build, runtime, or deploy for implemented structural checks

### Milestone 4: Materializer

- [x] generate service-specific `.env` files
- [x] mount file secrets at declared repo-compatible paths beneath an explicit confined mount root
- [x] inject pointer env vars such as `GOOGLE_APPLICATION_CREDENTIALS`
- [x] avoid writing file-secret contents into `.env` files when a mount is required
- [x] support dry-run output for CI review

### Milestone 5: Dynamic registry and scoped ephemeral values

- [x] accept service outputs from ephemeral containers through `publish`
- [x] assign every publisher a unique instance identity
- [x] store generated values by environment, overlay, sandbox, task, service, and instance scope
- [ ] support primary leases and optional replicas — partial: primary conflict/takeover behavior exists; replica policy enforcement does not
- [ ] mark outputs stale when leases expire — expired values are excluded and can be marked `expired`, but renewable lease/stale semantics remain
- [ ] apply TTL-based deletion for task-scoped and sandbox-scoped values — TTL expiry exists; deletion and tombstone retention do not
- [ ] trigger rebuild, restart, or hot refresh only inside the affected scope
- [x] prevent duplicate instances from silently overwriting primary outputs
- [x] never write generated task-local values back into shared provider secrets

### Milestone 6: Provenance and debugging

- [ ] record source, provider, service, environment, overlay, phase, sandbox, instance, status, and timestamp for every value — partial: resolved values now include the complete provenance record; raw registry events still lack provider and phase
- [x] provide `doctor`, `trace`, `events`, and `diff` commands
- [ ] surface missing, malformed, stale, and conflicting values — partial: malformed declarations and source conflicts are surfaced; resolved-value and stale diagnostics remain incomplete
- [x] include actionable remediation hints in validation errors for implemented checks

### Milestone 7: Provider adapters and guided mapping

- [x] GCP Secret Manager metadata listing and on-demand payload retrieval
- [ ] Cloudflare secrets
- [x] Doppler metadata listing and on-demand payload retrieval
- [ ] Vault
- [ ] AWS Secrets Manager
- [ ] Kubernetes Secrets
- [ ] local encrypted files for development — a plaintext local `.env` metadata adapter exists, but encrypted storage does not
- [ ] list provider resources and metadata for selected environments — partial: explicit provider profiles can be listed/synced; environment-driven selection is incomplete
- [x] suggest bindings between discovered env vars and provider secrets
- [x] assign confidence scores and evidence to every suggestion
- [ ] require approval for low-confidence or production-impacting mappings — partial: non-high-confidence mappings require explicit `approve --all`; production-impact policy is not implemented

### Milestone 8: Enterprise policy and governance

- [ ] support required reviewers for contract changes
- [ ] detect accidental secret exposure in public/build variables
- [ ] enforce prod-only restrictions and approval policies
- [ ] export audit logs for security teams
- [ ] integrate with CI status checks

## First usable workflow

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

This local metadata workflow is implemented. `suggest` persists candidates in `.agent-vars/suggestions.json`; `approve` accepts high-confidence candidates by default; and `sync` records resource availability and approved binding status without storing provider secret values.

## Success criteria

- developers can see every required env var and file secret across a repo
- platforms can fail early before expensive builds or deploys
- contracts are generated and synchronized from repo/provider evidence instead of handwritten from scratch
- contracts reflect real repo topology instead of hard-coded example roots
- Vite, Next.js, API gateways, mobile gateways, and microservices can each use their own correct env names
- runtime dependencies such as NATS, Redis, MongoDB, and Postgres are represented explicitly
- Google JSON credentials can be mounted as files at repo-compatible paths
- task-local and sandbox-local values expire without mutating shared secret managers
- duplicate service instances cannot silently overwrite primary dynamic outputs
- provenance makes every env error traceable to service, source, provider, phase, sandbox, and instance
