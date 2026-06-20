from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contract import as_json, contract_summary, load_contract, materialize_service, validate_contract
from .providers import list_secrets, suggest_bindings
from .registry import Registry
from .scanner import scan_repo
from .workflow import DEFAULT_STATE_DIR, approve_suggestions, sync_provider_metadata, write_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-vars", description="Validate and materialize Agent Vars contracts")
    parser.add_argument("--contract", default="agent-vars.yaml", help="Path to agent-vars YAML contract")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan a repository or summarize an existing contract")
    scan.add_argument("--repo", default=".", help="Repository root to scan when --discover is set")
    scan.add_argument("--discover", action="store_true", help="Discover a draft contract from repository evidence")
    scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    scan.add_argument("--out", help="Write discovered contract JSON (valid YAML) to this path")

    validate = sub.add_parser("validate", help="Validate a contract")
    validate.add_argument("--environment", help="Environment to validate")
    validate.add_argument("--overlay", help="Overlay to validate")

    materialize = sub.add_parser("materialize", help="Render a service .env file")
    materialize.add_argument("service", help="Service name")
    materialize.add_argument("--out", help="Output file path; defaults to stdout")
    materialize.add_argument("--dry-run", action="store_true", help="Print instead of writing --out")
    materialize.add_argument("--environment", help="Environment used to validate materialization scope")
    materialize.add_argument("--overlay", help="Overlay used to validate materialization scope")

    suggest = sub.add_parser("suggest", help="Suggest provider bindings for required variables")
    suggest.add_argument("--provider", required=True, help="Provider profile name from the contract")
    suggest.add_argument("--out", default=str(DEFAULT_STATE_DIR / "suggestions.json"), help="Suggestion state file")

    approve = sub.add_parser("approve", help="Approve saved provider suggestions")
    approve.add_argument("--suggestions", default=str(DEFAULT_STATE_DIR / "suggestions.json"))
    approve.add_argument("--out", default=str(DEFAULT_STATE_DIR / "approvals.json"))
    approve.add_argument("--all", action="store_true", help="Also approve medium/low-confidence mappings")

    sync = sub.add_parser("sync", help="Synchronize approved bindings with provider metadata")
    sync.add_argument("--provider", required=True, help="Provider profile name from the contract")
    sync.add_argument("--approvals", default=str(DEFAULT_STATE_DIR / "approvals.json"))
    sync.add_argument("--out", help="Sync state file; defaults to .agent-vars/sync-<provider>.json")

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

    trace = sub.add_parser("trace", help="Trace the latest scoped runtime value")
    trace.add_argument("key")
    trace.add_argument("--environment")
    trace.add_argument("--overlay")
    trace.add_argument("--sandbox")
    trace.add_argument("--task")
    trace.add_argument("--service")

    events = sub.add_parser("events", help="List registry events")
    events.add_argument("--environment")
    events.add_argument("--overlay")
    events.add_argument("--sandbox")
    events.add_argument("--task")
    events.add_argument("--service")

    sub.add_parser("expire", help="Expire registry values whose TTL elapsed")

    doctor = sub.add_parser("doctor", help="Validate and show a service's required variables")
    doctor.add_argument("service")
    doctor.add_argument("--environment")
    doctor.add_argument("--overlay")

    diff = sub.add_parser("diff", help="Diff provider profiles used by two environments")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--service")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contract_path = Path(args.contract)
    try:
        if args.command == "scan" and args.discover:
            discovered = scan_repo(Path(args.repo))
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
            print("contract valid")
            return 0

        if args.command == "materialize":
            issues = validate_contract(contract, environment=args.environment, overlay=args.overlay)
            if any(issue.level == "error" for issue in issues):
                for issue in issues:
                    print(issue.render(), file=sys.stderr)
                return 1
            rendered = materialize_service(contract, args.service)
            if args.out and not args.dry_run:
                Path(args.out).write_text(rendered, encoding="utf-8")
            else:
                sys.stdout.write(rendered)
            return 0

        if args.command == "suggest":
            providers = contract.get("providers", {})
            provider = providers.get(args.provider) if isinstance(providers, dict) else None
            if not isinstance(provider, dict):
                raise ValueError(f"unknown provider: {args.provider}")
            suggestions = suggest_bindings(contract, args.provider, list_secrets(args.provider, provider))
            write_state(Path(args.out), suggestions)
            sys.stdout.write(as_json(suggestions))
            return 0

        if args.command == "approve":
            approvals = approve_suggestions(Path(args.suggestions), Path(args.out), approve_all=args.all)
            sys.stdout.write(as_json(approvals))
            return 0

        if args.command == "sync":
            providers = contract.get("providers", {})
            provider = providers.get(args.provider) if isinstance(providers, dict) else None
            if not isinstance(provider, dict):
                raise ValueError(f"unknown provider: {args.provider}")
            sync_path = Path(args.out) if args.out else DEFAULT_STATE_DIR / f"sync-{args.provider}.json"
            result = sync_provider_metadata(args.provider, list_secrets(args.provider, provider), Path(args.approvals), sync_path)
            sys.stdout.write(as_json(result))
            return 0

        if args.command == "publish":
            event = Registry().publish(args.key, args.value, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service, instance=args.instance, slot=args.slot, ttl=args.ttl, takeover=args.takeover)
            sys.stdout.write(as_json(event.__dict__))
            return 0

        if args.command == "trace":
            event = Registry().trace(args.key, environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service)
            sys.stdout.write(as_json(event or {}))
            return 0

        if args.command == "events":
            sys.stdout.write(as_json(Registry().events(environment=args.environment, overlay=args.overlay, sandbox=args.sandbox, task=args.task, service=args.service)))
            return 0

        if args.command == "expire":
            print(f"expired: {Registry().expire()}")
            return 0

        if args.command == "doctor":
            issues = validate_contract(contract, environment=args.environment, overlay=args.overlay)
            service = (contract.get("services") or {}).get(args.service)
            sys.stdout.write(as_json({"service": args.service, "issues": [i.__dict__ for i in issues], "requires": service.get("requires", []) if isinstance(service, dict) else []}))
            return 1 if any(i.level == "error" for i in issues) else 0

        if args.command == "diff":
            envs = contract.get("environments", {})
            left = envs.get(args.left, {}) if isinstance(envs, dict) else {}
            right = envs.get(args.right, {}) if isinstance(envs, dict) else {}
            sys.stdout.write(as_json({"left": args.left, "right": args.right, "provider_profile_changed": left.get("provider_profile") != right.get("provider_profile"), "left_provider": left.get("provider_profile"), "right_provider": right.get("provider_profile"), "service": args.service}))
            return 0
    except Exception as exc:
        print(f"agent-vars: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
