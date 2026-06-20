# Agent Vars Enterprise Plan

## Objective

Build a service and CLI that let enterprise repositories declare, validate, materialize, and trace environment configuration across many services, environments, cloud providers, file secrets, runtime dependencies, and ephemeral workspaces.

## Implementation status (2026-06-20)

The roadmap below describes the complete product. Each milestone item is marked individually. `[x]` means implemented and tested; `[ ]` means incomplete, with partial implementations called out explicitly.

- Milestone 1: complete foundation
- Milestone 2: complete
- Milestone 3: complete
- Milestone 4: complete
- Milestone 5: complete
- Milestone 6: complete
- Milestone 7: complete
- Milestone 8: pending

### Production readiness outside Milestone 8

- [x] Resolve and inject actual scalar values using documented task/sandbox/overlay/environment/provider/default precedence
- [x] Fetch provider secret payloads for implemented adapters without persisting them in workflow metadata
- [x] Materialize file-secret payloads atomically, validate shape/size/mode policies, set permissions, track manifests, and safely unmount files
- [x] Expand scanner parsing for Kubernetes, Helm, Terraform outputs, cloud outputs, and CI matrices beyond evidence detection
- [x] Add typed service-output schemas and enforce them before publication
- [x] Add complete provenance to every resolved value and value-aware environment diffs
- [x] Add Vault, AWS Secrets Manager, Kubernetes Secrets, Cloudflare secrets, and encrypted-local provider adapters
- [x] Add integration/end-to-end tests against local provider CLI emulators

All identified non-governance production-readiness items are complete. Reviewer enforcement, public-secret leak policy, audit export, and CI status checks remain exclusively in Milestone 8.

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

- [x] detect service roots in monorepos and multi-repo workspaces
- [x] detect frontend frameworks and public env prefixes such as `VITE_` and `NEXT_PUBLIC_`
- [x] detect env usage in code, including `process.env`, `import.meta.env`, framework config, and build scripts
- [x] detect existing `.env*` files and templates
- [x] detect Docker Compose runtime dependencies such as NATS, Redis, MongoDB, and Postgres
- [x] detect Kubernetes, Helm, Terraform, and cloud output references
- [x] detect CI/CD environment matrices such as `dev`, `qa`, `stage`, `uat`, and `prod`
- [x] detect Dockerfile patterns such as `Dockerfile.server`
- [x] detect existing credential paths such as `/app/gcp-credentials` and `/app/.config/gcloud`
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
- [x] support renewable primary leases and policy-limited optional replicas
- [x] mark outputs stale when leases expire
- [x] apply TTL-based deletion for task-scoped and sandbox-scoped values while retaining value-free tombstones
- [x] queue rebuild, restart, or hot refresh actions only inside the affected scope
- [x] prevent duplicate instances from silently overwriting primary outputs
- [x] never write generated task-local values back into shared provider secrets

### Milestone 6: Provenance and debugging

- [x] record source, provider, service, environment, overlay, phase, sandbox, instance, status, and timestamp for every value
- [x] provide `doctor`, `trace`, `events`, and `diff` commands
- [x] surface missing, malformed, stale, and conflicting values
- [x] include actionable remediation hints in validation errors for implemented checks

### Milestone 7: Provider adapters and guided mapping

- [x] GCP Secret Manager metadata listing and on-demand payload retrieval
- [x] Cloudflare secret metadata adapter (payloads are write-only by platform design)
- [x] Doppler metadata listing and on-demand payload retrieval
- [x] Vault
- [x] AWS Secrets Manager
- [x] Kubernetes Secrets
- [x] local encrypted files for development through `age`
- [x] list provider resources and metadata for selected environments
- [x] suggest bindings between discovered env vars and provider secrets
- [x] assign confidence scores and evidence to every suggestion
- [x] require explicit approval flags for low-confidence and production-impacting mappings

### Milestone 8: Enterprise policy and governance

- [ ] support required reviewers for contract changes
- [ ] detect accidental secret exposure in public/build variables
- [ ] enforce prod-only restrictions and approval policies
- [ ] export audit logs for security teams
- [ ] integrate with CI status checks

## First usable workflow

```bash
agent-vars scan --discover --repo . --out agent-vars.yaml
# review the generated contract and add provider profiles
agent-vars resources --environment dev
agent-vars suggest --environment dev
agent-vars approve
agent-vars sync --environment dev
agent-vars validate --environment dev --overlay preview --service frontend --phase build
agent-vars materialize frontend --environment dev --overlay preview
agent-vars materialize api-gateway --environment dev --overlay preview
agent-vars publish service.api-gateway.primary.url https://api-gateway-abc123.example.dev --environment dev --overlay preview --service api-gateway
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
