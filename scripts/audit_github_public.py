"""Read-only privacy scan of public GitHub text and uploaded release assets.

Reports contain locations and categories, never matched text. This heuristic
audit complements GitHub secret scanning; it cannot certify absence of secrets.
"""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

MAX_BYTES = 25 * 1024**2
MAX_MEMBER_BYTES = 2 * 1024**2
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "bearer_token": re.compile(r"\bBearer\s+(?!\[|<|\$)[A-Za-z0-9._~-]{20,}", re.IGNORECASE),
    "literal_credential": re.compile(
        r"""["']?(?:password|access_token|api_key|client_secret)["']?\s*[:=]\s*["']([^"'\n]{8,})["']""",
        re.IGNORECASE,
    ),
    "serial_device_path": re.compile(r"/dev/serial/by-id/(?![<${])([\w.-]+)"),
    "home_directory": re.compile(r"/(?:home|Users)/(?!<|\$)[\w.-]+/"),
}
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))
PLACEHOLDER = re.compile(r"^(?:<[^>]+>|\$\{[^}]+\}|!?secret\s+\w+|REDACTED|EXAMPLE[_\w-]*|CHANGEME)$", re.IGNORECASE)


def categories(text: str) -> set[str]:
    """Classify likely sensitive data without returning the actual values."""
    found = set()
    for category, pattern in PATTERNS.items():
        matches = pattern.finditer(text)
        if category == "literal_credential":
            matches = (match for match in matches if not PLACEHOLDER.fullmatch(match[1]))
        elif category == "serial_device_path":
            matches = (match for match in matches if match[1] not in {
                "...", "usb-...", "REPLACE_WITH_YOUR_ADAPTER",
            })
        if any(matches):
            found.add(category)
    for candidate in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|\b[fF][cCdD][0-9a-fA-F:]+", text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(address in network for network in PRIVATE_NETWORKS):
            found.add("private_ip")
    return found


def scan_archive(content: bytes) -> set[str]:
    """Inspect bounded ZIP members in memory, without extracting any path."""
    found: set[str] = set()
    if len(content) > MAX_BYTES:
        return {"archive_size_limit"}
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(item.file_size for item in entries) > MAX_BYTES:
                return {"archive_size_limit"}
            names = set()
            for item in entries:
                name = PurePosixPath(item.filename)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.filename in names or name.is_absolute() or ".." in name.parts
                        or "\\" in item.filename or item.flag_bits & 1
                        or mode not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    return found | {"unsafe_archive_member"}
                names.add(item.filename)
                found.update(categories(item.filename))
                if item.is_dir():
                    continue
                if item.file_size > MAX_MEMBER_BYTES:
                    return found | {"archive_size_limit"}
                data = archive.read(item)
                # Binary images are not OCR'd. UTF-8 text is inspected regardless
                # of extension so renaming a text payload does not bypass this.
                try:
                    found.update(categories(data.decode("utf-8")))
                except UnicodeDecodeError:
                    continue
    except (BadZipFile, NotImplementedError, RuntimeError, OSError, ValueError):
        found.add("invalid_archive")
    return found


def request(endpoint: str, *, binary: bool = False) -> Any:
    """Only issue GETs to api.github.com; do not print API errors or bodies."""
    command = ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint]
    if binary:
        command.extend(["-H", "Accept: application/octet-stream"])
    result = subprocess.run(command, capture_output=True, check=False, timeout=60)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError("GitHub read failed or exceeded the size limit")
    return result.stdout if binary else json.loads(result.stdout)


def objects(endpoint: str) -> Iterator[dict[str, Any]]:
    """Read every page, failing visibly if the generous safety bound is hit."""
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, 1001):
        batch = request(f"{endpoint}{separator}per_page=100&page={page}")
        if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
            raise ValueError("Invalid GitHub list response")
        yield from batch
        if len(batch) < 100:
            return
    raise ValueError("GitHub pagination limit reached; audit incomplete")


def identifier(obj: dict[str, Any], field: str = "id") -> int:
    value = obj.get(field)
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid GitHub object identifier")
    return value


def audit(repository: str, hacs_pr: int | None = None) -> dict[str, Any]:
    """Scan public releases, issues, PRs, reviews and both comment collections."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected owner/repository")
    if hacs_pr is not None and hacs_pr <= 0:
        raise ValueError("Expected a positive HACS PR number")
    prefix = f"repos/{repository}"
    if request(prefix).get("visibility") != "public":
        raise ValueError("This audit only accepts public repositories")
    findings: list[dict[str, str]] = []
    inspected = 0

    def record(location: str, kinds: set[str]) -> None:
        nonlocal inspected
        inspected += 1
        findings.extend({"location": location, "category": kind} for kind in sorted(kinds))

    def scan(obj: dict[str, Any], surface: str) -> None:
        record(f"{surface}:{identifier(obj)}", categories("\n".join(
            str(obj.get(key) or "") for key in ("title", "name", "body", "label")
        )))

    for release in objects(f"{prefix}/releases"):
        # gh may be authenticated as an owner; draft text is not public.
        if release.get("draft"):
            continue
        scan(release, "release")
        for asset in objects(f"{prefix}/releases/{identifier(release)}/assets"):
            scan(asset, "asset_metadata")
            location = f"asset:{identifier(asset)}"
            size = asset.get("size")
            if type(size) is not int or not 0 <= size <= MAX_BYTES:
                record(location, {"asset_size_limit"})
                continue
            content = request(f"{prefix}/releases/assets/{identifier(asset)}", binary=True)
            if len(content) != size:
                raise ValueError("GitHub asset size mismatch")
            if str(asset.get("name", "")).lower().endswith(".zip") or content.startswith(b"PK"):
                record(location, scan_archive(content))
            else:
                try:
                    record(location, categories(content.decode("utf-8")))
                except UnicodeDecodeError:
                    record(location, {"uninspected_binary_asset"})
    for issue in objects(f"{prefix}/issues?state=all"):
        scan(issue, "issue_or_pr")
        if "pull_request" in issue:
            for review in objects(f"{prefix}/pulls/{identifier(issue, 'number')}/reviews"):
                scan(review, "review")
    for endpoint in ("issues/comments", "pulls/comments"):
        for comment in objects(f"{prefix}/{endpoint}"):
            scan(comment, endpoint)
    if hacs_pr is not None:
        scan(request(f"repos/hacs/default/pulls/{hacs_pr}"), "hacs_pr")
        for endpoint in (f"issues/{hacs_pr}/comments", f"pulls/{hacs_pr}/comments", f"pulls/{hacs_pr}/reviews"):
            for comment in objects(f"repos/hacs/default/{endpoint}"):
                scan(comment, f"hacs/{endpoint.split('/')[-1]}")
    return {"inspected_surfaces": inspected, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="memphi2/ha-mennekes-amtron")
    parser.add_argument("--hacs-pr", type=int)
    args = parser.parse_args()
    try:
        report = audit(args.repo, args.hacs_pr)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
        # Never include an exception string: remote errors can echo input.
        sys.stderr.write("Public privacy audit incomplete; check GitHub access and input limits.\n")
        return 2
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return int(bool(report["findings"]))


if __name__ == "__main__":
    sys.exit(main())
