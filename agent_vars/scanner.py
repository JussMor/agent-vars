from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import json

ENV_PATTERNS = [re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"), re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)"), re.compile(r"os\.environ(?:\.get)?\(['\"]([A-Z][A-Z0-9_]*)['\"]")]
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
COMPOSE_SERVICES = {"nats", "redis", "mongodb", "mongo", "postgres", "postgresql"}


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
    env_file_vars = _read_env_files(root, env_files)
    frameworks = _detect_frameworks(root)
    runtime_dependencies = _detect_compose_dependencies(root)
    environments = _detect_environments(root, env_files)
    manifests = _detect_manifests(root)
    services = _discover_services(root, env_files, dockerfiles, code_vars, env_file_vars, frameworks)
    return {
        "version": 1,
        "project": root.name,
        "repo": {"topology": "monorepo" if len(services) > 1 else "single"},
        "environments": environments,
        "runtime_dependencies": runtime_dependencies,
        "evidence": {
            "env_files": env_files,
            "env_file_vars": env_file_vars,
            "dockerfiles": dockerfiles,
            "code_vars": code_vars,
            "manifests": manifests,
        },
        "services": services,
        "uncertain_mappings": _uncertain(services),
    }


def _discover_services(root: Path, env_files: list[str], dockerfiles: list[str], code_vars: dict[str, list[str]], env_file_vars: dict[str, list[str]], frameworks: dict[str, str]) -> dict[str, Any]:
    package_roots = [str(path.relative_to(root)) for path in root.rglob("package.json") if ".git" not in path.parts and path.parent != root]
    service_roots = {Path(p).parent for p in env_files + dockerfiles + package_roots if Path(p).parent != Path(".")}
    if not service_roots:
        service_roots = {Path(".")}
    services: dict[str, Any] = {}
    for service_root in sorted(service_roots):
        name = root.name if str(service_root) == "." else service_root.name
        service_env_files = [p for p in env_files if Path(p).is_relative_to(service_root)]
        vars_for_service = sorted(
            {v for path, vars_ in code_vars.items() if Path(path).is_relative_to(service_root) for v in vars_}
            | {v for path, vars_ in env_file_vars.items() if Path(path).is_relative_to(service_root) for v in vars_}
        )
        spec: dict[str, Any] = {"root": str(service_root), "env_files": service_env_files, "requires": [{"name": v, "source": "review.required", "visibility": _visibility(v), "phase": _phase(v), "required": True} for v in vars_for_service]}
        if str(service_root) in frameworks:
            spec["framework"] = frameworks[str(service_root)]
        services[name] = spec
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


def _read_env_files(root: Path, env_files: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for relative in env_files:
        names = sorted({match.group(1) for line in (root / relative).read_text(encoding="utf-8", errors="ignore").splitlines() if (match := ENV_LINE.match(line))})
        if names:
            result[relative] = names
    return result


def _detect_frameworks(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for package_file in root.rglob("package.json"):
        if ".git" in package_file.parts:
            continue
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        framework = "next" if "next" in dependencies else "vite" if "vite" in dependencies else None
        if framework:
            result[str(package_file.parent.relative_to(root))] = framework
    return result


def _detect_compose_dependencies(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in [*root.rglob("compose*.yml"), *root.rglob("compose*.yaml"), *root.rglob("docker-compose*.yml"), *root.rglob("docker-compose*.yaml")]:
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for name in COMPOSE_SERVICES:
            if re.search(rf"^\s{{2,}}{re.escape(name)}:\s*$", text, re.MULTILINE):
                canonical = "mongodb" if name == "mongo" else "postgres" if name == "postgresql" else name
                output = "uri" if canonical == "mongodb" else "url"
                result[canonical] = {"source": f"compose.service.{name}", "outputs": {output: f"{canonical.upper()}_{'URI' if output == 'uri' else 'URL'}"}}
    return result


def _detect_environments(root: Path, env_files: list[str]) -> dict[str, Any]:
    known = {"dev", "development", "qa", "stage", "staging", "uat", "prod", "production", "test"}
    found = {part for path in env_files for part in Path(path).name.split(".") if part in known}
    for workflow in (root / ".github" / "workflows").glob("*.y*ml") if (root / ".github" / "workflows").exists() else []:
        text = workflow.read_text(encoding="utf-8", errors="ignore").lower()
        found.update(name for name in known if re.search(rf"\b{re.escape(name)}\b", text))
    aliases = {"development": "dev", "staging": "stage", "production": "prod"}
    return {aliases.get(name, name): {} for name in sorted(found)}


def _detect_manifests(root: Path) -> list[str]:
    names = {"chart.yaml", "terraform.tf", "wrangler.toml", "wrangler.jsonc", "kustomization.yaml"}
    result = []
    for path in root.rglob("*"):
        lower = path.name.lower()
        if path.is_file() and ".git" not in path.parts and (lower in names or path.suffix == ".tf"):
            result.append(str(path.relative_to(root)))
    return sorted(result)
