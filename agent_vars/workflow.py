from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


DEFAULT_STATE_DIR = Path(".agent-vars")


def write_state(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_state(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def approved_binding_map(path: Path, *, environment: str | None = None) -> dict[tuple[str, str], str]:
    approvals = read_state(path, [])
    if not isinstance(approvals, list):
        raise ValueError(f"approvals file must contain a JSON list: {path}")
    bindings: dict[tuple[str, str], str] = {}
    for item in approvals:
        if not isinstance(item, dict) or item.get("status") != "approved":
            continue
        item_environment = item.get("environment")
        if item_environment and item_environment != environment:
            continue
        service = item.get("service")
        required = item.get("required")
        source = item.get("suggested_source")
        if all(isinstance(value, str) and value for value in (service, required, source)):
            bindings[(service, required)] = source
    return bindings


def approve_suggestions(
    suggestions_path: Path,
    approvals_path: Path,
    *,
    approve_all: bool = False,
    allow_production: bool = False,
) -> list[dict[str, Any]]:
    suggestions = read_state(suggestions_path, None)
    if suggestions is None:
        raise ValueError(f"suggestions not found: {suggestions_path}; run agent-vars suggest first")
    if not isinstance(suggestions, list):
        raise ValueError(f"suggestions file must contain a JSON list: {suggestions_path}")

    approved = read_state(approvals_path, [])
    if not isinstance(approved, list):
        approved = []
    by_binding = {(item.get("environment"), item.get("service"), item.get("required")): item for item in approved if isinstance(item, dict)}
    timestamp = datetime.now(timezone.utc).isoformat()
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        if not suggestion.get("suggested_source"):
            continue
        if not approve_all and suggestion.get("confidence") != "high":
            continue
        if suggestion.get("production_impact") and not allow_production:
            continue
        item = dict(suggestion)
        item["status"] = "approved"
        item["approved_at"] = timestamp
        item.pop("action", None)
        by_binding[(item.get("environment"), item.get("service"), item.get("required"))] = item
    result = sorted(by_binding.values(), key=lambda item: (str(item.get("environment")), str(item.get("service")), str(item.get("required"))))
    write_state(approvals_path, result)
    return result


def sync_provider_metadata(
    provider_name: str,
    secrets: list[Any],
    approvals_path: Path,
    sync_path: Path,
) -> dict[str, Any]:
    approvals = read_state(approvals_path, [])
    if not isinstance(approvals, list):
        raise ValueError(f"approvals file must contain a JSON list: {approvals_path}")
    resources = [
        {"name": secret.name, "kind": secret.kind, "metadata": secret.metadata}
        for secret in secrets
    ]
    names = {resource["name"] for resource in resources}
    bindings = []
    for approval in approvals:
        if not isinstance(approval, dict) or approval.get("provider") != provider_name:
            continue
        source = str(approval.get("suggested_source", ""))
        secret_name = source.removeprefix(f"{provider_name}.")
        binding = dict(approval)
        binding["status"] = "available" if secret_name in names else "missing"
        bindings.append(binding)
    result = {
        "provider": provider_name,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "resources": resources,
        "bindings": bindings,
    }
    write_state(sync_path, result)
    return result
