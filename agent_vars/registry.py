from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json
import os
import re
import secrets


class RegistryConflict(RuntimeError):
    """Raised when an active primary lease would be replaced implicitly."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ttl(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([smhd]?)", value.strip())
    if not match:
        raise ValueError("TTL must be a non-negative integer optionally followed by s, m, h, or d")
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    seconds = {"s": amount, "m": amount * 60, "h": amount * 3600, "d": amount * 86400}[unit]
    return now() + timedelta(seconds=seconds)


@dataclass
class RegistryEvent:
    type: str
    key: str
    value: str | None
    environment: str
    overlay: str | None
    sandbox: str | None
    task: str | None
    service: str | None
    instance: str | None
    slot: str | None
    status: str
    created_at: str
    expires_at: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    source: str | None = None
    provider: str | None = None
    phase: str | None = None


class Registry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.environ.get("AGENT_VARS_REGISTRY", ".agent-vars/registry.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def publish(self, key: str, value: str, *, environment: str, overlay: str | None, sandbox: str | None, task: str | None, service: str | None, instance: str | None, slot: str | None, ttl: str | None, takeover: bool = False, max_replicas: int | None = 0, actions: list[dict[str, Any]] | None = None, source: str | None = None, provider: str | None = None, phase: str | None = "runtime") -> RegistryEvent:
        instance = instance or f"{service or 'instance'}.{secrets.token_hex(8)}"
        slot = slot or "primary"
        status = "active"
        if slot == "primary":
            for event in self.data["events"]:
                if self._same_scope(event, environment, overlay, sandbox, task, service, slot) and not self._expired(event):
                    if event.get("status") != "active":
                        continue
                    if event.get("instance") == instance:
                        continue
                    if not takeover:
                        raise RegistryConflict(
                            f"primary lease is held by {event.get('instance')}; retry with --takeover to replace it"
                        )
                    event["status"] = "stale"
        elif slot == "replica":
            active_instances = {
                event.get("instance")
                for event in self.data["events"]
                if self._same_service_scope(event, environment, overlay, sandbox, task, service)
                and event.get("slot") == "replica"
                and event.get("status") == "active"
                and not self._expired(event)
            }
            if instance not in active_instances and max_replicas is not None and len(active_instances) >= max_replicas:
                raise RegistryConflict(f"replica limit reached for {service}: {max_replicas}")
        else:
            raise RegistryConflict("slot must be primary or replica")
        for prior in self.data["events"]:
            if prior.get("instance") == instance and prior.get("key") == key and prior.get("status") == "active":
                prior["status"] = "superseded"
        event = RegistryEvent(
            type="publish", key=key, value=value, environment=environment, overlay=overlay,
            sandbox=sandbox, task=task, service=service, instance=instance, slot=slot,
            status=status, created_at=now().isoformat(),
            expires_at=parse_ttl(ttl).isoformat() if ttl else None, actions=actions or [],
            source=source or key, provider=provider, phase=phase,
        )
        self.data["events"].append(asdict(event))
        self._save()
        return event

    def renew(self, instance: str, *, environment: str, overlay: str | None, sandbox: str | None, task: str | None, service: str | None, ttl: str) -> int:
        expires_at = parse_ttl(ttl)
        if expires_at is None:
            raise ValueError("renewal TTL is required")
        renewed = 0
        for event in self.data["events"]:
            if event.get("type") != "publish" or event.get("instance") != instance or event.get("status") != "active":
                continue
            if not self._same_service_scope(event, environment, overlay, sandbox, task, service):
                continue
            if self._expired(event):
                event["status"] = "stale"
                continue
            event["expires_at"] = expires_at.isoformat()
            renewed += 1
        if not renewed:
            self._save()
            raise RegistryConflict(f"no active lease found for instance {instance}")
        self.data["events"].append(asdict(RegistryEvent(
            type="renew", key="lease", value=None, environment=environment, overlay=overlay,
            sandbox=sandbox, task=task, service=service, instance=instance, slot=None,
            status="active", created_at=now().isoformat(), expires_at=expires_at.isoformat(),
            source="lease", phase="runtime",
        )))
        self._save()
        return renewed

    def trace(self, key: str, **scope: str | None) -> dict[str, Any] | None:
        matches = [
            event for event in self.data["events"]
            if event.get("type") == "publish"
            and event.get("status") == "active"
            and event.get("key") == key
            and not self._expired(event)
            and self._matches(event, scope)
        ]
        return matches[-1] if matches else None

    def diagnose(self, key: str, **scope: str | None) -> dict[str, Any] | None:
        matches = [event for event in self.data["events"] if event.get("key") == key and self._matches(event, scope)]
        if not matches:
            return None
        event = dict(matches[-1])
        if event.get("type") == "publish" and self._expired(event) and event.get("status") == "active":
            event["status"] = "stale"
        if event.get("status") == "deleted:ttl_expired":
            event["status"] = "stale"
            event["deletion_reason"] = "ttl_expired"
        return event

    def resolve_scoped(
        self,
        key: str,
        *,
        environment: str,
        overlay: str | None,
        sandbox: str | None,
        task: str | None,
    ) -> dict[str, Any] | None:
        """Resolve a publication from task to sandbox to overlay to environment scope."""
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, event in enumerate(self.data["events"]):
            if event.get("key") != key or event.get("environment") != environment or self._expired(event):
                continue
            if event.get("status") != "active":
                continue
            event_overlay = event.get("overlay")
            event_sandbox = event.get("sandbox")
            event_task = event.get("task")
            if event_task is not None:
                if task is None or event_task != task or event_sandbox != sandbox or event_overlay != overlay:
                    continue
                rank = 4
            elif event_sandbox is not None:
                if sandbox is None or event_sandbox != sandbox or event_overlay != overlay:
                    continue
                rank = 3
            elif event_overlay is not None:
                if overlay is None or event_overlay != overlay:
                    continue
                rank = 2
            else:
                rank = 1
            ranked.append((rank, index, event))
        return max(ranked, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]

    def events(self, **scope: str | None) -> list[dict[str, Any]]:
        return [e for e in self.data["events"] if self._matches(e, scope)]

    def actions(self, **scope: str | None) -> list[dict[str, Any]]:
        queued = []
        action_service = scope.pop("service", None)
        for index, event in enumerate(self.data["events"]):
            if event.get("type") != "publish" or event.get("status") != "active" or event.get("actions_acknowledged_at") or self._expired(event) or not self._matches(event, scope):
                continue
            for action in event.get("actions", []):
                if action_service is not None and action.get("service") != action_service:
                    continue
                queued.append({"publication": index, "key": event.get("key"), **action})
        return queued

    def acknowledge_actions(self, publication: int) -> int:
        try:
            event = self.data["events"][publication]
        except IndexError as exc:
            raise ValueError(f"unknown publication index: {publication}") from exc
        if event.get("type") != "publish":
            raise ValueError(f"event {publication} is not a publication")
        count = len(event.get("actions", []))
        event["actions_acknowledged_at"] = now().isoformat()
        self._save()
        return count

    def expire(self) -> int:
        count = 0
        tombstones: list[dict[str, Any]] = []
        for event in list(self.data["events"]):
            if event.get("type") == "publish" and event.get("status") == "active" and self._expired(event):
                event["status"] = "stale"
                if event.get("task") is not None or event.get("sandbox") is not None:
                    event["value"] = None
                    tombstones.append(self._tombstone(event, "ttl_expired"))
                count += 1
        self.data["events"].extend(tombstones)
        self._save()
        return count

    def cleanup(self, *, environment: str | None = None, overlay: str | None = None, sandbox: str | None = None, task: str | None = None) -> int:
        if sandbox is None and task is None:
            raise ValueError("cleanup requires --sandbox or --task")
        count = 0
        tombstones: list[dict[str, Any]] = []
        scope = {"environment": environment, "overlay": overlay, "sandbox": sandbox, "task": task}
        for event in list(self.data["events"]):
            if event.get("type") != "publish" or not self._matches(event, scope):
                continue
            if event.get("value") is None and event.get("status") == "deleted":
                continue
            event["value"] = None
            event["status"] = "deleted"
            tombstones.append(self._tombstone(event, "scope_cleanup"))
            count += 1
        self.data["events"].extend(tombstones)
        self._save()
        return count

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"events": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _expired(event: dict[str, Any]) -> bool:
        expires = event.get("expires_at")
        return bool(expires and datetime.fromisoformat(expires) <= now())

    @staticmethod
    def _same_scope(event: dict[str, Any], environment: str, overlay: str | None, sandbox: str | None, task: str | None, service: str | None, slot: str | None) -> bool:
        return all(event.get(k) == v for k, v in {"environment": environment, "overlay": overlay, "sandbox": sandbox, "task": task, "service": service, "slot": slot}.items())

    @staticmethod
    def _same_service_scope(event: dict[str, Any], environment: str, overlay: str | None, sandbox: str | None, task: str | None, service: str | None) -> bool:
        return all(event.get(k) == v for k, v in {"environment": environment, "overlay": overlay, "sandbox": sandbox, "task": task, "service": service}.items())

    @staticmethod
    def _matches(event: dict[str, Any], scope: dict[str, str | None]) -> bool:
        return all(v is None or event.get(k) == v for k, v in scope.items())

    @staticmethod
    def _tombstone(event: dict[str, Any], reason: str) -> dict[str, Any]:
        return asdict(RegistryEvent(
            type="tombstone", key=str(event.get("key", "")), value=None,
            environment=str(event.get("environment", "")), overlay=event.get("overlay"),
            sandbox=event.get("sandbox"), task=event.get("task"), service=event.get("service"),
            instance=event.get("instance"), slot=event.get("slot"), status=f"deleted:{reason}",
            created_at=now().isoformat(), source=event.get("source") or event.get("key"),
            provider=event.get("provider"), phase=event.get("phase"),
        ))


def refresh_actions(contract: dict[str, Any], changed_key: str, *, environment: str, overlay: str | None, sandbox: str | None, task: str | None) -> list[dict[str, Any]]:
    """Return scope-confined actions for services depending on a changed source."""
    actions = []
    action_for_phase = {"setup": "restart", "build": "rebuild", "runtime": "restart", "hot": "hot-refresh"}
    for service_name, service in (contract.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        for requirement in service.get("requires", []):
            if not isinstance(requirement, dict) or requirement.get("source") != changed_key:
                continue
            phase = str(requirement.get("phase", "runtime"))
            actions.append({
                "service": service_name,
                "variable": requirement.get("name"),
                "action": action_for_phase.get(phase, "restart"),
                "phase": phase,
                "scope": {"environment": environment, "overlay": overlay, "sandbox": sandbox, "task": task},
            })
    return actions
