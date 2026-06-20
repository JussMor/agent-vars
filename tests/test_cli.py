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
