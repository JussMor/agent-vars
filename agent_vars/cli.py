from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contract import as_json, contract_summary, load_contract, materialize_service, validate_contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-vars", description="Validate and materialize Agent Vars contracts")
    parser.add_argument("--contract", default="agent-vars.yaml", help="Path to agent-vars YAML contract")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Summarize an existing contract as the first coding-phase scanner")
    scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    validate = sub.add_parser("validate", help="Validate a contract")
    validate.add_argument("--environment", help="Environment to validate")
    validate.add_argument("--overlay", help="Overlay to validate")

    materialize = sub.add_parser("materialize", help="Render a service .env file")
    materialize.add_argument("service", help="Service name")
    materialize.add_argument("--out", help="Output file path; defaults to stdout")
    materialize.add_argument("--dry-run", action="store_true", help="Print instead of writing --out")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contract_path = Path(args.contract)
    try:
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
            rendered = materialize_service(contract, args.service)
            if args.out and not args.dry_run:
                Path(args.out).write_text(rendered, encoding="utf-8")
            else:
                sys.stdout.write(rendered)
            return 0
    except Exception as exc:
        print(f"agent-vars: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
