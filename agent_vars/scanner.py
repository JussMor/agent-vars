from __future__ import annotations

from pathlib import Path
from typing import Any
import re
import json

import yaml

ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"process\.env\[['\"]([A-Z][A-Z0-9_]*)['\"]\]"),
    re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"import\.meta\.env\[['\"]([A-Z][A-Z0-9_]*)['\"]\]"),
    re.compile(r"os\.(?:environ(?:\.get)?|getenv)\(['\"]([A-Z][A-Z0-9_]*)['\"]"),
]
SCRIPT_ENV_PATTERN = re.compile(r"(?<![A-Z0-9_])\$\{?([A-Z][A-Z0-9_]*)\}?")
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
COMPOSE_SERVICES = {"nats", "redis", "mongodb", "mongo", "postgres", "postgresql"}
CREDENTIAL_PATH = re.compile(r"(?P<path>/(?:app|workspace|home|var|run)/[^\s'\";,)]*(?:credential|credentials|gcloud|service[-_]?account)[^\s'\";,)]*)", re.IGNORECASE)
IGNORED_PARTS = {".agent-vars", ".git", ".pytest_cache", ".tox", ".venv", "node_modules", "dist", "build", "__pycache__"}


def scan_repo(root: Path) -> dict[str, Any]:
    root = root.resolve()
    env_files = sorted(str(p.relative_to(root)) for p in root.rglob(".env*") if not _ignored(p))
    dockerfiles = sorted(str(p.relative_to(root)) for p in root.rglob("Dockerfile*") if not _ignored(p))
    code_vars: dict[str, list[str]] = {}
    for path in root.rglob("*"):
        if path.is_dir() or _ignored(path) or path.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = sorted({m.group(1) for pat in ENV_PATTERNS for m in pat.finditer(text)})
        if found:
            code_vars[str(path.relative_to(root))] = found
    env_file_vars = _read_env_files(root, env_files)
    frameworks = _detect_frameworks(root)
    script_vars = _detect_build_script_vars(root)
    code_vars.update({path: sorted(set(code_vars.get(path, [])) | set(names)) for path, names in script_vars.items()})
    runtime_dependencies = _detect_compose_dependencies(root)
    ci = _detect_ci(root)
    environments = _detect_environments(root, env_files, ci)
    manifests = _detect_manifests(root)
    infrastructure = _detect_infrastructure_references(root, manifests)
    credential_paths = _detect_credential_paths(root)
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
            "infrastructure": infrastructure,
            "ci": ci,
            "credential_paths": credential_paths,
        },
        "services": services,
        "uncertain_mappings": _uncertain(services),
    }


def scan_workspace(roots: list[Path]) -> dict[str, Any]:
    if len(roots) < 2:
        return scan_repo(roots[0])
    repositories: dict[str, Any] = {}
    for root in roots:
        resolved = root.resolve()
        name = resolved.name
        if name in repositories:
            name = str(resolved).replace("/", "_").strip("_")
        repositories[name] = scan_repo(resolved)
    services = {
        f"{repo}:{service}": {**spec, "repository": repo}
        for repo, profile in repositories.items()
        for service, spec in profile.get("services", {}).items()
    }
    environments = {
        environment: {}
        for profile in repositories.values()
        for environment in profile.get("environments", {})
    }
    runtime_dependencies = {
        f"{repo}:{dependency}": {**spec, "repository": repo}
        for repo, profile in repositories.items()
        for dependency, spec in profile.get("runtime_dependencies", {}).items()
    }
    return {
        "version": 1,
        "project": "workspace",
        "repo": {"topology": "multi-repo", "repositories": list(repositories)},
        "repositories": repositories,
        "environments": environments,
        "runtime_dependencies": runtime_dependencies,
        "services": services,
        "uncertain_mappings": [
            {**item, "repository": repo}
            for repo, profile in repositories.items()
            for item in profile.get("uncertain_mappings", [])
        ],
    }


def _discover_services(root: Path, env_files: list[str], dockerfiles: list[str], code_vars: dict[str, list[str]], env_file_vars: dict[str, list[str]], frameworks: dict[str, str]) -> dict[str, Any]:
    package_roots = _package_roots(root)
    evidence_paths = sorted(set(env_files) | set(dockerfiles) | set(code_vars) | set(env_file_vars))
    assigned: dict[Path, set[str]] = {}
    env_files_by_root: dict[Path, list[str]] = {}

    for relative in evidence_paths:
        service_root = _nearest_service_root(Path(relative), package_roots)
        assigned.setdefault(service_root, set())
        if relative in code_vars:
            assigned[service_root].update(code_vars[relative])
        if relative in env_file_vars:
            assigned[service_root].update(env_file_vars[relative])
        if relative in env_files:
            env_files_by_root.setdefault(service_root, []).append(relative)

    for framework_root in frameworks:
        assigned.setdefault(Path(framework_root), set())
    for package_root in package_roots:
        assigned.setdefault(package_root, set())
    if not assigned:
        assigned[Path(".")] = set()

    services: dict[str, Any] = {}
    used_names: set[str] = set()
    for service_root in sorted(assigned, key=lambda value: (len(value.parts), str(value))):
        if _covered_by_child_service(service_root, assigned):
            continue
        service_env_files = sorted(env_files_by_root.get(service_root, []))
        vars_for_service = sorted(assigned.get(service_root, set()))
        name = _unique_service_name(_service_name(root, service_root), service_root, used_names)
        used_names.add(name)
        spec: dict[str, Any] = {
            "root": str(service_root),
            "env_files": service_env_files,
            "requires": [
                {
                    "name": variable,
                    "source": variable,
                    "visibility": _visibility(variable),
                    "phase": _phase(variable),
                    "required": _required_by_default(variable),
                }
                for variable in vars_for_service
            ],
        }
        if str(service_root) in frameworks:
            spec["framework"] = frameworks[str(service_root)]
        services[name] = spec
    return services


