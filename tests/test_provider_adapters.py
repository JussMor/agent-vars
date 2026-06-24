from __future__ import annotations

import base64
import json

import pytest

from agent_vars import providers
from agent_vars.providers import ProviderError, get_secret, list_secrets
from agent_vars.workflow import approve_suggestions, write_state


@pytest.mark.parametrize("kind", ["gcp", "cloudflare", "doppler", "vault", "aws", "kubernetes", "vercel", "local", "local-encrypted"])
def test_all_provider_kinds_support_deterministic_fixtures(tmp_path, kind):
    fixture = tmp_path / f"{kind}.json"
    fixture.write_text('{"API_TOKEN":"secret"}', encoding="utf-8")
    provider = {"kind": kind, "fixture": str(fixture)}
    listed = list_secrets("profile", provider)
    assert listed[0].name == "API_TOKEN"
    assert listed[0].kind == kind
    assert get_secret("profile", provider, "API_TOKEN") == "secret"


def test_cloudflare_adapter_lists_metadata_but_payload_is_write_only(monkeypatch):
    monkeypatch.setattr(providers, "_run", lambda args: '[{"name":"API_TOKEN","type":"secret_text"}]')
    listed = list_secrets("cf", {"kind": "cloudflare", "name": "worker"})
    assert listed[0].metadata == {"type": "secret_text", "readable": False}
    with pytest.raises(ProviderError, match="write-only"):
        get_secret("cf", {"kind": "cloudflare"}, "API_TOKEN")


def test_vault_aws_and_kubernetes_payload_shapes(monkeypatch):
    def fake_run(args):
        if args[0] == "vault" and "list" in args:
            return '["API_TOKEN"]'
        if args[0] == "vault":
            return '{"data":{"data":{"value":"vault-secret"}}}'
        if args[0] == "aws" and "list-secrets" in args:
            return '{"SecretList":[{"Name":"API_TOKEN","ARN":"arn:test"}]}'
        if args[0] == "aws":
            return '{"SecretString":"aws-secret"}'
        if "secrets" in args:
            return '{"items":[{"metadata":{"name":"API_TOKEN"},"type":"Opaque"}]}'
        encoded = base64.b64encode(b"k8s-secret").decode()
        return json.dumps({"data": {"value": encoded}})

    monkeypatch.setattr(providers, "_run", fake_run)
    assert list_secrets("vault", {"kind": "vault", "path": "secret/apps"})[0].name == "API_TOKEN"
    assert get_secret("vault", {"kind": "vault", "path": "secret/apps"}, "API_TOKEN") == "vault-secret"
    assert list_secrets("aws", {"kind": "aws"})[0].metadata["arn"] == "arn:test"
    assert get_secret("aws", {"kind": "aws"}, "API_TOKEN") == "aws-secret"
    assert list_secrets("k8s", {"kind": "kubernetes", "namespace": "apps"})[0].metadata["namespace"] == "apps"
    assert get_secret("k8s", {"kind": "kubernetes", "namespace": "apps"}, "API_TOKEN") == "k8s-secret"


def test_vercel_adapter_lists_and_pulls_env_values(monkeypatch):
    providers._VERCEL_ENV_CACHE.clear()
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["vercel", "env", "ls"]:
            return "API_TOKEN Encrypted Production\nNEXT_PUBLIC_URL Encrypted Preview\n"
        if "pull" in args:
            env_path = args[args.index("pull") + 1]
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("API_TOKEN=vercel-secret\nNEXT_PUBLIC_URL=https://example.test\n")
            return "Pulled env"
        raise AssertionError(args)

    monkeypatch.setattr(providers, "_run", fake_run)
    provider = {"kind": "vercel", "environment": "preview", "git_branch": "feature-x"}

    listed = list_secrets("vercel-preview", provider)
    assert [secret.name for secret in listed] == ["API_TOKEN", "NEXT_PUBLIC_URL"]
    assert listed[0].metadata == {"environment": "preview", "git_branch": "feature-x", "readable": True}
    assert get_secret("vercel-preview", provider, "API_TOKEN") == "vercel-secret"
    assert calls[0] == ["vercel", "env", "ls", "preview", "feature-x"]
    assert "--environment=preview" in calls[1]
    assert "--git-branch=feature-x" in calls[1]
    assert "--yes" in calls[1]
    assert "--non-interactive" not in calls[0]
    assert "--non-interactive" not in calls[1]


def test_vercel_adapter_supports_json_list_output(monkeypatch):
    monkeypatch.setattr(providers, "_run", lambda args: '[{"name":"API_TOKEN"},{"key":"OTHER_TOKEN"}]')
    listed = list_secrets("vercel", {"kind": "vercel"})
    assert [secret.name for secret in listed] == ["API_TOKEN", "OTHER_TOKEN"]


def test_vercel_adapter_pulls_once_per_process(monkeypatch):
    providers._VERCEL_ENV_CACHE.clear()
    pull_count = 0

    def fake_run(args, **kwargs):
        nonlocal pull_count
        if "pull" in args:
            pull_count += 1
            env_path = args[args.index("pull") + 1]
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("API_TOKEN=one\nOTHER_TOKEN=two\n")
            return "Pulled env"
        raise AssertionError(args)

    monkeypatch.setattr(providers, "_run", fake_run)
    provider = {"kind": "vercel", "environment": "production"}

    assert get_secret("vercel-production", provider, "API_TOKEN") == "one"
    assert get_secret("vercel-production", provider, "OTHER_TOKEN") == "two"
    assert pull_count == 1


def test_encrypted_local_adapter_decrypts_json(monkeypatch):
    monkeypatch.setattr(providers, "_run", lambda args: '{"API_TOKEN":"encrypted-secret"}')
    provider = {"kind": "local-encrypted", "path": "secrets.json.age", "identity": "identity.txt"}
    assert list_secrets("encrypted", provider)[0].name == "API_TOKEN"
    assert get_secret("encrypted", provider, "API_TOKEN") == "encrypted-secret"


def test_production_and_low_confidence_suggestions_require_explicit_approval(tmp_path):
    suggestions = tmp_path / "suggestions.json"
    approvals = tmp_path / "approvals.json"
    write_state(suggestions, [{
        "service": "api", "required": "TOKEN", "provider": "prod", "suggested_source": "prod.TOKEN",
        "confidence": "low", "production_impact": True,
    }])
    assert approve_suggestions(suggestions, approvals, approve_all=True) == []
    approved = approve_suggestions(suggestions, approvals, approve_all=True, allow_production=True)
    assert approved[0]["status"] == "approved"
