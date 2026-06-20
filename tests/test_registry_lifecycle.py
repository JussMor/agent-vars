from pathlib import Path

import pytest

from agent_vars.contract import load_contract
from agent_vars.registry import Registry, RegistryConflict, parse_ttl, refresh_actions


def publish(registry, *, instance, slot="primary", ttl="1h", max_replicas=0, task="t1"):
    return registry.publish(
        "service.api.primary.url",
        f"https://{instance}",
        environment="dev",
        overlay="preview",
        sandbox="s1",
        task=task,
        service="api",
        instance=instance,
        slot=slot,
        ttl=ttl,
        max_replicas=max_replicas,
    )


def test_replica_policy_enforces_limit(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    publish(registry, instance="api.replica.1", slot="replica", max_replicas=1)
    with pytest.raises(RegistryConflict, match="replica limit"):
        publish(registry, instance="api.replica.2", slot="replica", max_replicas=1)


def test_one_primary_instance_can_publish_multiple_outputs(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    publish(registry, instance="api.1")
    registry.publish("service.api.primary.health", "ok", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.1", slot="primary", ttl="1h")
    assert registry.trace("service.api.primary.url", environment="dev", sandbox="s1")["value"] == "https://api.1"
    assert registry.trace("service.api.primary.health", environment="dev", sandbox="s1")["value"] == "ok"


def test_renew_extends_active_instance_lease(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    event = publish(registry, instance="api.1", ttl="1m")
    original_expiry = event.expires_at
    assert registry.renew("api.1", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", ttl="2h") == 1
    renewed = registry.trace("service.api.primary.url", environment="dev", overlay="preview", sandbox="s1", task="t1")
    assert renewed["expires_at"] > original_expiry
    assert any(item["type"] == "renew" for item in registry.events(environment="dev", sandbox="s1"))


def test_expiry_stales_output_scrubs_value_and_adds_tombstone(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    publish(registry, instance="api.1", ttl="0s")
    assert registry.expire() == 1
    events = registry.events(environment="dev", sandbox="s1")
    assert events[0]["status"] == "stale"
    assert events[0]["value"] is None
    assert events[-1]["type"] == "tombstone"
    assert events[-1]["status"] == "deleted:ttl_expired"


def test_scope_cleanup_deletes_only_matching_scope(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    publish(registry, instance="api.s1", task="t1")
    registry.publish("service.api.primary.url", "https://s2", environment="dev", overlay="preview", sandbox="s2", task="t2", service="api", instance="api.s2", slot="primary", ttl="1h")
    assert registry.cleanup(environment="dev", sandbox="s1", task="t1") == 1
    assert registry.trace("service.api.primary.url", environment="dev", sandbox="s1", task="t1") is None
    assert registry.trace("service.api.primary.url", environment="dev", sandbox="s2", task="t2")["value"] == "https://s2"


def test_refresh_actions_are_confined_to_publication_scope():
    contract = load_contract(Path("agent-vars.example.yaml"))
    actions = refresh_actions(
        contract,
        "service.api-gateway.primary.url",
        environment="dev",
        overlay="preview",
        sandbox="s1",
        task="t1",
    )
    assert actions == [{
        "service": "frontend",
        "variable": "VITE_BASE_API_URL",
        "action": "rebuild",
        "phase": "build",
        "scope": {"environment": "dev", "overlay": "preview", "sandbox": "s1", "task": "t1"},
    }]


def test_publication_queues_scope_confined_actions(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    actions = [{"service": "frontend", "action": "rebuild", "scope": {"sandbox": "s1"}}]
    registry.publish("service.api.url", "https://api", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.1", slot="primary", ttl="1h", actions=actions)
    assert registry.actions(environment="dev", sandbox="s1", service="frontend")[0]["action"] == "rebuild"
    assert registry.actions(environment="dev", sandbox="s2") == []
    assert registry.acknowledge_actions(0) == 1
    assert registry.actions(environment="dev", sandbox="s1") == []


def test_ttl_rejects_unknown_units():
    with pytest.raises(ValueError, match="TTL"):
        parse_ttl("2weeks")
