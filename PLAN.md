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

The criteria below are acceptance tests for Milestones 1–7. They do not imply the reviewer, security-policy, audit-export, or CI-status guarantees reserved for Milestone 8.

### 1. Developers can see every required environment variable and file secret across a repository

**Status: implemented for declared requirements and supported scanner evidence.**

- `scan --discover` inventories variables from `.env*` files, JavaScript/TypeScript and Python references, package build scripts, framework configuration, Compose, Kubernetes, Helm, Terraform, CI matrices, and credential paths.
- The generated contract groups requirements by service rather than flattening names globally.
- `doctor <service>` shows each requirement, resolution status, source, phase, scope, provider, and remediation hint.
- File-secret requirements appear as first-class `files` entries and pointer variables, not hidden scalar values.

**Pass condition:** a supported reference found in repository evidence appears in the draft contract or `uncertain_mappings`; every approved service requirement appears in `doctor` output.

**Verification:** scanner tests cover code, templates, build scripts, multi-repo workspaces, CI, infrastructure manifests, and credential paths. Contract and doctor tests cover scalar and file-secret requirements.

**Boundary:** unsupported languages or dynamically constructed variable names require explicit contract declarations. The scanner reports evidence; it cannot prove that arbitrary runtime reflection has no additional configuration.

### 2. Platforms can fail early before expensive builds or deploys

**Status: implemented.**

- `validate` rejects malformed contract structure, unknown sources/providers, incompatible overlays, invalid phases/visibility, conflicting variables, invalid output schemas, and invalid file policies.
- `validate --service --phase` resolves actual requirements and fails on missing, stale, malformed, or provider-error values.
- `materialize --strict` stops before writing incomplete service configuration.
- `publish` validates publisher identity, slot-qualified keys, and typed output values before registry mutation.

**Pass condition:** invalid configuration returns a non-zero exit code before the dependent setup, build, runtime, or deploy command starts.

**Verification:** contract, diagnostics, CLI, materializer, and typed-output tests exercise successful and failing paths.

### 3. Contracts are generated and synchronized from repository/provider evidence instead of handwritten from scratch

**Status: implemented with human-reviewed application.**

- `scan --discover` generates a draft contract from repository evidence.
- `resources` lists provider metadata selected directly or through an environment profile.
- `suggest` records candidate bindings with confidence and evidence.
- `approve` persists explicit decisions; low-confidence and production-impacting suggestions require independent opt-in flags.
- `sync` records provider resource availability and approved binding status without persisting payloads.

**Pass condition:** a new repository can produce a reviewable draft and provider-binding state without manually rediscovering every variable or secret resource.

**Verification:** scanner, workflow, provider adapter, environment-selection, and approval-policy tests cover the full metadata workflow.

**Boundary:** synchronization does not silently rewrite the reviewed contract. Contract edits remain an explicit human-controlled step, and secret payloads never enter suggestion, approval, or sync state.

### 4. Contracts reflect real repository topology instead of hard-coded example roots

**Status: implemented.**

- Service roots are derived from env files, Dockerfiles, package roots, framework evidence, and repository-relative paths.
- Repeating `--repo` creates a coordinated multi-repository profile with repository-qualified service and runtime-dependency names.
- The example contract demonstrates schema shape only; generated profiles use scanned roots.

**Pass condition:** discovered services point to paths that exist under the scanned repository roots, and same-named services in different repositories remain distinguishable.

**Verification:** single-repository, monorepo, nested service, and multi-repository scanner tests.

### 5. Vite, Next.js, gateways, and microservices use their own correct environment names

**Status: implemented.**

- Framework detection identifies Vite and Next.js from package metadata.
- Public prefixes such as `VITE_`, `NEXT_PUBLIC_`, and `PUBLIC_` are classified as public build-phase values.
- Requirements are scoped under each service, allowing gateways and microservices to use different names for related values.
- Materialization renders only the selected service's requirements.

