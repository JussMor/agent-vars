from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import tempfile

from .providers import get_secret


def materialize_file_secrets(
    contract: dict[str, Any],
    service_name: str,
    *,
    environment: str,
    mount_root: Path,
    payloads: dict[str, Any] | None = None,
) -> list[Path]:
    service = (contract.get("services") or {}).get(service_name)
    if not isinstance(service, dict):
        raise ValueError(f"unknown service: {service_name}")
    required_files = {
        str(req.get("source")).split(".")[1]
        for req in service.get("requires", [])
        if isinstance(req, dict) and str(req.get("source", "")).startswith("file.")
    }
    written = []
    for file_name in sorted(required_files):
        spec = (contract.get("files") or {}).get(file_name)
        if not isinstance(spec, dict):
            raise ValueError(f"unknown file secret: {file_name}")
        payload = _payload(contract, environment, file_name, spec, payloads or {})
        if spec.get("format") == "json":
            try:
                json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"file secret {file_name} is not valid JSON") from exc
        declared_path = Path(str(spec.get("mount", {}).get("path", "")))
        if not str(declared_path):
            raise ValueError(f"file secret {file_name} has no mount path")
        target = _safe_target(mount_root, declared_path)
        _atomic_private_write(target, payload)
        written.append(target)
    return written


def _payload(contract: dict[str, Any], environment: str, file_name: str, spec: dict[str, Any], payloads: dict[str, Any]) -> str:
    if file_name in payloads:
        value = payloads[file_name]
        return value if isinstance(value, str) else json.dumps(value)
    env = (contract.get("environments") or {}).get(environment, {})
    provider_name = env.get("provider_profile") if isinstance(env, dict) else None
    provider = (contract.get("providers") or {}).get(provider_name) if provider_name else None
    if not isinstance(provider, dict):
        raise ValueError(f"environment {environment} has no usable provider profile")
    source = str(spec.get("source", ""))
    secret_name = source.split(".secret_manager.", 1)[1] if ".secret_manager." in source else source.rsplit(".", 1)[-1]
    return get_secret(str(provider_name), provider, secret_name)


def _safe_target(root: Path, declared: Path) -> Path:
    root = root.resolve()
    relative = Path(*declared.parts[1:]) if declared.is_absolute() else declared
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"mount path escapes mount root: {declared}")
    return target


def _atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise
