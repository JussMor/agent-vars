from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_vars.contract import load_contract, materialize_service
from agent_vars.materializer import cleanup_file_secrets, materialize_file_secrets
from agent_vars.registry import Registry
from agent_vars.resolution import missing_required, resolve_service


EXAMPLE = Path("agent-vars.example.yaml")


def test_layered_resolution_uses_documented_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    contract = load_contract(EXAMPLE)
    layers = {
        "repo_default": {"NATS_URL": "nats://repo", "REDIS_URL": "redis://repo"},
        "environment_value": {"api-gateway": {"NATS_URL": "nats://dev"}},
        "task_override": {"NATS_URL": "nats://task"},
    }
    resolved = resolve_service(
        contract,
        "api-gateway",
        environment="dev",
        overlay="preview",
        sandbox="sandbox-1",
        task="task-1",
        layers=layers,
        registry=Registry(tmp_path / "registry.json"),
    )
    assert resolved["NATS_URL"].value == "nats://task"
    assert resolved["NATS_URL"].layer == "task_override"
    assert resolved["REDIS_URL"].value == "redis://repo"
    assert missing_required(contract["services"]["api-gateway"], resolved) == []


def test_contract_environment_and_overlay_values_are_inherited(tmp_path, monkeypatch):
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    contract = load_contract(EXAMPLE)
    contract["environments"]["dev"]["values"] = {"NATS_URL": "nats://dev", "REDIS_URL": "redis://dev"}
    contract["overlays"]["preview"]["values"] = {"NATS_URL": "nats://preview"}
    resolved = resolve_service(
        contract,
        "api-gateway",
        environment="dev",
        overlay="preview",
        registry=Registry(tmp_path / "registry.json"),
    )
    assert resolved["NATS_URL"].value == "nats://preview"
    assert resolved["NATS_URL"].layer == "overlay_value"
    assert resolved["REDIS_URL"].value == "redis://dev"
    assert resolved["REDIS_URL"].layer == "environment_value"


def test_environment_provider_profile_resolves_payload(tmp_path, monkeypatch):
    contract = load_contract(EXAMPLE)
    contract["services"]["api-gateway"]["requires"] = [{
        "name": "API_TOKEN",
        "source": "review.required",
        "visibility": "secret",
        "phase": "runtime",
        "required": True,
    }]
    fixture = tmp_path / "gcp-values.json"
    fixture.write_text('{"API_TOKEN":"provider-value"}', encoding="utf-8")
    monkeypatch.setenv("AGENT_VARS_GCP_VALUES_FILE", str(fixture))
    resolved = resolve_service(
        contract,
        "api-gateway",
        environment="dev",
        overlay="preview",
        registry=Registry(tmp_path / "registry.json"),
    )
    assert resolved["API_TOKEN"].value == "provider-value"
    assert resolved["API_TOKEN"].provider == "gcp-dev"
    assert resolved["API_TOKEN"].layer == "provider_secret"


def test_service_output_resolution_carries_instance_provenance(tmp_path):
    contract = load_contract(EXAMPLE)
    registry = Registry(tmp_path / "registry.json")
    registry.publish(
        "service.api-gateway.primary.url",
        "https://api.example",
        environment="dev",
        overlay="preview",
        sandbox="s1",
        task="t1",
        service="api-gateway",
        instance="api.1",
        slot="primary",
        ttl="1h",
    )
    resolved = resolve_service(contract, "frontend", environment="dev", overlay="preview", sandbox="s1", task="t1", registry=registry)
    assert resolved["VITE_BASE_API_URL"].value == "https://api.example"
    assert resolved["VITE_BASE_API_URL"].instance == "api.1"
    assert resolved["VITE_BASE_API_URL"].provenance()["value"] == "<redacted>"


def test_materializer_writes_valid_json_atomically_with_private_mode(tmp_path):
    contract = load_contract(EXAMPLE)
    written = materialize_file_secrets(
        contract,
        "api-gateway",
        environment="dev",
        mount_root=tmp_path,
        payloads={"gcp_service_account": {"type": "service_account", "project_id": "test"}},
    )
    target = tmp_path / "app/gcp-credentials/service-account.json"
    assert written == [target]
    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "service_account"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_materializer_rejects_invalid_json(tmp_path):
    with pytest.raises(ValueError, match="not valid JSON"):
        materialize_file_secrets(
            load_contract(EXAMPLE),
            "api-gateway",
            environment="dev",
            mount_root=tmp_path,
            payloads={"gcp_service_account": "not-json"},
        )


def test_materializer_enforces_json_shape_and_size_policies(tmp_path):
    contract = load_contract(EXAMPLE)
    with pytest.raises(ValueError, match="required JSON keys"):
        materialize_file_secrets(contract, "api-gateway", environment="dev", mount_root=tmp_path, payloads={"gcp_service_account": {"type": "service_account"}})
    contract["files"]["gcp_service_account"]["policy"]["max_bytes"] = 2
    with pytest.raises(ValueError, match="max_bytes"):
        materialize_file_secrets(contract, "api-gateway", environment="dev", mount_root=tmp_path, payloads={"gcp_service_account": {"type": "service_account", "project_id": "test"}})


def test_mount_manifest_supports_safe_lifecycle_cleanup(tmp_path):
    contract = load_contract(EXAMPLE)
    manifest = tmp_path / ".mounts.json"
    written = materialize_file_secrets(
        contract, "api-gateway", environment="dev", mount_root=tmp_path,
        payloads={"gcp_service_account": {"type": "service_account", "project_id": "test"}},
        manifest_path=manifest,
    )
    assert manifest.exists()
    removed = cleanup_file_secrets(tmp_path, manifest, service_name="api-gateway")
    assert removed == written
    assert not written[0].exists()
    assert not manifest.exists()


def test_cleanup_refuses_to_delete_modified_mount_without_force(tmp_path):
    contract = load_contract(EXAMPLE)
    manifest = tmp_path / ".mounts.json"
    target = materialize_file_secrets(
        contract, "api-gateway", environment="dev", mount_root=tmp_path,
        payloads={"gcp_service_account": {"type": "service_account", "project_id": "test"}},
        manifest_path=manifest,
    )[0]
    target.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to remove modified"):
        cleanup_file_secrets(tmp_path, manifest)
    assert cleanup_file_secrets(tmp_path, manifest, force=True) == [target]


def test_rendered_env_uses_resolved_scalar_values(tmp_path, monkeypatch):
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    contract = load_contract(EXAMPLE)
    resolved = resolve_service(
        contract,
        "api-gateway",
        environment="dev",
        overlay="preview",
        layers={"environment_value": {"NATS_URL": "nats://dev", "REDIS_URL": "redis://dev"}},
        registry=Registry(tmp_path / "registry.json"),
    )
    rendered = materialize_service(contract, "api-gateway", resolved)
    assert "NATS_URL=nats://dev" in rendered
    assert "REDIS_URL=redis://dev" in rendered
