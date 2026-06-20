from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from hashlib import sha256
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
    manifest_path: Path | None = None,
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
        policy = spec.get("policy", {}) if isinstance(spec.get("policy", {}), dict) else {}
        payload_bytes = payload.encode("utf-8")
        max_bytes = int(policy.get("max_bytes", 1024 * 1024))
        if len(payload_bytes) > max_bytes:
            raise ValueError(f"file secret {file_name} exceeds max_bytes policy ({max_bytes})")
        parsed_json = None
        if spec.get("format") == "json":
            try:
                parsed_json = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"file secret {file_name} is not valid JSON") from exc
            required_keys = policy.get("required_json_keys", [])
            if required_keys and (not isinstance(parsed_json, dict) or any(key not in parsed_json for key in required_keys)):
                missing = [key for key in required_keys if not isinstance(parsed_json, dict) or key not in parsed_json]
                raise ValueError(f"file secret {file_name} is missing required JSON keys: {', '.join(missing)}")
        declared_path = Path(str(spec.get("mount", {}).get("path", "")))
        if not str(declared_path):
            raise ValueError(f"file secret {file_name} has no mount path")
        target = _safe_target(mount_root, declared_path)
        mode = int(str(policy.get("mode", "0600")), 8)
        if mode not in {0o400, 0o600}:
            raise ValueError(f"file secret {file_name} mode must be 0400 or 0600")
        _atomic_private_write(target, payload, mode=mode)
        written.append(target)
        if manifest_path:
            _record_mount(manifest_path, mount_root, target, file_name, service_name, environment, payload_bytes, mode)
    return written


def cleanup_file_secrets(
    mount_root: Path,
    manifest_path: Path,
    *,
    service_name: str | None = None,
    force: bool = False,
) -> list[Path]:
    manifest = _load_manifest(manifest_path)
    kept = []
    candidates: list[Path] = []
    for record in manifest.get("mounts", []):
        if service_name and record.get("service") != service_name:
            kept.append(record)
            continue
        target = _safe_target(mount_root, Path(str(record.get("relative_path", ""))))
        if target.exists():
            current_hash = sha256(target.read_bytes()).hexdigest()
            if not force and current_hash != record.get("sha256"):
                raise ValueError(f"refusing to remove modified mounted file: {target}; use --force to override")
            candidates.append(target)
    removed = []
    for target in candidates:
        target.unlink()
        removed.append(target)
        _remove_empty_parents(target.parent, mount_root.resolve())
    if kept:
        _write_manifest(manifest_path, {"version": 1, "mounts": kept})
    else:
        manifest_path.unlink(missing_ok=True)
    return removed


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


def _atomic_private_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _record_mount(manifest_path: Path, root: Path, target: Path, file_name: str, service: str, environment: str, payload: bytes, mode: int) -> None:
    manifest = _load_manifest(manifest_path)
    root = root.resolve()
    record = {
        "file": file_name,
        "service": service,
        "environment": environment,
        "relative_path": str(target.resolve().relative_to(root)),
        "sha256": sha256(payload).hexdigest(),
        "bytes": len(payload),
        "mode": f"{mode:04o}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mounts = [item for item in manifest.get("mounts", []) if item.get("relative_path") != record["relative_path"]]
    mounts.append(record)
    _write_manifest(manifest_path, {"version": 1, "mounts": mounts})


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "mounts": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("mounts"), list):
        raise ValueError(f"invalid mount manifest: {path}")
    return data


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    _atomic_private_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o600)


def _remove_empty_parents(path: Path, root: Path) -> None:
    current = path.resolve()
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