**Pass condition:** no service receives another service's variable name unless the contract explicitly declares it; public framework variables retain build-phase/public visibility.

**Verification:** scanner framework tests, service-scoped contract tests, and service-specific materializer tests.

### 6. Runtime dependencies are represented explicitly

**Status: implemented.**

- Compose discovery recognizes NATS, Redis, MongoDB, and Postgres services.
- `runtime_dependencies` maps each dependency to named outputs such as `runtime.nats.url`.
- Contract validation rejects references to undeclared runtime outputs.
- Resolution maps runtime outputs to their declared environment variable names.

**Pass condition:** a service requirement references a declared `runtime.<dependency>.<output>` rather than an unexplained global value.

**Verification:** Compose scanner, contract source validation, and layered resolution tests.

### 7. Google JSON credentials can be mounted as files at repository-compatible paths

**Status: implemented.**

- JSON payloads are fetched in memory or supplied through controlled local automation input.
- Materialization confines declared paths beneath an explicit mount root, validates JSON shape and size policy, writes atomically, and applies owner-only `0400` or `0600` modes.
- `.env` output contains only the pointer path such as `GOOGLE_APPLICATION_CREDENTIALS`, never the JSON payload.
- Mount manifests contain metadata and hashes but no payloads; `unmount` uses those hashes to refuse unsafe deletion of modified files.

**Pass condition:** the mounted file is valid according to policy, exists at the mapped compatible path, has private permissions, and can be safely removed without leaking its contents into `.env` or lifecycle state.

**Verification:** materializer policy, path-confinement, permissions, manifest, modified-file protection, and CLI lifecycle tests.

### 8. Task-local and sandbox-local values expire without mutating shared secret managers

**Status: implemented.**

- Ephemeral publications live only in the scoped runtime registry.
- TTL expiry marks outputs stale, scrubs task/sandbox payloads, and retains value-free tombstones.
- `cleanup` explicitly deletes matching task or sandbox scope.
- Provider adapters expose read/list operations to resolution; registry publication has no provider writeback path.

**Pass condition:** after expiry or cleanup, scoped values no longer resolve, no payload remains in registry history, and no provider secret is changed.

**Verification:** registry expiry, scope cleanup, tombstone, redaction, and provider-state separation tests.

### 9. Duplicate service instances cannot silently overwrite primary outputs

**Status: implemented.**

- Every publisher receives or supplies a unique instance identity.
- One active primary lease is allowed per service scope.
- A second instance receives a conflict unless takeover is explicit.
- Replica publication is checked against `instance_policy.replicas`.
- Leases can be renewed; expired primaries stop resolving.

**Pass condition:** an unapproved second primary publish fails without changing the active value; explicit takeover records the old output as stale.

**Verification:** primary conflict, explicit takeover, multiple-output publisher, replica-limit, renewal, and expiry tests.

### 10. Provenance makes every environment error traceable

**Status: implemented.**

- Resolved values and registry events record source, provider, service, environment, overlay, phase, sandbox, task, instance, status, and timestamp.
- `doctor` distinguishes missing, malformed, stale, provider-error, and conflicting values and provides remediation hints.
- `trace` and `events` expose scoped history while redacting payloads by default.
- `diff --service` compares value fingerprints and provenance without printing secret values.

**Pass condition:** an operator can identify where a value came from, which scope and instance produced it, why resolution failed, and the next corrective action without revealing the value.

**Verification:** diagnostics, provenance, stale trace, event redaction, and value-safe diff tests.

### Acceptance test summary

- Unit and CLI suite: `pytest -q`
- Provider CLI protocol integration: `pytest -q tests/integration`
- Syntax check: `python3 -m compileall -q agent_vars tests`
- Documentation/patch hygiene: `git diff --check`

Passing these checks validates the implemented behavior above. Production governance remains separately gated by Milestone 8.
