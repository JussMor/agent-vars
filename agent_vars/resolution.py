from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import os

from .providers import get_secret
from .registry import Registry


LAYER_ORDER = (
    "task_override",
    "sandbox_override",
    "overlay_value",
    "environment_value",
    "provider_secret",
    "repo_default",
)


@dataclass(frozen=True)
class ResolvedValue:
    name: str
    value: str | None
    source: str
    layer: str
    service: str
    environment: str | None
    overlay: str | None
    phase: str | None
    provider: str | None
    sandbox: str | None
    task: str | None
    instance: str | None
    status: str
    resolved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def provenance(self, *, reveal: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not reveal and data["value"] is not None:
            data["value"] = "<redacted>"
        return data


def resolve_service(
    contract: dict[str, Any],
    service_name: str,
    *,
    environment: str | None,
    overlay: str | None,
    sandbox: str | None = None,
    task: str | None = None,
    layers: dict[str, Any] | None = None,
    registry: Registry | None = None,
) -> dict[str, ResolvedValue]:
    service = (contract.get("services") or {}).get(service_name)
    if not isinstance(service, dict):
        raise ValueError(f"unknown service: {service_name}")
    layers = _effective_layers(contract, environment, overlay, layers or {})
    registry = registry or Registry()
    result: dict[str, ResolvedValue] = {}
    for requirement in service.get("requires", []):
        if not isinstance(requirement, dict) or not requirement.get("name"):
            continue
        resolved = _resolve_requirement(
            contract,
            service_name,
            requirement,
            environment=environment,
            overlay=overlay,
            sandbox=sandbox,
            task=task,
            layers=layers,
            registry=registry,
        )
        result[resolved.name] = resolved
    return result


def missing_required(service: dict[str, Any], resolved: dict[str, ResolvedValue], phase: str | None = None) -> list[str]:
    missing = []
    for requirement in service.get("requires", []):
        if not isinstance(requirement, dict) or not requirement.get("required", True):
            continue
        if phase and requirement.get("phase") != phase:
            continue
        item = resolved.get(str(requirement.get("name")))
        if item is None or item.status != "resolved":
            missing.append(str(requirement.get("name")))
    return missing


def _resolve_requirement(
    contract: dict[str, Any],
    service_name: str,
    requirement: dict[str, Any],
    *,
    environment: str | None,
    overlay: str | None,
    sandbox: str | None,
    task: str | None,
    layers: dict[str, Any],
    registry: Registry,
) -> ResolvedValue:
    name = str(requirement["name"])
    source = str(requirement.get("source", name))
    common = {
        "name": name,
        "source": source,
        "service": service_name,
        "environment": environment,
        "overlay": overlay,
        "phase": requirement.get("phase"),
        "sandbox": sandbox,
        "task": task,
    }
    if source.startswith("file."):
        path = _file_mount_path(contract, source)
        return ResolvedValue(value=path, layer="file_mount", provider=None, instance=None, status="resolved" if path else "missing", **common)
    if source.startswith("service."):
        event = registry.resolve_scoped(
            source,
            environment=environment or "default",
            overlay=overlay,
            sandbox=sandbox,
            task=task,
        )
        return ResolvedValue(
            value=event.get("value") if event else None,
            layer="registry",
            provider=None,
            instance=event.get("instance") if event else None,
            status="resolved" if event else "missing",
            **common,
        )

    for layer in LAYER_ORDER:
        value = _layer_value(layers.get(layer), name, service_name)
        if value is not None:
            return ResolvedValue(value=str(value), layer=layer, provider=None, instance=None, status="resolved", **common)

    runtime_env = _runtime_env_name(contract, source)
    if runtime_env and runtime_env in os.environ:
        return ResolvedValue(value=os.environ[runtime_env], layer="process_environment", provider=None, instance=None, status="resolved", **common)
    if name in os.environ:
        return ResolvedValue(value=os.environ[name], layer="process_environment", provider=None, instance=None, status="resolved", **common)

    provider_name, secret_name = _provider_binding(contract, environment, source, name)
    if provider_name and secret_name:
        provider = (contract.get("providers") or {}).get(provider_name)
        if isinstance(provider, dict):
            try:
                value = get_secret(provider_name, provider, secret_name)
            except Exception:
                value = None
            if value is not None:
                return ResolvedValue(value=value, layer="provider_secret", provider=provider_name, instance=None, status="resolved", **common)
    return ResolvedValue(value=None, layer="unresolved", provider=provider_name, instance=None, status="missing", **common)


def _layer_value(layer: Any, name: str, service: str) -> Any:
    if not isinstance(layer, dict):
        return None
    service_values = layer.get(service)
    if isinstance(service_values, dict) and name in service_values:
        return service_values[name]
    return layer.get(name)


def _effective_layers(contract: dict[str, Any], environment: str | None, overlay: str | None, supplied: dict[str, Any]) -> dict[str, Any]:
    env = (contract.get("environments") or {}).get(environment, {}) if environment else {}
    overlay_spec = (contract.get("overlays") or {}).get(overlay, {}) if overlay else {}
    built_in = {
        "repo_default": contract.get("defaults", {}),
        "environment_value": env.get("values", {}) if isinstance(env, dict) else {},
        "overlay_value": overlay_spec.get("values", {}) if isinstance(overlay_spec, dict) else {},
    }
    result: dict[str, Any] = {}
    for layer in LAYER_ORDER:
        base = built_in.get(layer, {})
        override = supplied.get(layer, {})
        if isinstance(base, dict) and isinstance(override, dict):
            result[layer] = _deep_merge(base, override)
        else:
            result[layer] = override or base
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _runtime_env_name(contract: dict[str, Any], source: str) -> str | None:
    parts = source.split(".")
    if len(parts) != 3 or parts[0] != "runtime":
        return None
    dependency = (contract.get("runtime_dependencies") or {}).get(parts[1], {})
    outputs = dependency.get("outputs", {}) if isinstance(dependency, dict) else {}
    value = outputs.get(parts[2]) if isinstance(outputs, dict) else None
    return str(value) if value else None


def _file_mount_path(contract: dict[str, Any], source: str) -> str | None:
    parts = source.split(".")
    if len(parts) != 4:
        return None
    item = (contract.get("files") or {}).get(parts[1], {})
    mount = item.get("mount", {}) if isinstance(item, dict) else {}
    value = mount.get("path") if isinstance(mount, dict) else None
    return str(value) if value else None


def _provider_binding(contract: dict[str, Any], environment: str | None, source: str, name: str) -> tuple[str | None, str | None]:
    env = (contract.get("environments") or {}).get(environment, {}) if environment else {}
    provider_name = env.get("provider_profile") if isinstance(env, dict) else None
    providers = contract.get("providers") or {}
    if source.split(".", 1)[0] in providers:
        explicit, secret = source.split(".", 1)
        return explicit, secret
    if not provider_name:
        return None, None
    if ".secret_manager." in source:
        return str(provider_name), source.split(".secret_manager.", 1)[1]
    return str(provider_name), name
