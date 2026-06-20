from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import base64
import json
import os
import re
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
    fixture = _provider_fixture(provider)
    if fixture is not None:
        return [ProviderSecret(str(name), provider_name, str(kind), {"fixture": True}) for name in fixture]
    if kind == "gcp":
        return _list_gcp(provider_name, provider)
    if kind == "cloudflare":
        return _list_cloudflare(provider_name, provider)
    if kind == "doppler":
        return _list_doppler(provider_name, provider)
    if kind == "vault":
        return _list_vault(provider_name, provider)
    if kind == "aws":
        return _list_aws(provider_name, provider)
    if kind == "kubernetes":
        return _list_kubernetes(provider_name, provider)
    if kind == "local-encrypted":
        return _list_local_encrypted(provider_name, provider)
    if kind == "local":
        return _list_local(provider_name, provider)
    raise ProviderError(f"unsupported provider kind: {kind}")


def get_secret(provider_name: str, provider: dict[str, Any], secret_name: str) -> str:
    """Fetch one secret payload without writing it to Agent Vars state."""
    kind = provider.get("kind")
    fixture = _provider_fixture(provider)
    if fixture is not None:
        if secret_name not in fixture:
            raise ProviderError(f"fixture secret not found: {secret_name}")
        return _string_value(fixture[secret_name])
    if kind == "gcp":
        fixture = os.environ.get("AGENT_VARS_GCP_VALUES_FILE")
        if fixture:
            return _fixture_value(Path(fixture), secret_name)
        project = provider.get("project_id")
        if not project:
            raise ProviderError(f"gcp provider {provider_name} missing project_id")
        result = subprocess.run(
            [str(provider.get("executable", "gcloud")), "secrets", "versions", "access", "latest", "--secret", secret_name, "--project", str(project)],
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
    if kind == "cloudflare":
        raise ProviderError("Cloudflare Worker secret payloads are write-only and cannot be retrieved")
    if kind == "vault":
        path = _required(provider, "path", provider_name)
        result = _run([str(provider.get("executable", "vault")), "kv", "get", "-format=json", f"{path}/{secret_name}"])
        data = json.loads(result)
        values = data.get("data", {}).get("data", data.get("data", {}))
        if not isinstance(values, dict):
            raise ProviderError("Vault response does not contain secret data")
        if secret_name in values:
            return _string_value(values[secret_name])
        if "value" in values:
            return _string_value(values["value"])
        return json.dumps(values)
    if kind == "aws":
        args = [str(provider.get("executable", "aws")), "secretsmanager", "get-secret-value", "--secret-id", secret_name, "--output", "json"]
        if provider.get("region"):
            args += ["--region", str(provider["region"])]
        data = json.loads(_run(args))
        if data.get("SecretString") is not None:
            return str(data["SecretString"])
        if data.get("SecretBinary") is not None:
            return base64.b64decode(data["SecretBinary"]).decode("utf-8")
        raise ProviderError(f"AWS secret has no payload: {secret_name}")
    if kind == "kubernetes":
        namespace = str(provider.get("namespace", "default"))
        args = [str(provider.get("executable", "kubectl")), "get", "secret", secret_name, "--namespace", namespace, "-o", "json"]
        data = json.loads(_run(args)).get("data", {})
        if not isinstance(data, dict):
            raise ProviderError(f"Kubernetes Secret has no data: {secret_name}")
        key = str(provider.get("key", "value"))
        if key in data:
            return base64.b64decode(data[key]).decode("utf-8")
        if len(data) == 1:
            return base64.b64decode(next(iter(data.values()))).decode("utf-8")
        return json.dumps({name: base64.b64decode(value).decode("utf-8") for name, value in data.items()})
    if kind == "local-encrypted":
        values = _decrypt_local(provider)
        if secret_name not in values:
            raise ProviderError(f"encrypted local secret not found: {secret_name}")
        return _string_value(values[secret_name])
    if kind == "local":
        values = _read_env_values(Path(provider.get("path", ".env")))
        if secret_name not in values:
            raise ProviderError(f"local secret not found: {secret_name}")
        return values[secret_name]
    raise ProviderError(f"provider {provider_name} does not support payload reads")


def suggest_bindings(contract: dict[str, Any], provider_name: str, secrets: list[ProviderSecret], *, environment: str | None = None) -> list[dict[str, Any]]:
    exact_names = {s.name.lower(): s for s in secrets}
    normalized_names = {re.sub(r"[^a-z0-9]", "", s.name.lower()): s for s in secrets}
    environment_spec = (contract.get("environments") or {}).get(environment, {}) if environment else {}
    production_impact = environment in {"prod", "production"} or (isinstance(environment_spec, dict) and bool(environment_spec.get("production") or environment_spec.get("protected")))
    suggestions: list[dict[str, Any]] = []
    for service_name, service in (contract.get("services") or {}).items():
        for req in service.get("requires", []):
            env_name = str(req.get("name", ""))
            key = env_name.lower()
            matched = exact_names.get(key)
            evidence = []
            confidence = "low"
            if matched:
                confidence = "high"
                evidence.append("secret name exactly matches env var ignoring case")
            elif normalized_names.get(re.sub(r"[^a-z0-9]", "", key)):
                matched = normalized_names[re.sub(r"[^a-z0-9]", "", key)]
                confidence = "medium"
                evidence.append("secret name matches env var after separator normalization")
            elif "GOOGLE_APPLICATION_CREDENTIALS" == env_name:
                matched = next((s for s in secrets if "service" in s.name.lower() and "account" in s.name.lower()), None)
                if matched:
                    confidence = "medium"
                    evidence.append("env var matches Google SDK credential convention")
            suggestions.append({
                "service": service_name,
                "required": env_name,
                "provider": provider_name,
                "environment": environment,
                "production_impact": production_impact,
                "suggested_source": f"{provider_name}.{matched.name}" if matched else None,
                "confidence": confidence,
                "evidence": evidence or ["no provider resource matched by naming convention"],
                "action": "approve|edit|reject",
            })
    return suggestions


def _list_gcp(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    fixture = os.environ.get("AGENT_VARS_GCP_SECRETS_FILE")
    if fixture:
        names = json.loads(Path(fixture).read_text(encoding="utf-8"))
    else:
        project = provider.get("project_id")
        if not project:
            raise ProviderError(f"gcp provider {provider_name} missing project_id")
        result = subprocess.run([str(provider.get("executable", "gcloud")), "secrets", "list", "--project", str(project), "--format=json"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
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


def _list_cloudflare(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    args = [str(provider.get("executable", "wrangler")), "secret", "list", "--json"]
    if provider.get("name"):
        args += ["--name", str(provider["name"])]
    values = json.loads(_run(args))
    if not isinstance(values, list):
        raise ProviderError("Cloudflare secret list must be a JSON array")
    return [ProviderSecret(str(item.get("name")), provider_name, "cloudflare", {"type": item.get("type"), "readable": False}) for item in values if isinstance(item, dict) and item.get("name")]


def _list_vault(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    path = _required(provider, "path", provider_name)
    values = json.loads(_run([str(provider.get("executable", "vault")), "kv", "list", "-format=json", path]))
    if not isinstance(values, list):
        raise ProviderError("Vault list response must be a JSON array")
    return [ProviderSecret(str(name).rstrip("/"), provider_name, "vault", {"path": path}) for name in values]


def _list_aws(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    args = [str(provider.get("executable", "aws")), "secretsmanager", "list-secrets", "--output", "json"]
    if provider.get("region"):
        args += ["--region", str(provider["region"])]
    values = json.loads(_run(args)).get("SecretList", [])
    return [ProviderSecret(str(item.get("Name")), provider_name, "aws", {"arn": item.get("ARN"), "description": item.get("Description")}) for item in values if isinstance(item, dict) and item.get("Name")]


def _list_kubernetes(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    namespace = str(provider.get("namespace", "default"))
    args = [str(provider.get("executable", "kubectl")), "get", "secrets", "--namespace", namespace, "-o", "json"]
    items = json.loads(_run(args)).get("items", [])
    return [ProviderSecret(str(item.get("metadata", {}).get("name")), provider_name, "kubernetes", {"namespace": namespace, "type": item.get("type")}) for item in items if isinstance(item, dict) and item.get("metadata", {}).get("name")]


def _list_local_encrypted(provider_name: str, provider: dict[str, Any]) -> list[ProviderSecret]:
    return [ProviderSecret(name, provider_name, "local-encrypted", {"path": provider.get("path")}) for name in _decrypt_local(provider)]


def _download_doppler(provider: dict[str, Any]) -> dict[str, Any]:
    args = [str(provider.get("executable", "doppler")), "secrets", "download", "--no-file", "--format=json"]
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


def _provider_fixture(provider: dict[str, Any]) -> dict[str, Any] | None:
    fixture = provider.get("fixture")
    if not fixture:
        return None
    values = json.loads(Path(str(fixture)).read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ProviderError("provider fixture must be a JSON object")
    return values


def _decrypt_local(provider: dict[str, Any]) -> dict[str, Any]:
    path = _required(provider, "path", "local-encrypted")
    args = [str(provider.get("executable", "age")), "--decrypt"]
    if provider.get("identity"):
        args += ["--identity", str(provider["identity"])]
    args.append(path)
    plaintext = _run(args)
    if str(provider.get("format", "json")) == "env":
        values = {}
        for line in plaintext.splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip("'\"")
        return values
    values = json.loads(plaintext)
    if not isinstance(values, dict):
        raise ProviderError("decrypted local file must contain a JSON object")
    return values


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise ProviderError(f"provider executable not available: {args[0]}") from exc
    if result.returncode != 0:
        raise ProviderError(result.stderr.strip() or f"provider command failed: {' '.join(args[:3])}")
    return result.stdout


def _required(provider: dict[str, Any], key: str, provider_name: str) -> str:
    value = provider.get(key)
    if not value:
        raise ProviderError(f"provider {provider_name} missing {key}")
    return str(value)


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)
