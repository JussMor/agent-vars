from pathlib import Path
import json
import pytest

from agent_vars.contract import load_contract
from agent_vars.providers import ProviderSecret, get_secret, suggest_bindings
from agent_vars.registry import Registry, RegistryConflict
from agent_vars.scanner import scan_repo
from agent_vars.workflow import approve_suggestions, sync_provider_metadata, write_state


def test_provider_suggestions_match_env_names():
    contract = load_contract(Path("agent-vars.example.yaml"))
    suggestions = suggest_bindings(contract, "gcp-dev", [ProviderSecret("NATS_URL", "gcp-dev", "gcp", {})])
    assert any(item["service"] == "api-gateway" and item["required"] == "NATS_URL" and item["confidence"] == "high" for item in suggestions)


def test_registry_primary_publish_requires_explicit_takeover(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    registry.publish("service.api.url", "https://one", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.1", slot="primary", ttl="1h")
    with pytest.raises(RegistryConflict):
        registry.publish("service.api.url", "https://two", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.2", slot="primary", ttl="1h")
    latest = registry.publish("service.api.url", "https://two", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.2", slot="primary", ttl="1h", takeover=True)
    assert latest.value == "https://two"
    events = registry.events(environment="dev", overlay="preview", sandbox="s1")
    assert [event["status"] for event in events] == ["stale", "active"]
    assert registry.trace("service.api.url", environment="dev", overlay="preview", sandbox="s1")["value"] == "https://two"


def test_registry_resolution_falls_back_through_scopes(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    registry.publish("service.api.url", "https://env", environment="dev", overlay=None, sandbox=None, task=None, service="api", instance="api.env", slot="primary", ttl="1h")
    registry.publish("service.api.url", "https://sandbox", environment="dev", overlay="preview", sandbox="s1", task=None, service="api", instance="api.sandbox", slot="primary", ttl="1h")
    assert registry.resolve_scoped("service.api.url", environment="dev", overlay="preview", sandbox="s1", task="t1")["value"] == "https://sandbox"
    assert registry.resolve_scoped("service.api.url", environment="dev", overlay="preview", sandbox="s2", task="t2")["value"] == "https://env"


def test_scanner_discovers_env_usage(tmp_path):
    app = tmp_path / "frontend"
    app.mkdir()
    (app / ".env.example").write_text("VITE_BASE_API_URL=\n", encoding="utf-8")
    (app / "main.ts").write_text("console.log(import.meta.env.VITE_BASE_API_URL)", encoding="utf-8")
    result = scan_repo(tmp_path)
    assert result["services"]["frontend"]["requires"][0]["name"] == "VITE_BASE_API_URL"
    assert result["uncertain_mappings"]


def test_scanner_reads_templates_framework_and_compose(tmp_path):
    app = tmp_path / "frontend"
    app.mkdir()
    (app / ".env.example").write_text("VITE_API_URL=\n", encoding="utf-8")
    (app / "package.json").write_text('{"devDependencies":{"vite":"latest"}}', encoding="utf-8")
    (tmp_path / "compose.yaml").write_text("services:\n  redis:\n    image: redis\n", encoding="utf-8")
    result = scan_repo(tmp_path)
    assert result["services"]["frontend"]["framework"] == "vite"
    assert result["services"]["frontend"]["requires"][0]["name"] == "VITE_API_URL"
    assert result["runtime_dependencies"]["redis"]["outputs"]["url"] == "REDIS_URL"


def test_approval_and_sync_workflow(tmp_path):
    suggestions = tmp_path / "suggestions.json"
    approvals = tmp_path / "approvals.json"
    sync = tmp_path / "sync.json"
    write_state(suggestions, [
        {"service": "api", "required": "TOKEN", "provider": "local", "suggested_source": "local.TOKEN", "confidence": "high", "action": "approve|edit|reject"},
        {"service": "api", "required": "OTHER", "provider": "local", "suggested_source": "local.OTHER", "confidence": "low", "action": "approve|edit|reject"},
    ])
    approved = approve_suggestions(suggestions, approvals)
    assert [item["required"] for item in approved] == ["TOKEN"]
    state = sync_provider_metadata("local", [ProviderSecret("TOKEN", "local", "local", {})], approvals, sync)
    assert state["bindings"][0]["status"] == "available"


def test_gcp_payload_fixture_is_read_without_state_file(tmp_path, monkeypatch):
    fixture = tmp_path / "values.json"
    fixture.write_text('{"API_TOKEN":"secret-value"}', encoding="utf-8")
    monkeypatch.setenv("AGENT_VARS_GCP_VALUES_FILE", str(fixture))
    assert get_secret("gcp-dev", {"kind": "gcp", "project_id": "test"}, "API_TOKEN") == "secret-value"