def _package_roots(root: Path) -> set[Path]:
    return {path.parent.relative_to(root) for path in root.rglob("package.json") if not _ignored(path)}


def _nearest_service_root(relative: Path, package_roots: set[Path]) -> Path:
    candidates = [candidate for candidate in package_roots if relative == candidate or relative.is_relative_to(candidate)]
    if candidates:
        return max(candidates, key=lambda value: len(value.parts))
    parent = relative.parent
    return parent if parent != Path(".") else Path(".")


def _covered_by_child_service(service_root: Path, assigned: dict[Path, set[str]]) -> bool:
    if assigned.get(service_root):
        return False
    if service_root == Path("."):
        return any(child != service_root for child in assigned)
    return any(child != service_root and child.is_relative_to(service_root) for child in assigned)


def _service_name(root: Path, service_root: Path) -> str:
    package_file = root / service_root / "package.json"
    if package_file.exists():
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            package = {}
        package_name = package.get("name") if isinstance(package, dict) else None
        if isinstance(package_name, str) and package_name.strip():
            return _sanitize_service_name(package_name.rsplit("/", 1)[-1])
    return root.name if service_root == Path(".") else service_root.name


def _unique_service_name(name: str, service_root: Path, used_names: set[str]) -> str:
    if name not in used_names:
        return name
    fallback = _sanitize_service_name(str(service_root).replace("/", "-").replace(".", "root"))
    if fallback and fallback not in used_names:
        return fallback
    index = 2
    while f"{name}-{index}" in used_names:
        index += 1
    return f"{name}-{index}"


def _sanitize_service_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")
    return sanitized or "service"


def _visibility(name: str) -> str:
    if name.startswith(("VITE_", "NEXT_PUBLIC_", "PUBLIC_")):
        return "public"
    if "SECRET" in name or "KEY" in name or "TOKEN" in name or "PASSWORD" in name:
        return "secret"
    return "internal"


def _phase(name: str) -> str:
    return "build" if name.startswith(("VITE_", "NEXT_PUBLIC_", "PUBLIC_")) else "runtime"


def _required_by_default(name: str) -> bool:
    return not name.endswith("_OPTIONAL")


