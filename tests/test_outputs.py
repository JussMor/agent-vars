from pathlib import Path

import pytest

from agent_vars.contract import load_contract, validate_contract
from agent_vars.outputs import validate_service_output


EXAMPLE = Path("agent-vars.example.yaml")


def test_service_output_schema_accepts_valid_url():
    schema = validate_service_output(load_contract(EXAMPLE), "api-gateway", "service.api-gateway.primary.url", "https://api.example", slot="primary")
    assert schema["type"] == "url"


def test_service_output_schema_rejects_malformed_or_undeclared_output():
    contract = load_contract(EXAMPLE)
    with pytest.raises(ValueError, match="not valid url"):
        validate_service_output(contract, "api-gateway", "service.api-gateway.primary.url", "not-a-url")
    with pytest.raises(ValueError, match="undeclared output"):
        validate_service_output(contract, "api-gateway", "service.api-gateway.primary.health", "ok")


def test_service_output_schema_requires_slot_qualified_matching_key():
    contract = load_contract(EXAMPLE)
    with pytest.raises(ValueError, match="output key must"):
        validate_service_output(contract, "api-gateway", "service.other.url", "https://api.example")
    with pytest.raises(ValueError, match="does not match --slot"):
        validate_service_output(contract, "api-gateway", "service.api-gateway.primary.url", "https://api.example", slot="replica")


def test_contract_validator_checks_output_schema():
    contract = load_contract(EXAMPLE)
    contract["services"]["api-gateway"]["outputs"]["url"]["type"] = "socket"
    issues = validate_contract(contract)
    assert any("unsupported output type" in issue.message and issue.hint for issue in issues)
