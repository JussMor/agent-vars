from pathlib import Path

from agent_vars.contract import contract_summary, load_contract, materialize_service, validate_contract


EXAMPLE = Path("agent-vars.example.yaml")


def test_example_contract_validates_for_preview_dev():
    contract = load_contract(EXAMPLE)
    assert validate_contract(contract, environment="dev", overlay="preview") == []


def test_summary_counts_enterprise_shape():
    summary = contract_summary(load_contract(EXAMPLE))
    assert summary == {
        "services": 4,
        "environments": 5,
        "overlays": 1,
        "providers": 6,
        "requirements": 9,
    }


def test_materialize_file_secret_uses_mount_path_not_secret_content():
    contract = load_contract(EXAMPLE)
    rendered = materialize_service(contract, "api-gateway")
    assert "GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials/service-account.json" in rendered
    assert "NATS_URL=${NATS_URL}" in rendered


def test_unknown_runtime_source_is_actionable():
    contract = load_contract(EXAMPLE)
    contract["services"]["api-gateway"]["requires"][0]["source"] = "runtime.kafka.url"
    issues = validate_contract(contract)
    assert any("unknown runtime output" in issue.message and issue.hint for issue in issues)
