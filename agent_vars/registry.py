from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json
import os
import secrets


class RegistryConflict(RuntimeError):
    """Raised when an active primary lease would be replaced implicitly."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ttl(value: str | None) -> datetime | None:
    if not value:
        return None
    unit = value[-1]
    amount = int(value[:-1]) if unit.isalpha() else int(value)
    seconds = {"s": amount, "m": amount * 60, "h": amount * 3600, "d": amount * 86400}.get(unit, amount)
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


class Registry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.environ.get("AGENT_VARS_REGISTRY", ".agent-vars/registry.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def publish(self, key: str, value: str, *, environment: str, overlay: str | None, sandbox: str | None, task: str | None, service: str | None, instance: str | None, slot: str | None, ttl: str | None, takeover: bool = False) -> RegistryEvent:
        instance = instance or f"{service or 'instance'}.{secrets.token_hex(8)}"
        status = "active"
        if slot == "primary":
            for event in self.data["events"]:
                if self._same_scope(event, environment, overlay, sandbox, task, service, slot) and not self._expired(event):
                    if event.get("status") != "active":
                        continue
                    if event.get("instance") == instance:
                        event["status"] = "superseded"
                        continue
                    if not takeover:
                        raise RegistryConflict(
                            f"primary lease is held by {event.get('instance')}; retry with --takeover to replace it"
                        )
                    event["status"] = "stale"
        event = RegistryEvent("publish", key, value, environment, overlay, sandbox, task, service, instance, slot, status, now().isoformat(), parse_ttl(ttl).isoformat() if ttl else None)
        self.data["events"].append(asdict(event))
        self._save()
        return event

    def trace(self, key: str, **scope: str | None) -> dict[str, Any] | None:
        matches = [e for e in self.data["events"] if e["key"] == key and not self._expired(e) and self._matches(e, scope)]
        return matches[-1] if matches else None

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

    def expire(self) -> int:
        count = 0
        for event in self.data["events"]:
            if event.get("status") == "active" and self._expired(event):
                event["status"] = "expired"
                count += 1
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
    def _matches(event: dict[str, Any], scope: dict[str, str | None]) -> bool:
        return all(v is None or event.get(k) == v for k, v in scope.items())
