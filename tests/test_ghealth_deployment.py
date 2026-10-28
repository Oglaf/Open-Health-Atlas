"""Credential and disabled-deployment contracts for Google Health."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "deploy/ghealth-auth"
SYNC = ROOT / "deploy/ghealth-sync"
INSTALLER = ROOT / "deploy/install-ghealth-sync.sh"
SERVICE = ROOT / "deploy/hermes-ghealth-sync.service"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "deploy/docker/docker-compose.yml"
GHEALTH_EXAMPLE = ROOT / "deploy/docker/ghealth.json.example"
GITIGNORE = ROOT / ".gitignore"


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_auth_and_sync_share_the_same_external_config_default():
    auth = _load(AUTH, "ghealth_auth_contract")
    sync = _load(SYNC, "ghealth_sync_contract")
    assert auth.CONFIG == sync.CONFIG == "/etc/hermes/ghealth.json"


def test_client_secret_uses_hidden_prompt_or_protected_file(tmp_path, monkeypatch):
    auth = _load(AUTH, "ghealth_auth_secret")
    monkeypatch.setattr(auth.getpass, "getpass", lambda _prompt: "fictional-secret")
    assert auth._load_client_secret(None) == "fictional-secret"
    secret = tmp_path / "client-secret"
    secret.write_text("fictional-file-secret\n")
    secret.chmod(0o600)
    assert auth._load_client_secret(str(secret)) == "fictional-file-secret"
    secret.chmod(0o644)
    with pytest.raises(SystemExit, match="mode 0600"):
        auth._load_client_secret(str(secret))
    assert "--client-secret\"" not in AUTH.read_text()


def test_installer_and_service_are_disabled_credential_bound_and_valid():
    installer = INSTALLER.read_text()
    service = SERVICE.read_text()
    commands = [line.strip() for line in installer.splitlines()]
    assert not any(line.startswith("systemctl enable") for line in commands)
    assert not any(line.startswith("systemctl start") for line in commands)
    assert "systemctl start" in installer  # printed operator instruction only
    assert "LoadCredential=ghealth.json:/etc/hermes/ghealth.json" in service
    assert "User=root" in service and "ProtectSystem=strict" in service
    checked = subprocess.run(
        ["sh", "-n", str(INSTALLER)], capture_output=True, text=True, timeout=30
    )
    assert checked.returncode == 0, checked.stderr


# ── container mode: Dockerfile / docker-compose.yml / example credentials ──
# Fork owner decision 2026-10-28 (docs/PRODUCT_AUTHORITY.md "Fork deployment:
# scheduled Google Health collector container"). No YAML parser is a project
# dependency, so these are plain-text contract checks, matching the installer
# checks above.

def _service_block(compose_text, name):
    """The named top-level service's own body, stopping at the next top-level
    service/section key (so assertions can't accidentally match a sibling
    service like `panel` or `broker`)."""
    match = re.search(
        rf"\n  {re.escape(name)}:\n(.*?)(?=\n  [A-Za-z]|\nnetworks:|\nvolumes:|\Z)",
        compose_text, re.DOTALL,
    )
    assert match, f"service {name!r} not found in docker-compose.yml"
    return match.group(1)


def test_dockerfile_copies_ghealth_sync_into_the_image():
    text = DOCKERFILE.read_text()
    assert re.search(r"^COPY .*\bdeploy/ghealth-sync\b", text, re.MULTILINE), (
        "deploy/ghealth-sync must be COPYed into the image for container mode")
    assert re.search(r"^RUN chmod 0755 .*\bdeploy/ghealth-sync\b", text, re.MULTILINE)


def test_compose_ghealth_service_is_profile_gated_and_hardened():
    block = _service_block(COMPOSE.read_text(), "ghealth")
    assert 'profiles: ["ghealth"]' in block, "must never start on a bare `docker compose up`"
    assert 'command: ["ghealth"]' in block
    assert "read_only: true" in block
    assert "- /tmp" in block        # tmpfs
    assert "- ALL" in block         # cap_drop: [ALL]
    assert "no-new-privileges:true" in block
    assert "HERMES_GHEALTH_MODE: container" in block
    assert "HERMES_GHEALTH_CONFIG: /creds/ghealth.json" in block
    assert "ghealth.json:/creds/ghealth.json:ro" in block
    assert "ghealth-health" in block   # healthcheck uses the helper


def test_compose_ghealth_service_is_not_on_the_public_proxy_network():
    block = _service_block(COMPOSE.read_text(), "ghealth")
    assert "internal" in block
    assert "proxy" not in block


def test_ghealth_example_credentials_file_has_only_placeholders():
    data = json.loads(GHEALTH_EXAMPLE.read_text())
    assert set(data) == {"client_id", "client_secret", "refresh_token"}
    for value in data.values():
        assert isinstance(value, str) and value.startswith("replace-with-")


def test_gitignore_excludes_the_real_ghealth_json_but_keeps_the_example():
    lines = GITIGNORE.read_text().splitlines()
    assert "ghealth.json" in lines
    assert "!ghealth.json.example" in lines
