from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re
import subprocess


class ContractError(ValueError):
    """Raised when a contract cannot be loaded or validated."""


PROVIDER_KINDS = {"gcp", "cloudflare", "doppler", "vault", "aws", "kubernetes", "vercel", "local", "local-encrypted"}
OUTPUT_TYPES = {"string", "url", "integer", "number", "boolean", "json"}


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    path: str
    message: str
    hint: str | None = None

    def render(self) -> str:
        hint = f" fix: {self.hint}" if self.hint else ""
        return f"{self.level}: {self.path}: {self.message}{hint}"


def load_contract(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ContractError(f"contract not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        data = _load_yaml_with_ruby(path)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ContractError("contract root must be a YAML mapping")
    return data


def validate_contract(contract: dict[str, Any], *, environment: str | None = None, overlay: str | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    _require(contract, "version", int, issues)
    _require(contract, "project", str, issues)
    services = _require(contract, "services", dict, issues) or {}
    envs = _require(contract, "environments", dict, issues) or {}
    providers = contract.get("providers", {})
    if providers is not None and not isinstance(providers, dict):
        issues.append(ValidationIssue("error", "providers", "providers must be a mapping"))
        providers = {}
    if isinstance(providers, dict):
        for provider_name, provider in providers.items():
            provider_path = f"providers.{provider_name}"
            if not isinstance(provider, dict):
                issues.append(ValidationIssue("error", provider_path, "provider must be a mapping"))
                continue
            kind = provider.get("kind")
            if kind not in PROVIDER_KINDS:
                issues.append(ValidationIssue("error", f"{provider_path}.kind", f"unsupported provider kind {kind!r}", f"choose one of {', '.join(sorted(PROVIDER_KINDS))}"))
            required_key = {"gcp": "project_id", "vault": "path", "local": "path", "local-encrypted": "path"}.get(kind)
            if required_key and not provider.get(required_key) and not provider.get("fixture"):
                issues.append(ValidationIssue("error", f"{provider_path}.{required_key}", "required provider setting is missing", f"set {required_key} or configure a deterministic fixture"))

    for env_name, env in envs.items():
        if not isinstance(env, dict):
            issues.append(ValidationIssue("error", f"environments.{env_name}", "environment must be a mapping"))
            continue
        profile = env.get("provider_profile")
        if profile and profile not in providers:
            issues.append(ValidationIssue("error", f"environments.{env_name}.provider_profile", f"unknown provider profile {profile!r}", "declare it under providers"))

    if environment and environment not in envs:
        issues.append(ValidationIssue("error", f"environments.{environment}", "environment is not declared", "add it under environments or choose a declared environment"))
    if overlay:
        overlays = contract.get("overlays", {})
        if not isinstance(overlays, dict) or overlay not in overlays:
            issues.append(ValidationIssue("error", f"overlays.{overlay}", "overlay is not declared", "add it under overlays or omit --overlay"))
        else:
            parent = overlays[overlay].get("inherits") if isinstance(overlays[overlay], dict) else None
            if parent and parent not in envs:
                issues.append(ValidationIssue("error", f"overlays.{overlay}.inherits", f"inherits unknown environment {parent!r}", "point inherits at a declared environment"))
            if environment and parent and environment != parent:
                issues.append(ValidationIssue("error", f"overlays.{overlay}.inherits", f"overlay inherits {parent!r}, not selected environment {environment!r}", "select the inherited environment or use a compatible overlay"))

    files = contract.get("files", {})
    if files is not None and not isinstance(files, dict):
        issues.append(ValidationIssue("error", "files", "files must be a mapping"))
    elif isinstance(files, dict):
        for file_name, item in files.items():
            file_path = f"files.{file_name}"
            if not isinstance(item, dict):
                issues.append(ValidationIssue("error", file_path, "file secret must be a mapping"))
                continue
            if item.get("format") not in {"json", "text", "binary"}:
                issues.append(ValidationIssue("error", f"{file_path}.format", "format must be json, text, or binary"))
            mount = item.get("mount")
            if not isinstance(mount, dict) or not isinstance(mount.get("path"), str) or not mount.get("path"):
                issues.append(ValidationIssue("error", f"{file_path}.mount.path", "mount path is required"))
            policy = item.get("policy", {})
            if not isinstance(policy, dict):
                issues.append(ValidationIssue("error", f"{file_path}.policy", "policy must be a mapping"))
            else:
                if policy.get("mode", "0600") not in {"0400", "0600"}:
                    issues.append(ValidationIssue("error", f"{file_path}.policy.mode", "mode must be 0400 or 0600", "use a private owner-only file mode"))
                if policy.get("max_bytes") is not None and (not isinstance(policy["max_bytes"], int) or policy["max_bytes"] <= 0):
                    issues.append(ValidationIssue("error", f"{file_path}.policy.max_bytes", "max_bytes must be a positive integer"))
                required_keys = policy.get("required_json_keys", [])
                if not isinstance(required_keys, list) or not all(isinstance(key, str) for key in required_keys):
                    issues.append(ValidationIssue("error", f"{file_path}.policy.required_json_keys", "required_json_keys must be a list of strings"))

    runtime_outputs = _collect_outputs(contract.get("runtime_dependencies", {}), "runtime")
    file_outputs = _collect_file_outputs(contract.get("files", {}))
    service_names = set(services) if isinstance(services, dict) else set()

    if isinstance(services, dict):
        seen_vars: dict[str, str] = {}
        for service_name, service in services.items():
            path = f"services.{service_name}"
            if not isinstance(service, dict):
                issues.append(ValidationIssue("error", path, "service must be a mapping"))
                continue
            _require(service, "root", str, issues, path)
            outputs = service.get("outputs", {})
            if not isinstance(outputs, dict):
                issues.append(ValidationIssue("error", f"{path}.outputs", "outputs must be a mapping"))
            else:
                for output_name, output in outputs.items():
                    output_path = f"{path}.outputs.{output_name}"
                    if not isinstance(output, dict):
                        issues.append(ValidationIssue("error", output_path, "output schema must be a mapping"))
                        continue
                    if output.get("type", "string") not in OUTPUT_TYPES:
                        issues.append(ValidationIssue("error", f"{output_path}.type", "unsupported output type", f"choose one of {', '.join(sorted(OUTPUT_TYPES))}"))
                    if output.get("phase", "runtime") not in {"setup", "build", "runtime", "hot"}:
                        issues.append(ValidationIssue("error", f"{output_path}.phase", "phase must be setup, build, runtime, or hot"))
                    if output.get("visibility", "internal") not in {"secret", "file-secret", "public", "internal"}:
                        issues.append(ValidationIssue("error", f"{output_path}.visibility", "visibility must be secret, file-secret, public, or internal"))
                    if output.get("enum") is not None and not isinstance(output["enum"], list):
                        issues.append(ValidationIssue("error", f"{output_path}.enum", "enum must be a list"))
                    if output.get("max_length") is not None and (not isinstance(output["max_length"], int) or output["max_length"] <= 0):
                        issues.append(ValidationIssue("error", f"{output_path}.max_length", "max_length must be a positive integer"))
                    if output.get("pattern") is not None:
                        try:
                            re.compile(str(output["pattern"]))
                        except re.error:
                            issues.append(ValidationIssue("error", f"{output_path}.pattern", "pattern must be a valid regular expression"))
            requires = service.get("requires", [])
            if not isinstance(requires, list):
                issues.append(ValidationIssue("error", f"{path}.requires", "requires must be a list"))
                continue
            for index, req in enumerate(requires):
                req_path = f"{path}.requires[{index}]"
                if not isinstance(req, dict):
                    issues.append(ValidationIssue("error", req_path, "requirement must be a mapping"))
                    continue
                name = _require(req, "name", str, issues, req_path)
                source = _require(req, "source", str, issues, req_path)
                phase = req.get("phase")
                visibility = req.get("visibility")
                if phase not in {"setup", "build", "runtime", "hot"}:
                    issues.append(ValidationIssue("error", f"{req_path}.phase", "phase must be setup, build, runtime, or hot"))
                if visibility not in {"secret", "file-secret", "public", "internal"}:
                    issues.append(ValidationIssue("error", f"{req_path}.visibility", "visibility must be secret, file-secret, public, or internal"))
                if name:
                    previous = seen_vars.setdefault(f"{service_name}.{name}", source or "")
                    if source and previous != source:
                        issues.append(ValidationIssue("error", f"{req_path}.source", "same service variable has conflicting sources", "split the variable by service or choose one source"))
                if source:
                    issues.extend(_validate_source(source, req_path, runtime_outputs, file_outputs, service_names))
    return issues


def materialize_service(contract: dict[str, Any], service_name: str, resolved_values: dict[str, Any] | None = None) -> str:
    services = contract.get("services", {})
    service = services.get(service_name) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise ContractError(f"unknown service: {service_name}")
    lines = ["# Generated by agent-vars materialize. Do not commit secrets."]
    for req in service.get("requires", []):
        if not isinstance(req, dict) or not req.get("name"):
            continue
        if req.get("visibility") == "file-secret" and str(req.get("source", "")).startswith("file."):
            value = _file_mount_value(contract, str(req["source"])) or f"${{{req['name']}}}"
        elif resolved_values and req["name"] in resolved_values:
            resolved = resolved_values[req["name"]]
            resolved_value = getattr(resolved, "value", resolved)
            value = _dotenv_value(str(resolved_value)) if resolved_value is not None else f"${{{req['name']}}}"
        else:
            value = f"${{{req['name']}}}"
        lines.append(f"{req['name']}={value}")
    return "\n".join(lines) + "\n"


def _dotenv_value(value: str) -> str:
    if not value or any(char in value for char in "\n\r#\"'") or value != value.strip():
        return json.dumps(value)
    return value


def contract_summary(contract: dict[str, Any]) -> dict[str, int]:
    services = contract.get("services", {}) if isinstance(contract.get("services", {}), dict) else {}
    return {
        "services": len(services),
        "environments": len(contract.get("environments", {}) or {}),
        "overlays": len(contract.get("overlays", {}) or {}),
        "providers": len(contract.get("providers", {}) or {}),
        "requirements": sum(len(s.get("requires", [])) for s in services.values() if isinstance(s, dict)),
    }


def _require(data: dict[str, Any], key: str, kind: type, issues: list[ValidationIssue], prefix: str = "") -> Any:
    path = f"{prefix}.{key}" if prefix else key
    if key not in data:
        issues.append(ValidationIssue("error", path, "required field is missing"))
        return None
    if not isinstance(data[key], kind):
        issues.append(ValidationIssue("error", path, f"must be {kind.__name__}"))
        return None
    return data[key]


def _collect_outputs(section: Any, prefix: str) -> set[str]:
    outputs: set[str] = set()
    if isinstance(section, dict):
        for name, item in section.items():
            if isinstance(item, dict) and isinstance(item.get("outputs"), dict):
                outputs.update(f"{prefix}.{name}.{key}" for key in item["outputs"])
    return outputs


def _collect_file_outputs(files: Any) -> set[str]:
    outputs: set[str] = set()
    if isinstance(files, dict):
        for name, item in files.items():
            if isinstance(item, dict) and isinstance(item.get("mount"), dict):
                outputs.add(f"file.{name}.mount.path")
    return outputs


def _validate_source(source: str, path: str, runtime_outputs: set[str], file_outputs: set[str], service_names: set[str]) -> list[ValidationIssue]:
    if source.startswith("runtime.") and source not in runtime_outputs:
        return [ValidationIssue("error", f"{path}.source", f"unknown runtime output {source!r}", "declare it under runtime_dependencies outputs")]
    if source.startswith("file.") and source not in file_outputs:
        return [ValidationIssue("error", f"{path}.source", f"unknown file mount {source!r}", "declare it under files with mount.path")]
    if source.startswith("service."):
        parts = source.split(".")
        if len(parts) < 4 or parts[1] not in service_names:
            return [ValidationIssue("error", f"{path}.source", f"unknown service source {source!r}", "declare the producer service under services")]
    return []


def _file_mount_value(contract: dict[str, Any], source: str) -> str | None:
    parts = source.split(".")
    if len(parts) == 4:
        item = contract.get("files", {}).get(parts[1], {})
        if isinstance(item, dict):
            mount = item.get("mount", {})
            if isinstance(mount, dict):
                return mount.get("path")
    return None


def as_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _load_yaml_with_ruby(path: Path) -> Any:
    """Load YAML via Ruby stdlib when PyYAML is unavailable.

    The project intentionally keeps the first coding-phase CLI dependency-light,
    while CI images commonly provide Ruby's YAML parser as part of stdlib.
    """
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV.fetch(0)))"
    try:
        result = subprocess.run(
            ["ruby", "-e", script, str(path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractError("PyYAML or Ruby stdlib YAML is required to read agent-vars YAML contracts") from exc
    return json.loads(result.stdout)
