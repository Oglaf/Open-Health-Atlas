"""Release scanner fails closed without echoing matched sensitive values."""

import hashlib
import subprocess

import pytest

from scripts import release_scan


def test_blob_scan_reports_only_sanitized_categories_and_paths():
    private_path = "/" + "Users/example/private/input.db"
    secret_value = "highentropy" + "fixturevalue"
    secret_line = "client_" + f"secret = \"{secret_value}\""
    findings = release_scan._scan_blob(
        f"{private_path}\n{secret_line}\n".encode(),
        "synthetic/example.txt",
    )
    rendered = [item.as_dict() for item in findings]
    assert {item["category"] for item in rendered} == {
        "private_absolute_home_path",
        "literal_secret_assignment",
    }
    assert all(item["path"] == "synthetic/example.txt" for item in rendered)
    assert secret_value not in repr(rendered)
    assert private_path not in repr(rendered)


def test_safe_generic_examples_and_public_provenance_are_accepted():
    text = "\n".join((
        "PANEL_SECRET_KEY=<replace-with-random-value>",
        'PANEL_SECRET_KEY="$(openssl rand -hex 32)"',
        "support@example.invalid",
        "git@github.com:OWNER/Hermes-Health-Open-Source.git",
        "http://localhost:5111",
        "https://github.com/apache/echarts",
        "https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution",
        "https://files.pythonhosted.org/packages/reviewed/package-source.tar.gz",
        "https://core.telegram.org/bots/features#creating-a-new-bot",
        "https://developers.google.com/health/setup",
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/",
        "https://hevy.com/settings?developer",
        "https://www.hostinger.com/support/5726606-how-to-use-the-vps-dashboard-in-hostinger/",
        "https://fsf.org/",
        "https://www.gnu.org/licenses/",
        "https://api.open-meteo.com/v1/forecast",
        "198.51.100.77",
        "/var/lib/hermes/health.db",
    ))
    assert release_scan._scan_blob(text.encode(), ".env.example") == []


def test_private_network_address_is_rejected_but_value_is_not_rendered():
    private_address = "10.23." + "45.67"
    findings = release_scan._scan_blob(
        f"endpoint={private_address}\n".encode(),
        "synthetic/network.example",
    )
    assert [item.as_dict() for item in findings] == [{
        "category": "private_ipv4_address",
        "path": "synthetic/network.example",
    }]
    assert private_address not in repr(findings)


def test_removed_personal_fixture_term_is_rejected_without_echoing_it():
    removed_term = "butter" + " chicken"
    findings = release_scan._scan_blob(
        f"fixture={removed_term}\n".encode(),
        "synthetic/meal.txt",
    )
    assert [item.as_dict() for item in findings] == [{
        "category": "removed_personal_fixture_term",
        "path": "synthetic/meal.txt",
    }]
    assert removed_term not in repr(findings)


def test_path_classifier_rejects_data_secrets_caches_and_submodule_metadata():
    cases = {
        "data/health.db": "data_secret_or_generated_suffix",
        "cache/__pycache__/module.pyc": "generated_or_dependency_path",
        "training_seed.py": "forbidden_or_ambiguous_file",
        ".gitmodules": "forbidden_or_ambiguous_file",
        ".env": "live_environment_file",
    }
    for path, category in cases.items():
        assert category in {
            item.category for item in release_scan._path_findings(path)
        }


def test_reviewed_binary_requires_exact_bytes_and_repository_path(monkeypatch):
    path = "docs/assets/fictional.png"
    data = b"\x89PNG\r\n\x1a\nfictional review fixture"
    monkeypatch.setattr(release_scan, "REVIEWED_BINARY_ASSETS", {
        path: hashlib.sha256(data).hexdigest(),
    })
    assert release_scan._scan_blob(data, path) == []
    assert release_scan._scan_blob(
        data, f"history/{path}", repository_path=path,
    ) == []
    for changed in (data + b"changed", b"replacement text"):
        assert [item.category for item in release_scan._scan_blob(changed, path)] == [
            "reviewed_binary_digest_mismatch",
        ]
    for relocated in ("docs/assets/other.png", f"history/{path}"):
        assert [item.category for item in release_scan._scan_blob(data, relocated)] == [
            "unexplained_binary",
        ]


def test_history_checks_unapproved_path_even_for_same_reviewed_blob(tmp_path, monkeypatch):
    approved = "docs/assets/fictional.png"
    unapproved = "unreviewed.png"
    data = b"\x89PNG\r\n\x1a\nfictional review fixture"
    monkeypatch.setattr(release_scan, "REVIEWED_BINARY_ASSETS", {
        approved: hashlib.sha256(data).hexdigest(),
    })

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Fictional Contributor")
    git("config", "user.email", "contributor@example.invalid")
    (tmp_path / approved).parent.mkdir(parents=True)
    (tmp_path / approved).write_bytes(data)
    (tmp_path / unapproved).write_bytes(data)
    git("add", approved, unapproved)
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fictional assets")
    git("rm", "--quiet", unapproved)
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Remove unreviewed copy")

    findings, blob_count = release_scan._history_scan(tmp_path)
    assert blob_count == 1
    assert [item.as_dict() for item in findings] == [{
        "category": "unexplained_binary", "path": f"history/{unapproved}",
    }]


def test_commit_metadata_allows_any_email_but_still_scans_other_content():
    body = 'Change' + '\n' * 2 + 'Co-Authored-By: Fictional <' + "@".join(("bot", "unreviewed.test")) + '>' + '\n'
    fields = ['a' * 40, 'Fictional Author', "@".join(("author", "unreviewed.test")), 'Fictional Committer',
              "@".join(("committer", "unreviewed.test")), body]
    assert release_scan._commit_metadata_findings('\n' + '\n'.join(fields)) == []
    changed = fields.copy()
    changed[5] = '/' + 'Users/fictional/private-file'
    assert [item.category for item in release_scan._commit_metadata_findings('\n'.join(changed))] == ['private_absolute_home_path']
    changed[5] = 'client_' + 'secret = "highentropyfixturevalue"'
    assert [item.category for item in release_scan._commit_metadata_findings('\n'.join(changed))] == ['literal_secret_assignment']


def test_file_contents_still_reject_unreviewed_emails():
    email = "@".join(("private-contact", "unapproved.test"))
    for path in ('README.md', 'history/<references>'):
        assert [item.category for item in release_scan._text_findings(email, path)] == ['non_example_email']


def test_actual_history_accepts_commit_emails(tmp_path):
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)

    git('init', '--quiet')
    git('config', 'user.name', 'Fictional Contributor')
    git('config', 'user.email', "@".join(("contributor", "unapproved.test")))
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet',
        '-m', 'Change' + '\n' * 2 + 'Co-Authored-By: Fictional <' + "@".join(("bot", "unapproved.test")) + '>')
    assert release_scan._history_scan(tmp_path) == ([], 0)
