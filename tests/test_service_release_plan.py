"""Independent app releases depend on real Git inputs and verified provenance."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from copy import deepcopy

import pytest

from scripts import plan_service_release as planner
from scripts import service_inputs as inputs

REPOSITORY = "memphi2/ha-mennekes-amtron"
DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
SOURCE = "a" * 40
PREVIOUS_SOURCE = "d" * 40


def git(root, *args):
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(
        [executable, *args], cwd=root, check=True, capture_output=True
    ).stdout.decode().strip()


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def commit(root):
    git(root, "add", "--all")
    git(root, "-c", "commit.gpgsign=false", "commit", "--quiet", "--message", "test change")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def source_tree(tmp_path):
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.name", "Release Test")
    git(tmp_path, "config", "user.email", "release-test@example.invalid")
    files = {
        "Dockerfile": "FROM scratch\nCOPY pyproject.toml requirements-service.txt LICENSE NOTICE /app/\n"
        f"COPY {inputs.DEVICE} /app/device\n",
        ".dockerignore": ".git\n",
        "pyproject.toml": '[build-system]\nrequires=["setuptools>=77"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="example-service"\nversion="0.5.0"\ndependencies=["aiohttp>=3"]\n'
        '[tool.setuptools]\npackages=["device"]\n[tool.ruff]\nline-length=88\n',
        "requirements-service.txt": "aiohttp==3.14.5\n",
        "LICENSE": "License\n",
        "NOTICE": "Notice\n",
        "modbus_service/config.yaml": "version: 0.5.0\nname: App\narch: [amd64, aarch64]\n",
        "custom_components/mennekes_amtron_rtu/brand/icon.png": "image bytes",
        f"{inputs.DEVICE}/server.py": "VALUE = 1\n",
        inputs.SERVICE_VERSION_FILE: 'SERVICE_VERSION = "0.5.0"\n',
        inputs.COMPAT_FILE: 'MIN_SERVICE_VERSION = "0.5.0"\n',
        "modbus_service/translations/en.yaml": "configuration:\n  token:\n    name: Token\n",
        "modbus_service/README.md": "App help\n",
        "modbus_service/DOCS.md": "App instructions\n",
        "modbus_service/CHANGELOG.md": "App changes\n",
        "README.md": "Integration help\n",
    }
    for path, content in files.items():
        write(tmp_path, path, content)
    commit(tmp_path)
    return tmp_path


@pytest.mark.parametrize("path", [
    "README.md", "docs/installation.md", "tests/test_runtime.py",
    "custom_components/mennekes_amtron_rtu/remote.py",
    "custom_components/mennekes_amtron_rtu/manifest.json",
    "custom_components/mennekes_amtron_rtu/translations/en.json",
    inputs.COMPAT_FILE, "modbus_service/README.md", "modbus_service/DOCS.md",
    "modbus_service/CHANGELOG.md",
])
def test_integration_docs_and_tests_do_not_change_service_inputs(source_tree, path):
    before = inputs.fingerprint("HEAD", source_tree)
    write(source_tree, path, "Changed integration-only content\n")
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) == before


@pytest.mark.parametrize("path", [
    f"{inputs.DEVICE}/server.py", "requirements-service.txt", "Dockerfile",
    ".dockerignore", "LICENSE", "NOTICE", "modbus_service/translations/en.yaml",
    "custom_components/mennekes_amtron_rtu/brand/icon.png",
])
def test_changed_service_inputs_require_a_different_fingerprint(source_tree, path):
    before = inputs.fingerprint("HEAD", source_tree)
    target = source_tree / path
    target.write_text(target.read_text() + "\n# relevant service change\n")
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) != before


def test_profile_addition_changes_and_removal_all_affect_release_identity(source_tree):
    """Profiles live outside the image but must never silently reuse an App release."""
    original = git(source_tree, "rev-parse", "HEAD")
    baseline = inputs.fingerprint(original, source_tree)
    profile = source_tree / "modbus_service/apparmor.txt"
    profile.write_text("profile app { /data/options.json r, }\n")
    added_commit = commit(source_tree)
    added = inputs.fingerprint(added_commit, source_tree)
    assert added != baseline
    profile.write_text("profile app { /data/options.json r, /data/intent.json rw, }\n")
    changed_commit = commit(source_tree)
    changed = inputs.fingerprint(changed_commit, source_tree)
    assert changed != added
    profile.unlink()
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) == baseline
    # Historical releases without a profile remain valid comparison baselines.
    assert inputs.fingerprint(original, source_tree) == baseline
    assert inputs.fingerprint(added_commit, source_tree) == added
    assert inputs.fingerprint(changed_commit, source_tree) == changed


@pytest.mark.parametrize(("path", "old", "new", "changes_inputs"), [
    ("pyproject.toml", "line-length=88", "line-length=100", False),
    ("pyproject.toml", "aiohttp>=3", "aiohttp>=4", True),
    ("pyproject.toml", "setuptools>=77", "setuptools>=78", True),
    ("pyproject.toml", 'packages=["device"]', 'packages=["device", "extra"]', True),
    ("modbus_service/config.yaml", "name: App", "name: Updated App", True),
])
def test_package_and_app_metadata_excludes_only_versions_and_dev_tooling(
    source_tree, path, old, new, changes_inputs
):
    before = inputs.fingerprint("HEAD", source_tree)
    target = source_tree / path
    target.write_text(target.read_text().replace(old, new))
    commit(source_tree)
    assert (inputs.fingerprint("HEAD", source_tree) != before) is changes_inputs


def test_version_labels_alone_do_not_change_service_inputs(source_tree):
    before = inputs.fingerprint("HEAD", source_tree)
    for path in ("pyproject.toml", "modbus_service/config.yaml", inputs.SERVICE_VERSION_FILE):
        target = source_tree / path
        target.write_text(target.read_text().replace("0.5.0", "0.6.0b1"))
    commit(source_tree)
    assert inputs.read_service_version(source_tree) == "0.6.0b1"
    assert inputs.fingerprint("HEAD", source_tree) == before


@pytest.mark.parametrize("path", ["pyproject.toml", "modbus_service/config.yaml", inputs.SERVICE_VERSION_FILE])
def test_service_version_metadata_must_agree_independently_of_ha(source_tree, path):
    target = source_tree / path
    target.write_text(target.read_text().replace("0.5.0", "0.6.0"))
    with pytest.raises(ValueError, match="versions must agree"):
        inputs.read_service_version(source_tree)


@pytest.mark.parametrize("source", [
    'SERVICE_VERSION = "0.5.0"\nSERVICE_VERSION = "0.6.0"\n',
    'SERVICE_VERSION = "0.5.0"\nSERVICE_VERSION = ".".join(["0", "6", "0"])\n',
    'SERVICE_VERSION = "0.5.0"\nSERVICE_VERSION += "b1"\n',
    'SERVICE_VERSION = "0.5.0"\nSERVICE_VERSION: str = "0.6.0"\n',
    'SERVICE_VERSION = ".".join(["0", "5", "0"])\n',
    "SERVICE_VERSION = 5\n", "OTHER_VERSION = '0.5.0'\n",
])
def test_runtime_release_version_must_be_one_literal(source):
    with pytest.raises(ValueError, match="single literal"):
        inputs.literal_version(source, "SERVICE_VERSION")


def test_fingerprint_uses_the_committed_tree_and_retains_deleted_file_history(source_tree):
    original = git(source_tree, "rev-parse", "HEAD")
    before = inputs.fingerprint(original, source_tree)
    target = source_tree / inputs.DEVICE / "server.py"
    target.write_text("VALUE = 2\n")
    assert inputs.fingerprint("HEAD", source_tree) == before
    target.unlink()
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) != before
    assert inputs.fingerprint(original, source_tree) == before


def test_input_executable_mode_is_part_of_the_build_identity(source_tree):
    before = inputs.fingerprint("HEAD", source_tree)
    (source_tree / inputs.DEVICE / "server.py").chmod(0o755)
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) != before


@pytest.mark.parametrize("instruction", [
    "COPY extra.txt /app/\n", 'COPY ["extra.txt", "/app/"]\n',
    "COPY\textra.txt /app/\n", "copy extra.txt /app/\n",
    "COPY --chown=1000:1000 extra.txt /app/\n",
    "COPY extra.txt \\\n /app/\n",
    "COPY extra.txt \\\r\n /app/\r\n",
])
def test_new_docker_copy_inputs_remain_tracked_after_the_dockerfile_stops_changing(
    source_tree, instruction
):
    dockerfile = source_tree / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + instruction)
    write(source_tree, "extra.txt", "first asset\n")
    commit(source_tree)
    before = inputs.fingerprint("HEAD", source_tree)
    write(source_tree, "extra.txt", "second asset\n")
    commit(source_tree)
    assert inputs.fingerprint("HEAD", source_tree) != before


@pytest.mark.parametrize("instruction", [
    "ADD https://example.invalid/archive.tar /app/\n",
    "COPY . /app/\n", "COPY ../outside /app/\n", "COPY /absolute /app/\n",
    "COPY $SOURCE /app/\n", "COPY assets/*.txt /app/\n",
    "COPY --chown 1000:1000 extra.txt /app/\n", "COPY missing.txt /app/\n",
    "RUN --mount=type=bind,source=extra.txt,target=/tmp/extra cat /tmp/extra\n",
])
def test_unknown_docker_inputs_fail_closed(source_tree, instruction):
    dockerfile = source_tree / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + instruction)
    commit(source_tree)
    with pytest.raises(ValueError, match=r"service|COPY|ADD"):
        inputs.fingerprint("HEAD", source_tree)


@pytest.mark.parametrize("path", [f"{inputs.DEVICE}/linked.py", "extra.txt"])
def test_symlink_build_inputs_fail_closed(source_tree, path):
    target = source_tree / path
    target.symlink_to("LICENSE")
    if path == "extra.txt":
        dockerfile = source_tree / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "COPY extra.txt /app/\n")
    commit(source_tree)
    with pytest.raises(ValueError, match="not a regular file"):
        inputs.fingerprint("HEAD", source_tree)


def info(version="0.5.0", tag="v0.7.0", source=SOURCE):
    return {
        "repository": REPOSITORY,
        "image": f"ghcr.io/{REPOSITORY}-service",
        "version": version,
        "commit": source,
        "source_tag": tag,
        "channel": "apps-beta" if "b" in version or "rc" in version else "apps",
    }


@pytest.mark.parametrize(("version", "tag", "previous_version", "changed", "minimum", "expected"), [
    ("0.5.0", "v0.7.0", "0.5.0", False, "0.5.0", False),
    ("0.5.0", "v0.8.0b1", "0.5.0", False, "0.5.0", False),
    ("0.5.1", "v0.7.0", "0.5.0", True, "0.5.0", True),
    ("0.6.0", "v0.8.0", "0.6.0b2", False, "0.5.0", True),
    ("0.6.0", "v0.8.0", "0.6.0rc1", False, "0.5.0", True),
    ("0.6.0b10", "v0.8.0b1", "0.6.0b2", True, "0.5.0", True),
    ("0.10.0", "v0.11.0", "0.9.0", True, "0.9.0", True),
    ("0.5.0", "v0.7.0", "0.5.0", True, "0.5.0", "increase"),
    ("0.5.1", "v0.7.0", "0.5.0", False, "0.5.0", "without relevant"),
    ("0.6.0b2", "v0.7.0b1", "0.6.0b1", False, "0.5.0", "without relevant"),
    ("0.5.0", "v0.7.0", "0.5.0", False, "0.5.1", "below"),
    ("0.5.0", "v0.7.0", "0.6.0", True, "0.5.0", "downgrade"),
    ("0.6.0b1", "v0.7.0", "0.5.0", True, "0.5.0", "channel"),
    ("0.6.0", "v0.7.0b1", "0.5.0", True, "0.5.0", "channel"),
])
def test_release_decision_tracks_changes_and_channel_independently_of_ha_version(
    version, tag, previous_version, changed, minimum, expected
):
    args = (info(version, tag), "new" if changed else "same", minimum,
            info(previous_version), "same")
    if isinstance(expected, str):
        with pytest.raises(ValueError, match=expected):
            planner.decide(*args)
    else:
        assert planner.decide(*args) is expected


@pytest.fixture
def planned_release(monkeypatch):
    current = info()
    previous = {**info(tag="v0.5.0", source=PREVIOUS_SOURCE), "digest": DIGEST, "catalog_channel": "apps"}
    state = {"current": current, "previous": previous, "changed": False,
             "minimum": "0.5.0", "digest": DIGEST, "dirty": False}
    commands = []
    verified = []
    reads = []

    def git_stub(_root, *args):
        commands.append(args)
        if args == ("status", "--porcelain"):
            return b" M changed" if state["dirty"] else b""
        assert args == ("merge-base", "--is-ancestor", state["previous"]["commit"], SOURCE)
        return b""

    def catalog(_repository, branch):
        assert _repository == REPOSITORY
        reads.append(branch)
        previous = state["previous"]
        return previous if previous and previous["catalog_channel"] == branch else None

    def verify(value):
        verified.append(deepcopy(value))
        if isinstance(state["digest"], Exception):
            raise state["digest"]
        return state["digest"]

    monkeypatch.setattr(planner, "release_info", lambda *_: state["current"])
    monkeypatch.setattr(planner, "git", git_stub)
    monkeypatch.setattr(planner, "read_catalog", catalog)
    monkeypatch.setattr(planner, "remote_commit", lambda _repo, tag: SOURCE if tag == current["source_tag"] else PREVIOUS_SOURCE)
    monkeypatch.setattr(planner, "fingerprint", lambda ref: "new" if state["changed"] and ref == SOURCE else "same")
    monkeypatch.setattr(planner, "minimum_service_version", lambda: state["minimum"])
    monkeypatch.setattr(planner, "existing_verified_image", verify)
    return state, commands, verified, reads


def test_unchanged_service_reuses_original_tag_commit_and_digest_without_publication(planned_release):
    state, commands, verified, _reads = planned_release
    result = planner.plan(REPOSITORY, state["current"]["source_tag"])
    expected = {key: value for key, value in state["previous"].items() if key not in ("digest", "catalog_channel")}
    assert result.info == expected
    assert result.info["source_tag"] == "v0.5.0"
    assert result.info["commit"] == PREVIOUS_SOURCE
    assert result.digest == DIGEST
    assert not result.build_required
    assert not result.catalog_required
    assert verified == [expected]
    assert commands == [("status", "--porcelain"), ("merge-base", "--is-ancestor", PREVIOUS_SOURCE, SOURCE)]


@pytest.mark.parametrize("existing_digest", [None, DIGEST])
def test_changed_service_builds_or_resumes_a_verified_image_before_catalog_publication(planned_release, existing_digest):
    state, _commands, verified, _reads = planned_release
    state["current"]["version"] = "0.5.1"
    state["changed"] = True
    state["digest"] = existing_digest
    result = planner.plan(REPOSITORY, state["current"]["source_tag"])
    assert result.info == state["current"]
    assert result.info["version"] != result.info["source_tag"].removeprefix("v")
    assert result.build_required is (existing_digest is None)
    assert result.catalog_required
    assert result.digest == (existing_digest or "")
    assert verified == [state["current"]]


@pytest.mark.parametrize("failure", ["missing_image", "different_digest", "provenance", "current_tag", "previous_tag", "dirty", "minimum", "missing_bump", "unnecessary_bump"])
def test_release_plan_rejects_unverified_or_inconsistent_state_before_any_build(planned_release, monkeypatch, failure):
    state, _commands, verified, _reads = planned_release
    if failure == "missing_image":
        state["digest"] = None
    elif failure == "different_digest":
        state["digest"] = OTHER_DIGEST
    elif failure == "provenance":
        state["digest"] = ValueError("missing image provenance")
    elif failure in ("current_tag", "previous_tag"):
        monkeypatch.setattr(planner, "remote_commit", lambda _repo, tag: (
            "e" * 40 if (tag == "v0.7.0") is (failure == "current_tag")
            else SOURCE if tag == "v0.7.0" else PREVIOUS_SOURCE
        ))
    elif failure == "dirty":
        state["dirty"] = True
    elif failure == "minimum":
        state["minimum"] = "0.6.0"
    elif failure == "missing_bump":
        state["changed"] = True
    else:
        state["current"]["version"] = "0.6.0"
    with pytest.raises(ValueError, match=r"image|tag|commit|minimum|increase|app version"):
        planner.plan(REPOSITORY, "v0.7.0")
    if failure not in ("missing_image", "different_digest", "provenance"):
        assert not verified


@pytest.mark.parametrize("field", ["image", "repository", "commit", "digest", "channel", "catalog_channel", "version", "source_tag"])
def test_catalog_identity_fields_are_validated(field):
    catalog = {**info(), "digest": DIGEST, "catalog_channel": "apps"}
    catalog[field] = "invalid"
    with pytest.raises(ValueError, match=r"identity|source tag|expected"):
        planner.normalize_catalog(catalog, REPOSITORY, "apps")


def test_old_catalog_metadata_derives_its_original_source_tag():
    catalog = {**info(), "digest": DIGEST}
    catalog.pop("source_tag")
    result = planner.normalize_catalog(catalog, REPOSITORY, "apps")
    assert result["source_tag"] == "v0.5.0"
    assert result["catalog_channel"] == "apps"


@pytest.mark.parametrize("failure", [None, "ref_object_list", "ref_commit", "missing_metadata", "wrong_encoding", "json_list", "wrong_digest"])
def test_catalog_read_is_pinned_and_malformed_metadata_fails_closed(monkeypatch, failure):
    catalog = {**info(), "digest": DIGEST, "catalog_channel": "apps"}
    if failure == "wrong_digest":
        catalog["digest"] = "sha256:bad"
    payload = [] if failure == "json_list" else catalog
    response = {"encoding": "base64", "content": base64.b64encode(json.dumps(payload).encode()).decode()}
    requests = []

    def api(repository, endpoint, **kwargs):
        assert repository == REPOSITORY
        requests.append((endpoint, kwargs))
        if endpoint == "git/ref/heads/apps":
            if failure == "ref_object_list":
                return {"object": []}
            return {"object": {"sha": "invalid" if failure == "ref_commit" else PREVIOUS_SOURCE}}
        assert endpoint == f"contents/release.json?ref={PREVIOUS_SOURCE}"
        if failure == "missing_metadata":
            return None
        if failure == "wrong_encoding":
            return {**response, "encoding": "unknown"}
        return response

    monkeypatch.setattr(planner, "api", api)
    if failure:
        with pytest.raises((ValueError, TypeError)):
            planner.read_catalog(REPOSITORY, "apps")
    else:
        assert planner.read_catalog(REPOSITORY, "apps") == catalog
        assert requests == [("git/ref/heads/apps", {"missing_ok": True}),
                            (f"contents/release.json?ref={PREVIOUS_SOURCE}", {})]


@pytest.mark.parametrize("secondary", ["missing", "older", "same", "newer_beta", "wrong_source", "wrong_digest"])
def test_original_release_rerun_completes_only_missing_or_older_catalogs(
    planned_release, monkeypatch, secondary
):
    state, _commands, verified, reads = planned_release
    original = state["current"]
    state["previous"] = {**original, "digest": DIGEST, "catalog_channel": "apps"}
    preview = {**state["previous"], "catalog_channel": "apps-beta"}
    if secondary == "missing":
        preview = None
    elif secondary == "older":
        preview["version"] = "0.4.0"
    elif secondary == "newer_beta":
        preview.update(version="0.6.0b1", channel="apps-beta")
    elif secondary == "wrong_source":
        preview["commit"] = PREVIOUS_SOURCE
    elif secondary == "wrong_digest":
        preview["digest"] = OTHER_DIGEST

    def catalog(_repository, branch):
        reads.append(branch)
        return state["previous"] if branch == "apps" else preview

    monkeypatch.setattr(planner, "read_catalog", catalog)
    if secondary.startswith("wrong_"):
        with pytest.raises(ValueError, match="same-version"):
            planner.plan(REPOSITORY, original["source_tag"])
    else:
        result = planner.plan(REPOSITORY, original["source_tag"])
        assert not result.build_required
        assert result.catalog_required is (secondary in ("missing", "older"))
        assert result.digest == DIGEST
        assert result.info == original
    assert verified == [original]
    assert reads == ["apps", "apps", "apps-beta"]


def test_first_stable_service_can_promote_an_identical_preview(planned_release):
    state, _commands, verified, reads = planned_release
    state["current"]["version"] = "0.6.0"
    state["previous"].update(version="0.6.0b1", channel="apps-beta", catalog_channel="apps-beta")
    state["digest"] = None
    result = planner.plan(REPOSITORY, "v0.7.0")
    assert result.build_required
    assert result.catalog_required
    assert result.info == state["current"]
    assert verified == [state["current"]]
    assert reads == ["apps", "apps-beta"]


def test_first_app_release_requires_no_existing_catalog(planned_release):
    state, commands, verified, reads = planned_release
    state["previous"] = None
    state["digest"] = None
    result = planner.plan(REPOSITORY, "v0.7.0")
    assert result.build_required
    assert result.catalog_required
    assert result.digest == ""
    assert commands == [("status", "--porcelain")]
    assert verified == [state["current"]]
    assert reads == ["apps", "apps-beta"]


@pytest.mark.parametrize("failure", [False, True])
def test_cli_emits_only_validated_plan_outputs(tmp_path, monkeypatch, capsys, failure):
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(planner.sys, "argv", ["plan_service_release.py", "--tag", "v0.7.0"])
    result = planner.ServicePlan(info(), "f" * 64, False, True, DIGEST)

    def plan(repository, tag):
        assert repository == REPOSITORY
        assert tag == "v0.7.0"
        if failure:
            raise ValueError("unverified app provenance")
        return result

    monkeypatch.setattr(planner, "plan", plan)
    assert planner.main() == (1 if failure else 0)
    captured = capsys.readouterr()
    if failure:
        assert not output.exists()
        assert captured.out == ""
        assert "unverified app provenance" in captured.err
    else:
        assert json.loads(captured.out) == {
            "info": info(), "inputs_sha256": "f" * 64, "build_required": False,
            "catalog_required": True, "digest": DIGEST,
        }
        assert output.read_text() == f"build_required=false\ncatalog_required=true\ndigest={DIGEST}\n"
