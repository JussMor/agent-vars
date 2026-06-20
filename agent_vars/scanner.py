from __future__ import annotations

from pathlib import Path
from typing import Any
import re

ENV_PATTERNS = [re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"), re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)"), re.compile(r"os\.environ(?:\.get)?\(['\"]([A-Z][A-Z0-9_]*)['\"]")]


def scan_repo(root: Path) -> dict[str, Any]:
    env_files = sorted(str(p.relative_to(root)) for p in root.rglob(".env*") if ".git" not in p.parts)
    dockerfiles = sorted(str(p.relative_to(root)) for p in root.rglob("Dockerfile*") if ".git" not in p.parts)
    code_vars: dict[str, list[str]] = {}
    for path in root.rglob("*"):
        if path.is_dir() or ".git" in path.parts or path.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = sorted({m.group(1) for pat in ENV_PATTERNS for m in pat.finditer(text)})
        if found:
            code_vars[str(path.relative_to(root))] = found
    services = _discover_services(root, env_files, dockerfiles, code_vars)
    return {"version": 1, "project": root.name, "repo": {"topology": "monorepo" if len(services) > 1 else "single"}, "evidence": {"env_files": env_files, "dockerfiles": dockerfiles, "code_vars": code_vars}, "services": services, "uncertain_mappings": _uncertain(services)}


def _discover_services(root: Path, env_files: list[str], dockerfiles: list[str], code_vars: dict[str, list[str]]) -> dict[str, Any]:
    service_roots = {Path(p).parent for p in env_files + dockerfiles if Path(p).parent != Path(".")}
    if not service_roots:
        service_roots = {Path(".")}
    services: dict[str, Any] = {}
    for service_root in sorted(service_roots):
        name = root.name if str(service_root) == "." else service_root.name
        vars_for_service = sorted({v for path, vars_ in code_vars.items() if Path(path).is_relative_to(service_root) for v in vars_})
        services[name] = {"root": str(service_root), "env_files": [p for p in env_files if Path(p).is_relative_to(service_root)], "requires": [{"name": v, "source": "review.required", "visibility": _visibility(v), "phase": _phase(v), "required": True} for v in vars_for_service]}
    return services


def _visibility(name: str) -> str:
    if name.startswith(("VITE_", "NEXT_PUBLIC_", "PUBLIC_")):
        return "public"
    if "SECRET" in name or "KEY" in name or "TOKEN" in name or "PASSWORD" in name:
        return "secret"
    return "internal"


def _phase(name: str) -> str:
    return "build" if name.startswith(("VITE_", "NEXT_PUBLIC_", "PUBLIC_")) else "runtime"


def _uncertain(services: dict[str, Any]) -> list[dict[str, str]]:
    return [{"service": service, "variable": req["name"], "reason": "source requires human/provider approval"} for service, spec in services.items() for req in spec.get("requires", []) if req.get("source") == "review.required"]
