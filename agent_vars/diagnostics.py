from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from .resolution import ResolvedValue


@dataclass(frozen=True)
class Diagnostic:
    level: str
    status: str
    service: str
    variable: str
    source: str
    phase: str | None
    message: str
    hint: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_service(contract: dict[str, Any], service_name: str, resolved: dict[str, ResolvedValue]) -> list[Diagnostic]:
    service = (contract.get("services") or {}).get(service_name, {})
    requirements = service.get("requires", []) if isinstance(service, dict) else []
    diagnostics: list[Diagnostic] = []
    seen: dict[str, str] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict) or not requirement.get("name"):
            continue
        name = str(requirement["name"])
        source = str(requirement.get("source", ""))
        phase = requirement.get("phase")
        previous = seen.setdefault(name, source)
        if previous != source:
            diagnostics.append(Diagnostic("error", "conflicting", service_name, name, source, phase, "variable has conflicting sources", "choose one source or rename the service-specific variable"))
        value = resolved.get(name)
        if value is None or value.status in {"missing", "unverified"}:
            if requirement.get("required", True):
                diagnostics.append(Diagnostic("error", "missing", service_name, name, source, phase, "required value is unresolved", value.hint if value else "declare and provide the required value"))
            continue
        if value.status == "malformed":
            diagnostics.append(Diagnostic("error", "malformed", service_name, name, source, phase, value.error or "mounted value is malformed", value.hint or "re-materialize the value"))
            continue
        if value.status in {"stale", "expired"}:
            diagnostics.append(Diagnostic("error", "stale", service_name, name, source, phase, "resolved value is stale", value.hint or "renew or republish the source in this scope"))
            continue
        if value.status == "error":
            diagnostics.append(Diagnostic("error", "malformed", service_name, name, source, phase, value.error or "provider resolution failed", value.hint or "check the provider configuration"))
            continue
        if value.value is not None and _expects_url(name) and not _valid_url(value.value):
            diagnostics.append(Diagnostic("error", "malformed", service_name, name, source, phase, "value is not a valid absolute URL/URI", "provide a value with a scheme such as https://, redis://, nats://, or mongodb://"))
    return diagnostics


def diff_values(left: dict[str, ResolvedValue], right: dict[str, ResolvedValue]) -> list[dict[str, Any]]:
    changes = []
    for name in sorted(set(left) | set(right)):
        left_value = left.get(name)
        right_value = right.get(name)
        left_fingerprint = _fingerprint(left_value.value if left_value else None)
        right_fingerprint = _fingerprint(right_value.value if right_value else None)
        left_meta = _diff_meta(left_value)
        right_meta = _diff_meta(right_value)
        if left_fingerprint != right_fingerprint or left_meta != right_meta:
            changes.append({
                "name": name,
                "value_changed": left_fingerprint != right_fingerprint,
                "left": left_meta,
                "right": right_meta,
            })
    return changes


def redact_event(event: dict[str, Any], *, reveal: bool = False) -> dict[str, Any]:
    result = dict(event)
    if not reveal and result.get("value") is not None:
        result["value"] = "<redacted>"
    return result


def _expects_url(name: str) -> bool:
    return name.endswith(("_URL", "_URI"))


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and (parsed.netloc or parsed.path))


def _fingerprint(value: str | None) -> str | None:
    return sha256(value.encode("utf-8")).hexdigest() if value is not None else None


def _diff_meta(value: ResolvedValue | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "status": value.status,
        "source": value.source,
        "layer": value.layer,
        "provider": value.provider,
        "instance": value.instance,
        "phase": value.phase,
    }
