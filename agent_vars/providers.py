from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import subprocess


@dataclass(frozen=True)
class ProviderSecret:
    name: str
    provider: str
    kind: str
    metadata: dict[str, Any]


class ProviderError(RuntimeError):
    pass


def list_secrets(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    kind = provider.get("kind")
    if kind == "gcp":
        return _list_gcp(provider_name, provider)
    if kind == "doppler":
        return _list_doppler(provider_name, provider)
    if kind == "local":
        return _list_local(provider_name, provider)
    return []


def get_secret(provider_name: str, provider: dict[str, Any], secret_name: str) -> str:
    """Fetch one secret payload without writing it to Agent Vars state."""
    kind = provider.get("kind")
    if kind == "gcp":
        fixture = os.environ.get("AGENT_VARS_GCP_VALUES_FILE")
        if fixture:
            return _fixture_value(Path(fixture), secret_name)
        project = provider.get("project_id")
        if not project:
            raise ProviderError(f"gcp provider {provider_name} missing project_id")
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest", "--secret", secret_name, "--project", str(project)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise ProviderError(result.stderr.strip() or f"failed to read GCP secret {secret_name}")
        return result.stdout
    if kind == "doppler":
        fixture = os.environ.get("AGENT_VARS_DOPPLER_SECRETS_FILE")
        values = json.loads(Path(fixture).read_text(encoding="utf-8")) if fixture else _download_doppler(provider)
        if secret_name not in values:
            raise ProviderError(f"Doppler secret not found: {secret_name}")
        return str(values[secret_name])
    if kind == "local":
        values = _read_env_values(Path(provider.get("path", ".env")))
        if secret_name not in values:
            raise ProviderError(f"local secret not found: {secret_name}")
        return values[secret_name]
    raise ProviderError(f"provider {provider_name} does not support payload reads")


def suggest_bindings(contract: dict[str, Any], provider_name: str, secrets: list[ProviderSecret]) -> list[dict[str, Any]]:
    secret_names = {s.name.lower().replace("-", "_"): s for s in secrets}
    suggestions: list[dict[str, Any]] = []
    for service_name, service in (contract.get("services") or {}).items():
        for req in service.get("requires", []):
            env_name = str(req.get("name", ""))
            key = env_name.lower()
            matched = secret_names.get(key) or secret_names.get(key.replace("_", ""))
            evidence = []
            confidence = "low"
            if matched:
                confidence = "high"
                evidence.append("secret name matches env var by convention")
            elif "GOOGLE_APPLICATION_CREDENTIALS" == env_name:
                matched = next((s for s in secrets if "service" in s.name.lower() and "account" in s.name.lower()), None)
                if matched:
                    confidence = "medium"
                    evidence.append("env var matches Google SDK credential convention")
            if matched:
                suggestions.append({"service": service_name, "required": env_name, "provider": provider_name, "suggested_source": f"{provider_name}.{matched.name}", "confidence": confidence, "evidence": evidence, "action": "approve|edit|reject"})
    return suggestions


def _list_gcp(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    fixture = os.environ.get("AGENT_VARS_GCP_SECRETS_FILE")
    if fixture:
        names = json.loads(Path(fixture).read_text(encoding="utf-8"))
    else:
        project = provider.get("project_id")
        if not project:
            raise ProviderError(f"gcp provider {provider_name} missing project_id")
        result = subprocess.run(["gcloud", "secrets", "list", "--project", str(project), "--format=json"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            raise ProviderError(result.stderr.strip() or "gcloud secrets list failed")
        names = [item.get("name", "").split("/")[-1] for item in json.loads(result.stdout)]
    return [ProviderSecret(str(name), provider_name, "gcp", {}) for name in names]


def _list_doppler(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    fixture = os.environ.get("AGENT_VARS_DOPPLER_SECRETS_FILE")
    if fixture:
        values = json.loads(Path(fixture).read_text(encoding="utf-8"))
    else:
        values = _download_doppler(provider)
    return [ProviderSecret(str(name), provider_name, "doppler", {"has_value": value is not None}) for name, value in values.items()]


def _list_local(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    path = Path(provider.get("path", ".env"))
    return [ProviderSecret(name, provider_name, "local", {}) for name in _read_env_values(path)]


def _download_doppler(provider: dict[str, Any]) -> dict[str, Any]:
    args = ["doppler", "secrets", "download", "--no-file", "--format=json"]
    if provider.get("project"):
        args += ["--project", str(provider["project"])]
    if provider.get("config"):
        args += ["--config", str(provider["config"])]
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise ProviderError(result.stderr.strip() or "doppler secrets download failed")
    values = json.loads(result.stdout)
    if not isinstance(values, dict):
        raise ProviderError("Doppler response must be a JSON object")
    return values


def _read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.removeprefix("export ").split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    return values


def _fixture_value(path: Path, secret_name: str) -> str:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or secret_name not in values:
        raise ProviderError(f"fixture secret not found: {secret_name}")
    value = values[secret_name]
    return value if isinstance(value, str) else json.dumps(value)
