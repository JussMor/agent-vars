# Agent Vars Enterprise Plan

## Objective

Build a service and CLI that let enterprise repositories declare, validate, materialize, and trace environment configuration across many services, environments, cloud providers, file secrets, runtime dependencies, and ephemeral workspaces.

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

- define `agent-vars.yaml`
- support `repo`, `environments`, `overlays`, `providers`, `runtime_dependencies`, `files`, and `services`
- support service-level `env_files`, `requires`, `instance_policy`, `framework`, and `discovered_from`
- distinguish `secret`, `file-secret`, `public`, and `internal` visibility
- distinguish `setup`, `build`, `runtime`, and `hot` phases

### Milestone 2: Repo scanner and profile generator

- detect service roots in monorepos and multi-repo workspaces
- detect frontend frameworks and public env prefixes such as `VITE_` and `NEXT_PUBLIC_`
- detect env usage in code, including `process.env`, `import.meta.env`, framework config, and build scripts
- detect existing `.env*` files and templates
- detect Docker Compose runtime dependencies such as NATS, Redis, MongoDB, and Postgres
- detect Kubernetes, Helm, Terraform, and cloud output references
- detect CI/CD environment matrices such as `dev`, `qa`, `stage`, `uat`, and `prod`
- detect Dockerfile patterns such as `Dockerfile.server`
- detect existing credential paths such as `/app/gcp-credentials` and `/app/.config/gcloud`
- produce a draft contract with uncertain mappings marked for review

### Milestone 3: Contract validator

- resolve environment and overlay inheritance
- resolve provider profiles by environment
- validate required values by service and phase
- validate runtime dependency outputs
- validate file-secret shape, including JSON parsing for Google credentials
- detect duplicate variables with conflicting source or meaning
- produce actionable errors before setup, build, runtime, or deploy

### Milestone 4: Materializer

- generate service-specific `.env` files
- mount file secrets at declared repo-compatible paths
- inject pointer env vars such as `GOOGLE_APPLICATION_CREDENTIALS`
- avoid writing file-secret contents into `.env` files when a mount is required
- support dry-run output for CI review

### Milestone 5: Dynamic registry and scoped ephemeral values

- accept service outputs from ephemeral containers
- assign every publisher a unique instance identity
- store generated values by environment, overlay, sandbox, task, service, and instance scope
- support primary leases and optional replicas
- mark outputs stale when leases expire
- apply TTL-based deletion for task-scoped and sandbox-scoped values
- trigger rebuild, restart, or hot refresh only inside the affected scope
- prevent duplicate instances from silently overwriting primary outputs
- never write generated task-local values back into shared provider secrets

### Milestone 6: Provenance and debugging

- record source, provider, service, environment, overlay, phase, sandbox, instance, status, and timestamp for every value
- provide `doctor`, `trace`, `events`, and `diff` commands
- surface missing, malformed, stale, and conflicting values
- include actionable remediation hints in validation errors

### Milestone 7: Provider adapters and guided mapping

- GCP Secret Manager
- Cloudflare secrets
- Doppler
- Vault
- AWS Secrets Manager
- Kubernetes Secrets
- local encrypted files for development
- list provider resources and metadata for selected environments
- suggest bindings between discovered env vars and provider secrets
- assign confidence scores and evidence to every suggestion
- require approval for low-confidence or production-impacting mappings

### Milestone 8: Enterprise policy and governance

- support required reviewers for contract changes
- detect accidental secret exposure in public/build variables
- enforce prod-only restrictions and approval policies
- export audit logs for security teams
- integrate with CI status checks

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
