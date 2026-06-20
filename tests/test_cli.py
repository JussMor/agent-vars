from pathlib import Path
import json

from agent_vars.cli import main


EXAMPLE = Path("agent-vars.example.yaml").resolve()


def test_validate_resolves_required_values_by_phase(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    values = tmp_path / "values.json"
    values.write_text(json.dumps({"environment_value": {"NATS_URL": "nats://dev", "REDIS_URL": "redis://dev"}}), encoding="utf-8")
    result = main([
        "--contract", str(EXAMPLE),
        "validate",
        "--environment", "dev",
        "--overlay", "preview",
        "--service", "api-gateway",
        "--phase", "runtime",
        "--values", str(values),
    ])
    assert result == 0
    assert "contract valid" in capsys.readouterr().out


def test_strict_materialize_fails_for_missing_required_values(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main([
        "--contract", str(EXAMPLE),
        "materialize", "api-gateway",
        "--environment", "dev",
        "--overlay", "preview",
        "--strict",
        "--dry-run",
    ])
    assert result == 2
    assert "missing required values" in capsys.readouterr().err


def test_publish_renew_actions_and_cleanup_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_VARS_REGISTRY", str(tmp_path / "registry.json"))
    scope = ["--environment", "dev", "--overlay", "preview", "--sandbox", "s1", "--task", "t1"]
    assert main([
        "--contract", str(EXAMPLE), "publish",
        "service.api-gateway.primary.url", "https://api.example",
        *scope, "--service", "api-gateway", "--instance", "api.1", "--ttl", "1h",
    ]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["actions"][0]["action"] == "rebuild"

    assert main(["--contract", str(EXAMPLE), "renew", "api.1", *scope, "--service", "api-gateway", "--ttl", "2h"]) == 0
    assert "renewed: 1" in capsys.readouterr().out

    assert main(["--contract", str(EXAMPLE), "actions", *scope, "--service", "frontend"]) == 0
    actions = json.loads(capsys.readouterr().out)
    assert actions[0]["service"] == "frontend"

    assert main(["--contract", str(EXAMPLE), "ack-actions", str(actions[0]["publication"])]) == 0
    assert "acknowledged: 1" in capsys.readouterr().out

    assert main(["--contract", str(EXAMPLE), "cleanup", *scope]) == 0
    assert "deleted: 1" in capsys.readouterr().out
