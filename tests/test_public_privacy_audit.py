"""Public audit is read-only, bounded and never echoes sensitive matches."""

import io
import json
import stat
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

import pytest

from scripts import audit_github_public as audit


@pytest.mark.parametrize(("value", "category"), [
    ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ("ghp_" + "a" * 36, "github_token"),
    ("github_pat_" + "a" * 45, "github_token"),
    ("Bearer " + "a" * 25, "bearer_token"),
    ("password" + '="synthetic-value"', "literal_credential"),
    ("/dev/serial/by-id/" + "usb-EXAMPLE-test", "serial_device_path"),
    ("/home/example-user/project", "home_directory"),
    ("10.23.45.67", "private_ip"),
    ("fd12:3456::1", "private_ip"),
])
def test_sensitive_categories_do_not_return_the_value(value, category):
    result = audit.categories(value)
    assert category in result
    assert value not in repr(result)


def test_documentation_placeholders_and_reserved_addresses_are_safe():
    assert not audit.categories("""password: "<REDACTED>" access_token="${SECRET}"
        api_key="EXAMPLE_VALUE" 127.0.0.1 192.0.2.1 198.51.100.1 203.0.113.2
        /dev/serial/by-id/<adapter> /home/<user>/project 999.999.999.999
        /dev/serial/by-id/... /dev/serial/by-id/usb-...
        /dev/serial/by-id/REPLACE_WITH_YOUR_ADAPTER""")


def archive_bytes(name="data.txt", value=b"safe example", mode=stat.S_IFREG):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        item = ZipInfo(name)
        item.external_attr = mode << 16
        archive.writestr(item, value)
    return buffer.getvalue()


def test_archives_are_scanned_in_memory_without_exposing_the_filename():
    content = archive_bytes("personal-name.txt", b"password" + b'="synthetic-value"')
    assert audit.scan_archive(content) == {"literal_credential"}
    assert audit.scan_archive(archive_bytes(value=b"\xff\xfe")) == set()


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "x\\outside.txt"])
def test_unsafe_archive_paths_are_rejected(name):
    assert audit.scan_archive(archive_bytes(name)) == {"unsafe_archive_member"}


def test_archive_limits_corruption_and_symlinks_are_visible(monkeypatch):
    assert audit.scan_archive(b"not a ZIP") == {"invalid_archive"}
    assert audit.scan_archive(archive_bytes(mode=stat.S_IFLNK)) == {"unsafe_archive_member"}
    monkeypatch.setattr(audit, "MAX_MEMBER_BYTES", 2)
    assert audit.scan_archive(archive_bytes()) == {"archive_size_limit"}
    monkeypatch.setattr(audit, "MAX_BYTES", 2)
    assert audit.scan_archive(archive_bytes()) == {"archive_size_limit"}


def test_pagination_consumes_the_second_page(monkeypatch):
    calls = []

    def request(endpoint):
        calls.append(endpoint)
        return [{"id": 1}] * 100 if len(calls) == 1 else [{"id": 2}]

    monkeypatch.setattr(audit, "request", request)
    assert len(list(audit.objects("repos/example/repo/issues?state=all"))) == 101
    assert calls[-1].endswith("&per_page=100&page=2")


def test_audit_includes_releases_comments_reviews_and_hacs_without_raw_output(monkeypatch):
    archive = archive_bytes(value=b"password" + b'="synthetic-value"')
    routes = {
        "repos/example/repo": {"visibility": "public"},
        "repos/example/repo/releases": [{"id": 1, "body": "safe"}, {"id": 2, "draft": True}],
        "repos/example/repo/releases/1/assets": [
            {"id": 3, "name": "release.zip", "size": len(archive),
             "url": "https://untrusted.invalid/do-not-follow"},
        ],
        "repos/example/repo/releases/assets/3": archive,
        "repos/example/repo/issues": [{"id": 4, "number": 5, "pull_request": {}}],
        "repos/example/repo/pulls/5/reviews": [{"id": 6, "body": "10.23.45.67"}],
        "repos/example/repo/issues/comments": [{"id": 7, "body": "safe"}],
        "repos/example/repo/pulls/comments": [{"id": 8, "body": "safe"}],
        "repos/hacs/default/pulls/9": {"id": 9, "body": "safe"},
        "repos/hacs/default/issues/9/comments": [],
        "repos/hacs/default/pulls/9/comments": [],
        "repos/hacs/default/pulls/9/reviews": [{"id": 10, "body": "safe"}],
    }
    calls = []

    def request(endpoint, *, binary=False):
        key = endpoint.split("?")[0]
        calls.append(key)
        assert binary == key.endswith("/assets/3")
        return routes[key]

    monkeypatch.setattr(audit, "request", request)
    result = audit.audit("example/repo", 9)
    assert result["findings"] == [
        {"location": "asset:3", "category": "literal_credential"},
        {"location": "review:6", "category": "private_ip"},
    ]
    assert result["inspected_surfaces"] == 9
    assert set(calls) == routes.keys()
    assert "synthetic-value" not in json.dumps(result)
    assert "10.23.45.67" not in json.dumps(result)


def test_only_public_repos_and_numeric_remote_ids_are_accepted(monkeypatch):
    monkeypatch.setattr(audit, "request", lambda _: {"visibility": "private"})
    with pytest.raises(ValueError, match="public"):
        audit.audit("example/repo")
    with pytest.raises(ValueError, match="owner/repository"):
        audit.audit("example/repo/../../elsewhere")
    with pytest.raises(ValueError, match="positive"):
        audit.audit("example/repo", -1)
    with pytest.raises(ValueError, match="identifier"):
        audit.identifier({"id": "../../elsewhere"})


def test_requests_are_explicit_gets_and_remote_errors_are_not_echoed(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["capture_output"]
        return SimpleNamespace(returncode=0, stdout=b"{}")

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.request("repos/example/repo") == {}
    assert audit.request("repos/example/repo/releases/assets/1", binary=True) == b"{}"
    assert all(command[:7] == ["gh", "api", "--hostname", "github.com", "--method", "GET", command[6]] for command in calls)
    monkeypatch.setattr(audit.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=1, stdout=b"private remote body"))
    with pytest.raises(ValueError, match="GitHub read failed") as error:
        audit.request("repos/example/repo")
    assert "private remote body" not in str(error.value)


def test_cli_redacts_remote_exceptions(monkeypatch, capsys):
    def fail(*_args):
        raise ValueError("private remote body")
    monkeypatch.setattr(audit, "audit", fail)
    monkeypatch.setattr(audit.sys, "argv", ["audit_github_public.py"])
    assert audit.main() == 2
    assert "private remote body" not in capsys.readouterr().err
