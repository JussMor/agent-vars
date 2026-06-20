from __future__ import annotations

from hashlib import sha256
import json
import os

import pytest

from agent_vars.providers import get_secret, list_secrets


pytestmark = pytest.mark.gcp_integration


def test_real_gcp_secret_manager_list_and_access_without_payload_logging():
    project = os.environ.get("AGENT_VARS_GCP_INTEGRATION_PROJECT")
    secret_name = os.environ.get("AGENT_VARS_GCP_INTEGRATION_SECRET")
    if not project or not secret_name:
        pytest.skip("set AGENT_VARS_GCP_INTEGRATION_PROJECT and AGENT_VARS_GCP_INTEGRATION_SECRET")

    provider = {"kind": "gcp", "project_id": project}
    resources = list_secrets("gcp-integration", provider)
    assert secret_name in {resource.name for resource in resources}

    payload = get_secret("gcp-integration", provider, secret_name)
    assert isinstance(payload, str)
    assert len(sha256(payload.encode("utf-8")).hexdigest()) == 64
    if os.environ.get("AGENT_VARS_GCP_INTEGRATION_EXPECT_JSON") == "1":
        assert isinstance(json.loads(payload), dict)
