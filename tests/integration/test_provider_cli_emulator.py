from __future__ import annotations

import os

from agent_vars.providers import get_secret, list_secrets


EMULATOR = r'''#!/usr/bin/env python3
import base64
import json
import sys

args = sys.argv[1:]
if args[:2] == ["secret", "list"]:
    print(json.dumps([{"name": "API_TOKEN", "type": "secret_text"}]))
elif args[:2] == ["kv", "list"]:
    print(json.dumps(["API_TOKEN"]))
elif args[:2] == ["kv", "get"]:
    print(json.dumps({"data": {"data": {"value": "vault-secret"}}}))
elif args[:2] == ["secretsmanager", "list-secrets"]:
    print(json.dumps({"SecretList": [{"Name": "API_TOKEN", "ARN": "arn:emulator"}]}))
elif args[:2] == ["secretsmanager", "get-secret-value"]:
    print(json.dumps({"SecretString": "aws-secret"}))
elif args[:2] == ["get", "secrets"]:
    print(json.dumps({"items": [{"metadata": {"name": "API_TOKEN"}, "type": "Opaque"}]}))
elif args[:2] == ["get", "secret"]:
    print(json.dumps({"data": {"value": base64.b64encode(b"k8s-secret").decode()}}))
elif "--decrypt" in args:
    print(json.dumps({"API_TOKEN": "encrypted-secret"}))
elif args[:2] == ["secrets", "list"]:
    print(json.dumps([{"name": "projects/test/secrets/API_TOKEN"}]))
elif args[:3] == ["secrets", "versions", "access"]:
    print("gcp-secret")
elif args[:2] == ["secrets", "download"]:
    print(json.dumps({"API_TOKEN": "doppler-secret"}))
elif args[:2] == ["env", "ls"] or args[:3] == ["--non-interactive", "env", "ls"]:
    print("API_TOKEN Encrypted Production")
elif "env" in args and "pull" in args:
    env_path = args[args.index("pull") + 1]
    with open(env_path, "w", encoding="utf-8") as handle:
        handle.write("API_TOKEN=vercel-secret\n")
    print("Pulled env")
else:
    print("unsupported emulator command", file=sys.stderr)
    raise SystemExit(2)
'''


def test_provider_adapters_execute_cli_protocols_end_to_end(tmp_path):
    executable = tmp_path / "provider-emulator"
    executable.write_text(EMULATOR, encoding="utf-8")
    executable.chmod(0o755)
    binary = os.fspath(executable)

    cloudflare = {"kind": "cloudflare", "executable": binary, "name": "worker"}
    vault = {"kind": "vault", "executable": binary, "path": "secret/apps"}
    aws = {"kind": "aws", "executable": binary, "region": "us-test-1"}
    kubernetes = {"kind": "kubernetes", "executable": binary, "namespace": "apps"}
    encrypted = {"kind": "local-encrypted", "executable": binary, "path": "dev.json.age"}
    gcp = {"kind": "gcp", "executable": binary, "project_id": "test"}
    doppler = {"kind": "doppler", "executable": binary}
    vercel = {"kind": "vercel", "executable": binary, "environment": "production"}

    assert list_secrets("cf", cloudflare)[0].name == "API_TOKEN"
    assert list_secrets("vault", vault)[0].name == "API_TOKEN"
    assert get_secret("vault", vault, "API_TOKEN") == "vault-secret"
    assert list_secrets("aws", aws)[0].metadata["arn"] == "arn:emulator"
    assert get_secret("aws", aws, "API_TOKEN") == "aws-secret"
    assert list_secrets("k8s", kubernetes)[0].name == "API_TOKEN"
    assert get_secret("k8s", kubernetes, "API_TOKEN") == "k8s-secret"
    assert list_secrets("encrypted", encrypted)[0].name == "API_TOKEN"
    assert get_secret("encrypted", encrypted, "API_TOKEN") == "encrypted-secret"
    assert list_secrets("gcp", gcp)[0].name == "API_TOKEN"
    assert get_secret("gcp", gcp, "API_TOKEN").strip() == "gcp-secret"
    assert list_secrets("doppler", doppler)[0].name == "API_TOKEN"
    assert get_secret("doppler", doppler, "API_TOKEN") == "doppler-secret"
    assert list_secrets("vercel", vercel)[0].name == "API_TOKEN"
    assert get_secret("vercel", vercel, "API_TOKEN") == "vercel-secret"
