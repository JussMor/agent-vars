from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import as_json, contract_summary, load_contract, materialize_service, validate_contract
from .diagnostics import diagnose_service, diff_values, redact_event
from .providers import list_secrets, suggest_bindings
from .registry import Registry, refresh_actions
from .resolution import missing_required, resolve_service
from .scanner import scan_repo, scan_workspace
from .materializer import cleanup_file_secrets, materialize_file_secrets
from .outputs import validate_service_output
from .workflow import DEFAULT_STATE_DIR, approve_suggestions, sync_provider_metadata, write_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-vars", description="Validate and materialize Agent Vars contracts")
    parser.add_argument("--contract", default="agent-vars.yaml", help="Path to agent-vars YAML contract")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan a repository or summarize an existing contract")
    scan.add_argument("--repo", action="append", help="Repository root to scan; repeat for a multi-repo workspace")
    scan.add_argument("--discover", action="store_true", help="Discover a draft contract from repository evidence")
    scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    scan.add_argument("--out", help="Write discovered contract JSON (valid YAML) to this path")

    validate = sub.add_parser("validate", help="Validate a contract")
    validate.add_argument("--environment", help="Environment to validate")
    validate.add_argument("--overlay", help="Overlay to validate")
    validate.add_argument("--service", help="Also resolve required values for this service")
    validate.add_argument("--phase", choices=("setup", "build", "runtime", "hot"))
    validate.add_argument("--values", help="JSON file containing layered values")
    validate.add_argument("--sandbox")
    validate.add_argument("--task")

    materialize = sub.add_parser("materialize", help="Render a service .env file")
    materialize.add_argument("service", help="Service name")
    materialize.add_argument("--out", help="Output file path; defaults to stdout")
    materialize.add_argument("--dry-run", action="store_true", help="Print instead of writing --out")
    materialize.add_argument("--environment", help="Environment used to validate materialization scope")
    materialize.add_argument("--overlay", help="Overlay used to validate materialization scope")
    materialize.add_argument("--sandbox")
    materialize.add_argument("--task")
    materialize.add_argument("--values", help="JSON file containing layered scalar values")
    materialize.add_argument("--strict", action="store_true", help="Fail when a required value is unresolved")
    materialize.add_argument("--file-values", help="JSON object of file-secret payloads, intended for tests/local automation")
    materialize.add_argument("--mount-root", help="Materialize declared file mounts beneath this root")
    materialize.add_argument("--mount-manifest", help="Mount lifecycle manifest; defaults beneath --mount-root")

    unmount = sub.add_parser("unmount", help="Remove tracked file-secret mounts safely")
    unmount.add_argument("service", nargs="?", help="Only remove mounts for this service")
    unmount.add_argument("--mount-root", required=True)
    unmount.add_argument("--mount-manifest", help="Mount lifecycle manifest; defaults beneath --mount-root")
    unmount.add_argument("--force", action="store_true", help="Remove files even when their content changed")

    suggest = sub.add_parser("suggest", help="Suggest provider bindings for required variables")
    suggest_source = suggest.add_mutually_exclusive_group(required=True)
    suggest_source.add_argument("--provider", help="Provider profile name from the contract")
    suggest_source.add_argument("--environment", help="Select the environment's provider profile")
    suggest.add_argument("--out", default=str(DEFAULT_STATE_DIR / "suggestions.json"), help="Suggestion state file")

    approve = sub.add_parser("approve", help="Approve saved provider suggestions")
    approve.add_argument("--suggestions", default=str(DEFAULT_STATE_DIR / "suggestions.json"))
    approve.add_argument("--out", default=str(DEFAULT_STATE_DIR / "approvals.json"))
    approve.add_argument("--all", action="store_true", help="Also approve medium/low-confidence mappings")
    approve.add_argument("--allow-production", action="store_true", help="Explicitly approve production-impacting mappings")

    sync = sub.add_parser("sync", help="Synchronize approved bindings with provider metadata")
    sync_source = sync.add_mutually_exclusive_group(required=True)
    sync_source.add_argument("--provider", help="Provider profile name from the contract")
    sync_source.add_argument("--environment", help="Select the environment's provider profile")
    sync.add_argument("--approvals", default=str(DEFAULT_STATE_DIR / "approvals.json"))
    sync.add_argument("--out", help="Sync state file; defaults to .agent-vars/sync-<provider>.json")

    resources = sub.add_parser("resources", help="List provider resources and metadata")
    resource_source = resources.add_mutually_exclusive_group(required=True)
    resource_source.add_argument("--provider")
    resource_source.add_argument("--environment")

    publish = sub.add_parser("publish", help="Publish a scoped ephemeral runtime value")
    publish.add_argument("key")
    publish.add_argument("value")
    for scoped in (publish,):
        scoped.add_argument("--environment", required=True)
        scoped.add_argument("--overlay")
        scoped.add_argument("--sandbox")
        scoped.add_argument("--task")
        scoped.add_argument("--service")
    publish.add_argument("--instance")
    publish.add_argument("--slot", default="primary")
    publish.add_argument("--ttl", default="2h")
    publish.add_argument("--takeover", action="store_true", help="Explicitly replace another active primary lease")
    publish.add_argument("--max-replicas", type=int, help="Override the service replica limit")
    publish.add_argument("--source", help="Provenance source; defaults to the published key")
    publish.add_argument("--provider", help="Provenance provider profile")
    publish.add_argument("--phase", choices=("setup", "build", "runtime", "hot"), default="runtime")

    renew = sub.add_parser("renew", help="Renew all active outputs for an instance lease")
    renew.add_argument("instance")
    renew.add_argument("--environment", required=True)
    renew.add_argument("--overlay")
    renew.add_argument("--sandbox")
    renew.add_argument("--task")
    renew.add_argument("--service")
    renew.add_argument("--ttl", default="60s")

    trace = sub.add_parser("trace", help="Trace the latest scoped runtime value")
    trace.add_argument("key")
    trace.add_argument("--environment")
    trace.add_argument("--overlay")
    trace.add_argument("--sandbox")
    trace.add_argument("--task")
    trace.add_argument("--service")
    trace.add_argument("--reveal", action="store_true", help="Reveal the stored value")

    events = sub.add_parser("events", help="List registry events")
    events.add_argument("--environment")
    events.add_argument("--overlay")
    events.add_argument("--sandbox")
    events.add_argument("--task")
    events.add_argument("--service")
    events.add_argument("--status")
    events.add_argument("--type")
    events.add_argument("--reveal", action="store_true", help="Reveal stored values")

    actions = sub.add_parser("actions", help="List scope-confined rebuild, restart, and hot-refresh actions")
    actions.add_argument("--environment")
    actions.add_argument("--overlay")
    actions.add_argument("--sandbox")
    actions.add_argument("--task")
    actions.add_argument("--service")

    acknowledge = sub.add_parser("ack-actions", help="Acknowledge queued actions for a publication index")
    acknowledge.add_argument("publication", type=int)

    sub.add_parser("expire", help="Expire registry values whose TTL elapsed")

    cleanup = sub.add_parser("cleanup", help="Delete scoped values and retain audit tombstones")
    cleanup.add_argument("--environment")
    cleanup.add_argument("--overlay")
    cleanup.add_argument("--sandbox")
    cleanup.add_argument("--task")

    doctor = sub.add_parser("doctor", help="Validate and show a service's required variables")
    doctor.add_argument("service")
    doctor.add_argument("--environment")
    doctor.add_argument("--overlay")
    doctor.add_argument("--sandbox")
    doctor.add_argument("--task")
    doctor.add_argument("--values", help="JSON file containing layered values")
    doctor.add_argument("--reveal", action="store_true", help="Include resolved values; unsafe for shared logs")

    diff = sub.add_parser("diff", help="Diff provider profiles used by two environments")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--service")
    diff.add_argument("--overlay")
    diff.add_argument("--sandbox")
    diff.add_argument("--task")
    diff.add_argument("--left-values")
    diff.add_argument("--right-values")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contract_path = Path(args.contract)
    try:
        if args.command == "scan" and args.discover:
            roots = [Path(path) for path in (args.repo or ["."])]
            discovered = scan_workspace(roots) if len(roots) > 1 else scan_repo(roots[0])
            rendered = as_json(discovered)
            if args.out:
                Path(args.out).write_text(rendered, encoding="utf-8")
            if args.json or not args.out:
                sys.stdout.write(rendered)
            return 0

        contract = load_contract(contract_path)
        if args.command == "scan":
            summary = contract_summary(contract)
            if args.json:
                sys.stdout.write(as_json(summary))
            else:
                print(f"project: {contract.get('project', 'unknown')}")
                for key, value in summary.items():
                    print(f"{key}: {value}")
            return 0

        if args.command == "validate":
            issues = validate_contract(contract, environment=args.environment, overlay=args.overlay)
            for issue in issues:
                print(issue.render(), file=sys.stderr if issue.level == "error" else sys.stdout)
            if any(issue.level == "error" for issue in issues):
                return 1
            if args.service:
                service = (contract.get("services") or {}).get(args.service)
                if not isinstance(service, dict):
                    raise ValueError(f"unknown service: {args.service}")
                resolved = resolve_service(
                    contract,
                    args.service,
                    environment=args.environment,
                    overlay=args.overlay,
                    sandbox=args.sandbox,
                    task=args.task,
                    layers=_read_json(args.values),
                )
                missing = missing_required(service, resolved, args.phase)
                diagnostics = [item for item in diagnose_service(contract, args.service, resolved) if not args.phase or item.phase == args.phase]
                if missing or diagnostics:
                    for diagnostic in diagnostics:
                        print(f"{diagnostic.level}: {diagnostic.service}.{diagnostic.variable}: {diagnostic.message} fix: {diagnostic.hint}", file=sys.stderr)
                    return 1
            print("contract valid")
            return 0

        if args.command == "materialize":
            issues = validate_contract(contract, environment=args.environment, overlay=args.overlay)
            if any(issue.level == "error" for issue in issues):
                for issue in issues:
                    print(issue.render(), file=sys.stderr)
                return 1
            resolved = resolve_service(
                contract,
                args.service,
                environment=args.environment,
                overlay=args.overlay,
                sandbox=args.sandbox,
                task=args.task,
                layers=_read_json(args.values),
            )
            service = (contract.get("services") or {}).get(args.service, {})
            missing = missing_required(service, resolved) if isinstance(service, dict) else []
            if args.strict and missing:
                raise ValueError(f"missing required values: {', '.join(missing)}")
            if args.mount_root and not args.dry_run:
                if not args.environment:
                    raise ValueError("--environment is required with --mount-root")
                materialize_file_secrets(
                    contract,
                    args.service,
                    environment=args.environment,
                    mount_root=Path(args.mount_root),
                    payloads=_read_json(args.file_values),
                    manifest_path=Path(args.mount_manifest) if args.mount_manifest else Path(args.mount_root) / ".agent-vars-mounts.json",
                )
            rendered = materialize_service(contract, args.service, resolved)
            if args.out and not args.dry_run:
                Path(args.out).write_text(rendered, encoding="utf-8")
            else:
                sys.stdout.write(rendered)
            return 0

        if args.command == "unmount":
            mount_root = Path(args.mount_root)
            manifest = Path(args.mount_manifest) if args.mount_manifest else mount_root / ".agent-vars-mounts.json"
            removed = cleanup_file_secrets(mount_root, manifest, service_name=args.service, force=args.force)
            sys.stdout.write(as_json({"removed": [str(path) for path in removed]}))
            return 0

        if args.command == "suggest":
            provider_name, provider = _select_provider(contract, args.provider, args.environment)
            suggestions = suggest_bindings(contract, provider_name, list_secrets(provider_name, provider), environment=args.environment)
            write_state(Path(args.out), suggestions)
            sys.stdout.write(as_json(suggestions))
            return 0

        if args.command == "approve":
            approvals = approve_suggestions(Path(args.suggestions), Path(args.out), approve_all=args.all, allow_production=args.allow_production)
            sys.stdout.write(as_json(approvals))
            return 0

        if args.command == "sync":
            provider_name, provider = _select_provider(contract, args.provider, args.environment)
            sync_path = Path(args.out) if args.out else DEFAULT_STATE_DIR / f"sync-{provider_name}.json"
            result = sync_provider_metadata(provider_name, list_secrets(provider_name, provider), Path(args.approvals), sync_path)
            sys.stdout.write(as_json(result))
            return 0

        if args.command == "resources":
            provider_name, provider = _select_provider(contract, args.provider, args.environment)
            sys.stdout.write(as_json([secret.__dict__ for secret in list_secrets(provider_name, provider)]))
            return 0

        if args.command == "publish":
            output_schema = validate_service_output(contract, args.service, args.key, args.value, slot=args.slot)
            replica_limit = args.max_replicas if args.max_replicas is not None else _replica_limit(contract, args.service)
            actions = refresh_actions(contract, args.key, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task)
            phase = str(output_schema.get("phase", args.phase)) if output_schema else args.phase
            event = Registry().publish(args.key, args.value, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service, instance=args.instance, slot=args.slot, ttl=args.ttl, takeover=args.takeover, max_replicas=replica_limit, actions=actions, source=args.source, provider=args.provider, phase=phase)
            sys.stdout.write(as_json(event.__dict__))
            return 0

        if args.command == "renew":
            renewed = Registry().renew(args.instance, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service, ttl=args.ttl)
            print(f"renewed: {renewed}")
            return 0

        if args.command == "trace":
            event = Registry().diagnose(args.key, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service)
            sys.stdout.write(as_json(redact_event(event, reveal=args.reveal) if event else {}))
            return 0

        if args.command == "events":
            events_result = Registry().events(environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service)
            if args.status:
                events_result = [event for event in events_result if event.get("status") == args.status]
            if args.type:
                events_result = [event for event in events_result if event.get("type") == args.type]
            sys.stdout.write(as_json([redact_event(event, reveal=args.reveal) for event in events_result]))
            return 0

        if args.command == "actions":
            sys.stdout.write(as_json(Registry().actions(environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service)))
            return 0

        if args.command == "ack-actions":
            print(f"acknowledged: {Registry().acknowledge_actions(args.publication)}")
            return 0

        if args.command == "expire":
            print(f"expired: {Registry().expire()}")
            return 0

        if args.command == "cleanup":
            deleted = Registry().cleanup(environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task)
            print(f"deleted: {deleted}")
            return 0

        if args.command == "doctor":
            issues = validate_contract(contract, environment=args.environment, overlay=args.overlay)
            service = (contract.get("services") or {}).get(args.service)
            if not isinstance(service, dict):
                raise ValueError(f"unknown service: {args.service}")
            resolved = resolve_service(
                contract,
                args.service,
                environment=args.environment,
                overlay=args.overlay,
                sandbox=args.sandbox,
                task=args.task,
                layers=_read_json(args.values),
            )
            missing = missing_required(service, resolved)
            diagnostics = diagnose_service(contract, args.service, resolved)
            sys.stdout.write(as_json({
                "service": args.service,
                "issues": [i.__dict__ for i in issues],
                "missing": missing,
                "diagnostics": [item.as_dict() for item in diagnostics],
                "values": {name: value.provenance(reveal=args.reveal) for name, value in resolved.items()},
            }))
            return 1 if any(i.level == "error" for i in issues) or diagnostics else 0

        if args.command == "diff":
            envs = contract.get("environments", {})
            left = envs.get(args.left, {}) if isinstance(envs, dict) else {}
            right = envs.get(args.right, {}) if isinstance(envs, dict) else {}
            result = {"left": args.left, "right": args.right, "provider_profile_changed": left.get("provider_profile") != right.get("provider_profile"), "left_provider": left.get("provider_profile"), "right_provider": right.get("provider_profile"), "service": args.service}
            if args.service:
                left_resolved = resolve_service(contract, args.service, environment=args.left, overlay=args.overlay, sandbox=args.sandbox, task=args.task, layers=_read_json(args.left_values))
                right_resolved = resolve_service(contract, args.service, environment=args.right, overlay=args.overlay, sandbox=args.sandbox, task=args.task, layers=_read_json(args.right_values))
                result["values"] = diff_values(left_resolved, right_resolved)
            sys.stdout.write(as_json(result))
            return 0
    except Exception as exc:
        print(f"agent-vars: {exc}", file=sys.stderr)
        return 2
    return 2


def _read_json(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def _replica_limit(contract: dict[str, object], service_name: str | None) -> int | None:
    service = (contract.get("services") or {}).get(service_name, {}) if service_name else {}
    policy = service.get("instance_policy", {}) if isinstance(service, dict) else {}
    replicas = policy.get("replicas", 0) if isinstance(policy, dict) else 0
    if replicas == "many":
        return None
    try:
        return max(0, int(replicas))
    except (TypeError, ValueError):
        return 0


def _select_provider(contract: dict[str, object], provider_name: str | None, environment: str | None) -> tuple[str, dict[str, object]]:
    if environment:
        environments = contract.get("environments", {})
        env = environments.get(environment) if isinstance(environments, dict) else None
        if not isinstance(env, dict):
            raise ValueError(f"unknown environment: {environment}")
        provider_name = env.get("provider_profile")
        if not provider_name:
            raise ValueError(f"environment has no provider profile: {environment}")
    providers = contract.get("providers", {})
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    if not isinstance(provider_name, str) or not isinstance(provider, dict):
        raise ValueError(f"unknown provider: {provider_name}")
    return provider_name, provider


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
