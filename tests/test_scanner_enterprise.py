from pathlib import Path

from agent_vars.contract import materialize_service, validate_contract
from agent_vars.scanner import scan_repo, scan_workspace


def test_scanner_parses_ci_manifests_scripts_and_credential_paths(tmp_path):
    app = tmp_path / "apps" / "web"
    app.mkdir(parents=True)
    (app / "package.json").write_text(
        '{"scripts":{"build":"VITE_API_URL=$VITE_API_URL node build.js"},"devDependencies":{"vite":"latest"}}',
        encoding="utf-8",
    )
    (app / "vite.config.ts").write_text(
        "const credentials = '/app/gcp-credentials/service-account.json'; console.log(process.env.CONFIG_TOKEN)",
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "deploy.yml").write_text(
        "jobs:\n  deploy:\n    strategy:\n      matrix:\n        environment: [dev, qa, prod]\n    environment: ${{ matrix.environment }}\n",
        encoding="utf-8",
    )
    manifests = tmp_path / "k8s"
    manifests.mkdir()
    (manifests / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n        - env:\n            - name: API_TOKEN\n              valueFrom:\n                secretKeyRef:\n                  name: api-secrets\n                  key: token\n",
        encoding="utf-8",
    )
    terraform = tmp_path / "infra"
    terraform.mkdir()
    (terraform / "main.tf").write_text(
        'variable "region" {}\noutput "api_url" { value = google_cloud_run_service.api.status[0].url }\n',
        encoding="utf-8",
    )
    helm = tmp_path / "helm"
    helm.mkdir()
    (helm / "values.yaml").write_text("image:\n  repository: company/api\nconfig:\n  logLevel: info\n", encoding="utf-8")

    profile = scan_repo(tmp_path)

    assert profile["repo"]["topology"] == "single"
    assert profile["environments"] == {"dev": {}, "prod": {}, "qa": {}}
    assert profile["evidence"]["ci"]["matrices"][0]["values"]["environment"] == ["dev", "qa", "prod"]
    assert any(item["name"] == "API_TOKEN" for item in profile["evidence"]["infrastructure"]["kubernetes"])
    assert any(item["kind"] == "output" and item["name"] == "api_url" for item in profile["evidence"]["infrastructure"]["terraform"])
    assert any(item["name"] == "image.repository" for item in profile["evidence"]["infrastructure"]["helm"])
    assert profile["evidence"]["credential_paths"][0]["path"] == "/app/gcp-credentials/service-account.json"
    requirements = profile["services"]["web"]["requires"]
    assert {item["name"] for item in requirements} == {"CONFIG_TOKEN", "VITE_API_URL"}


def test_scanner_coordinates_multiple_repositories(tmp_path):
    first = tmp_path / "frontend"
    second = tmp_path / "api"
    first.mkdir()
    second.mkdir()
    (first / ".env.example").write_text("VITE_API_URL=\n", encoding="utf-8")
    (second / ".env.example").write_text("DATABASE_URL=\n", encoding="utf-8")

    workspace = scan_workspace([first, second])

    assert workspace["repo"] == {"topology": "multi-repo", "repositories": ["frontend", "api"]}
    assert set(workspace["repositories"]) == {"frontend", "api"}
    assert set(workspace["services"]) == {"frontend:frontend", "api:api"}
    assert {item["repository"] for item in workspace["uncertain_mappings"]} == {"frontend", "api"}


def test_scanner_promotes_root_next_app_env_evidence_to_service_requirements(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"everbetter-pro","dependencies":{"next":"latest"}}', encoding="utf-8")
    (tmp_path / ".env.local.example").write_text(
        "APP_BASE_URL=\nAUTH0_CLIENT_SECRET=\nNEXT_PUBLIC_MAXWELL_API_BASE_URL=\n",
        encoding="utf-8",
    )
    src = tmp_path / "src" / "app" / "api" / "status"
    src.mkdir(parents=True)
    (src / "route.ts").write_text(
        "console.log(process.env.MAXWELL_API_BASE_URL, process.env.NEXT_PUBLIC_WS_URL)",
        encoding="utf-8",
    )
    stub = tmp_path / "prism-stub"
    stub.mkdir()
    (stub / "package.json").write_text('{"name":"prism-stub"}', encoding="utf-8")

    profile = scan_repo(tmp_path)

    assert profile["repo"]["topology"] == "monorepo"
    assert set(profile["services"]) == {"everbetter-pro", "prism-stub"}
    app = profile["services"]["everbetter-pro"]
    requirements = {item["name"]: item for item in app["requires"]}
    assert set(requirements) == {
        "APP_BASE_URL",
        "AUTH0_CLIENT_SECRET",
        "MAXWELL_API_BASE_URL",
        "NEXT_PUBLIC_MAXWELL_API_BASE_URL",
        "NEXT_PUBLIC_WS_URL",
    }
    assert requirements["APP_BASE_URL"]["source"] == "APP_BASE_URL"
    assert requirements["AUTH0_CLIENT_SECRET"]["visibility"] == "secret"
    assert requirements["NEXT_PUBLIC_MAXWELL_API_BASE_URL"]["phase"] == "build"
    assert app["env_files"] == [".env.local.example"]
    assert app["framework"] == "next"
    assert validate_contract(profile) == []
    rendered = materialize_service(profile, "everbetter-pro")
    assert "APP_BASE_URL=${APP_BASE_URL}" in rendered
    assert "NEXT_PUBLIC_MAXWELL_API_BASE_URL=${NEXT_PUBLIC_MAXWELL_API_BASE_URL}" in rendered


def test_scanner_maps_monorepo_package_evidence_to_nearest_service(tmp_path):
    api = tmp_path / "apps" / "api"
    web = tmp_path / "apps" / "web"
    api.mkdir(parents=True)
    web.mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"private":true,"workspaces":["apps/*"]}', encoding="utf-8")
    (api / "package.json").write_text('{"name":"api-service"}', encoding="utf-8")
    (api / ".env.example").write_text("DATABASE_URL=\n", encoding="utf-8")
    (api / "server.ts").write_text("console.log(process.env.API_TOKEN)", encoding="utf-8")
    (web / "package.json").write_text('{"name":"web","devDependencies":{"vite":"latest"}}', encoding="utf-8")
    (web / ".env.example").write_text("VITE_API_URL=\n", encoding="utf-8")
    (web / "main.ts").write_text("console.log(import.meta.env.VITE_PUBLIC_FLAG)", encoding="utf-8")

    profile = scan_repo(tmp_path)

    assert profile["repo"]["topology"] == "monorepo"
    assert set(profile["services"]) == {"api-service", "web"}
    assert {item["name"] for item in profile["services"]["api-service"]["requires"]} == {"API_TOKEN", "DATABASE_URL"}
    assert {item["name"] for item in profile["services"]["web"]["requires"]} == {"VITE_API_URL", "VITE_PUBLIC_FLAG"}
    assert profile["services"]["web"]["framework"] == "vite"
