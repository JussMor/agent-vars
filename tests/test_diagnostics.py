from pathlib import Path

from agent_vars.contract import load_contract
from agent_vars.diagnostics import diagnose_service, diff_values, redact_event
from agent_vars.registry import Registry
from agent_vars.resolution import resolve_service


EXAMPLE = Path("agent-vars.example.yaml")


def test_doctor_diagnoses_malformed_and_missing_values(tmp_path, monkeypatch):
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    contract = load_contract(EXAMPLE)
    contract["environments"]["dev"].pop("provider_profile")
    resolved = resolve_service(
        contract, "api-gateway", environment="dev", overlay="preview",
        layers={"environment_value": {"NATS_URL": "not-a-url"}},
        registry=Registry(tmp_path / "registry.json"),
    )
    diagnostics = diagnose_service(contract, "api-gateway", resolved)
    statuses = {(item.variable, item.status) for item in diagnostics}
    assert ("NATS_URL", "malformed") in statuses
    assert ("REDIS_URL", "missing") in statuses
    assert all(item.hint for item in diagnostics)


def test_stale_registry_output_is_visible_to_diagnostics(tmp_path):
    contract = load_contract(EXAMPLE)
    registry = Registry(tmp_path / "registry.json")
    registry.publish("service.api-gateway.primary.url", "https://api", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api-gateway", instance="api.1", slot="primary", ttl="0s")
    resolved = resolve_service(contract, "frontend", environment="dev", overlay="preview", sandbox="s1", task="t1", registry=registry)
    diagnostics = diagnose_service(contract, "frontend", resolved)
    assert any(item.variable == "VITE_BASE_API_URL" and item.status == "stale" for item in diagnostics)


def test_value_diff_reports_change_without_revealing_values(tmp_path):
    contract = load_contract(EXAMPLE)
    left = resolve_service(contract, "api-gateway", environment="dev", overlay=None, layers={"environment_value": {"NATS_URL": "nats://left"}}, registry=Registry(tmp_path / "left.json"))
    right = resolve_service(contract, "api-gateway", environment="qa", overlay=None, layers={"environment_value": {"NATS_URL": "nats://right"}}, registry=Registry(tmp_path / "right.json"))
    changes = diff_values(left, right)
    nats = next(item for item in changes if item["name"] == "NATS_URL")
    assert nats["value_changed"] is True
    assert "nats://" not in str(nats)


def test_event_redaction_preserves_complete_provenance(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    event = registry.publish("service.api.url", "https://secret", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.1", slot="primary", ttl="1h", provider="runtime", phase="hot")
    redacted = redact_event(event.__dict__)
    assert redacted["value"] == "<redacted>"
    for field in ("source", "provider", "service", "environment", "overlay", "phase", "sandbox", "instance", "status", "created_at"):
        assert field in redacted
