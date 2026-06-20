from pathlib import Path
import json

from agent_vars.contract import load_contract
from agent_vars.providers import ProviderSecret, suggest_bindings
from agent_vars.registry import Registry
from agent_vars.scanner import scan_repo


def test_provider_suggestions_match_env_names():
    contract = load_contract(Path("agent-vars.example.yaml"))
    suggestions = suggest_bindings(contract, "gcp-dev", [ProviderSecret("NATS_URL", "gcp-dev", "gcp", {})])
    assert any(item["service"] == "api-gateway" and item["required"] == "NATS_URL" and item["confidence"] == "high" for item in suggestions)


def test_registry_primary_publish_marks_previous_stale(tmp_path):
    registry = Registry(tmp_path / "registry.json")
    registry.publish("service.api.url", "https://one", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.1", slot="primary", ttl="1h")
    latest = registry.publish("service.api.url", "https://two", environment="dev", overlay="preview", sandbox="s1", task="t1", service="api", instance="api.2", slot="primary", ttl="1h")
    assert latest.value == "https://two"
    events = registry.events(environment="dev", overlay="preview", sandbox="s1")
    assert [event["status"] for event in events] == ["stale", "active"]
    assert registry.trace("service.api.url", environment="dev", overlay="preview", sandbox="s1")["value"] == "https://two"


def test_scanner_discovers_env_usage(tmp_path):
    app = tmp_path / "frontend"
    app.mkdir()
    (app / ".env.example").write_text("VITE_BASE_API_URL=\n", encoding="utf-8")
    (app / "main.ts").write_text("console.log(import.meta.env.VITE_BASE_API_URL)", encoding="utf-8")
    result = scan_repo(tmp_path)
    assert result["services"]["frontend"]["requires"][0]["name"] == "VITE_BASE_API_URL"
    assert result["uncertain_mappings"]