def _uncertain(services: dict[str, Any]) -> list[dict[str, str]]:
    return [{"service": service, "variable": req["name"], "reason": "source inferred from repository evidence; review provider mapping and required policy"} for service, spec in services.items() for req in spec.get("requires", [])]


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
        if _ignored(package_file):
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
        if _ignored(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for name in COMPOSE_SERVICES:
            if re.search(rf"^\s{{2,}}{re.escape(name)}:\s*$", text, re.MULTILINE):
                canonical = "mongodb" if name == "mongo" else "postgres" if name == "postgresql" else name
                output = "uri" if canonical == "mongodb" else "url"
                result[canonical] = {"source": f"compose.service.{name}", "outputs": {output: f"{canonical.upper()}_{'URI' if output == 'uri' else 'URL'}"}}
    return result


def _detect_environments(root: Path, env_files: list[str], ci: dict[str, Any]) -> dict[str, Any]:
    known = {"dev", "development", "qa", "stage", "staging", "uat", "prod", "production", "test"}
    found = {part for path in env_files for part in Path(path).name.split(".") if part in known}
    found.update(str(value).lower() for value in ci.get("environments", []))
    aliases = {"development": "dev", "staging": "stage", "production": "prod"}
    return {aliases.get(name, name): {} for name in sorted(found)}


def _detect_manifests(root: Path) -> list[str]:
    names = {"chart.yaml", "terraform.tf", "wrangler.toml", "wrangler.jsonc", "kustomization.yaml"}
    result = []
    for path in root.rglob("*"):
        lower = path.name.lower()
        if path.is_file() and not _ignored(path) and (
            lower in names
            or path.suffix == ".tf"
            or "helm" in {part.lower() for part in path.parts}
            or (path.suffix.lower() in {".yaml", ".yml"} and _looks_like_manifest(path))
        ):
            result.append(str(path.relative_to(root)))
    return sorted(result)


def _detect_build_script_vars(root: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for package_file in root.rglob("package.json"):
        if _ignored(package_file):
            continue
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scripts = package.get("scripts", {})
        text = "\n".join(str(value) for value in scripts.values()) if isinstance(scripts, dict) else ""
        found = {match.group(1) for pattern in ENV_PATTERNS for match in pattern.finditer(text)}
        found.update(match.group(1) for match in SCRIPT_ENV_PATTERN.finditer(text))
        if found:
            result[str(package_file.relative_to(root))] = sorted(found)
    return result


def _detect_ci(root: Path) -> dict[str, Any]:
    files = [*root.glob(".github/workflows/*.yml"), *root.glob(".github/workflows/*.yaml")]
    files += [path for name in (".gitlab-ci.yml", ".circleci/config.yml") if (path := root / name).exists()]
    environments: set[str] = set()
    matrices: list[dict[str, Any]] = []
    for path in files:
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError):
            continue
        for document in documents:
            _walk_ci(document, environments, matrices, str(path.relative_to(root)))
    aliases = {"development": "dev", "staging": "stage", "production": "prod"}
    normalized = sorted({aliases.get(value.lower(), value.lower()) for value in environments if value})
    return {"files": [str(path.relative_to(root)) for path in files], "environments": normalized, "matrices": matrices}


def _walk_ci(node: Any, environments: set[str], matrices: list[dict[str, Any]], source: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key).lower()
            if key_text == "matrix" and isinstance(value, dict):
                clean = {str(k): v for k, v in value.items() if isinstance(v, (list, str, int, float, bool))}
                matrices.append({"source": source, "values": clean})
                for matrix_key, matrix_value in clean.items():
                    if matrix_key.lower() in {"environment", "env", "stage"}:
                        environments.update(str(item) for item in (matrix_value if isinstance(matrix_value, list) else [matrix_value]))
            if key_text in {"environment", "environment_name"}:
                if isinstance(value, str) and "${{" not in value and "$" not in value:
                    environments.add(value)
                elif isinstance(value, dict) and isinstance(value.get("name"), str):
                    environments.add(value["name"])
            _walk_ci(value, environments, matrices, source)
    elif isinstance(node, list):
        for item in node:
            _walk_ci(item, environments, matrices, source)


def _detect_infrastructure_references(root: Path, manifests: list[str]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {"kubernetes": [], "helm": [], "terraform": [], "cloud_outputs": []}
    for relative in manifests:
        path = root / relative
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".tf":
            for kind, name in re.findall(r'(?m)^\s*(variable|output)\s+"([^"]+)"', text):
                result["terraform"].append({"file": relative, "kind": kind, "name": name})
            for provider, resource in re.findall(r'\b(google|aws|cloudflare|azurerm)_[a-z0-9_]+\.([a-zA-Z0-9_-]+)', text):
                result["cloud_outputs"].append({"file": relative, "provider": provider, "reference": resource})
            continue
        try:
            documents = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue
        category = "helm" if path.name.lower() == "chart.yaml" or "helm" in {part.lower() for part in path.parts} else "kubernetes"
        for document in documents:
            if category == "helm":
                _walk_helm(document, result[category], relative)
            else:
                _walk_manifest(document, result[category], relative)
    return {key: value for key, value in result.items() if value}


def _walk_manifest(node: Any, found: list[dict[str, str]], source: str) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("name"), str) and any(key in node for key in ("valueFrom", "secretKeyRef", "configMapKeyRef")):
            found.append({"file": source, "name": node["name"], "reference": _manifest_reference(node)})
        for key in ("secretKeyRef", "configMapKeyRef"):
            ref = node.get(key)
            if isinstance(ref, dict):
                found.append({"file": source, "name": str(ref.get("key", "")), "reference": f"{key}:{ref.get('name', '')}"})
        for value in node.values():
            _walk_manifest(value, found, source)
    elif isinstance(node, list):
        for item in node:
            _walk_manifest(item, found, source)


def _manifest_reference(node: dict[str, Any]) -> str:
    value_from = node.get("valueFrom")
    if isinstance(value_from, dict):
        for key in ("secretKeyRef", "configMapKeyRef", "fieldRef"):
            if key in value_from:
                return key
    return "value"


def _walk_helm(node: Any, found: list[dict[str, str]], source: str, prefix: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if not isinstance(value, (dict, list)):
                found.append({"file": source, "name": path, "reference": "helm-value"})
            _walk_helm(value, found, source, path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk_helm(item, found, source, f"{prefix}[{index}]")


def _detect_credential_paths(root: Path) -> list[dict[str, str]]:
    found: dict[str, str] = {}
    allowed = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".tf", ""}
    for path in root.rglob("*"):
        if not path.is_file() or _ignored(path) or path.suffix.lower() not in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in CREDENTIAL_PATH.finditer(text):
            found.setdefault(match.group("path"), str(path.relative_to(root)))
    return [{"path": path, "file": source} for path, source in sorted(found.items())]


def _ignored(path: Path) -> bool:
    return bool(IGNORED_PARTS.intersection(path.parts))


def _looks_like_manifest(path: Path) -> bool:
    if any(part in {".github", ".circleci"} for part in path.parts) or "compose" in path.name.lower():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")[:8192]
    return bool(re.search(r"(?m)^\s*(apiVersion|kind|secretKeyRef|configMapKeyRef):", text))
