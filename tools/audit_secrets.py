"""Audit publishable files and complete local Git history without printing secrets."""

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {
    "secrets.py", "badge_profiles.json", ".work_status_badge.json",
    ".work_status_badge_device.json",
}
WIFI_NAMES = {"wifi_ssid", "ssid", "wifi_password", "wifi_pass", "wlan_password", "psk"}
PLACEHOLDER = re.compile(r"^(?:your[_ -]|example[_ -]|test[_ -]|<|\$\{)", re.I)


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def wifi_literals(content, path):
    """Check multiline Python assignments as well as the scanner's text rules."""
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(content)
    except (SyntaxError, UnicodeError, ValueError):
        return []
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if not value.value or PLACEHOLDER.match(value.value):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.lower() in WIFI_NAMES:
                findings.append({"rule": "literal-wifi-setting", "file": path, "line": node.lineno})
    return findings


def scan(scanner, mode, source, report, config):
    command = [scanner, mode, str(source), "--config", str(config),
               "--redact=100", "--report-format", "json", "--report-path", str(report),
               "--no-banner", "--log-level", "error"]
    if mode == "git":
        command.append("--log-opts=--all --full-history")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError("Gitleaks could not complete the scan (exit %d)." % result.returncode)
    raw = json.loads(report.read_text(encoding="utf-8"))
    return [{"rule": item["RuleID"], "file": item["File"],
             "line": item["StartLine"], "commit": item.get("Commit", "")}
            for item in (raw or [])]


def audit(scanner):
    if git("rev-parse", "--is-shallow-repository").strip() != b"false":
        raise RuntimeError("Fetch complete history before auditing; this checkout is shallow.")
    head = git("rev-parse", "HEAD").decode().strip()
    commits = git("rev-list", "--all").decode().splitlines()
    paths = set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0"))
    files = sorted(path for path in paths if path and (ROOT / path).is_file())
    config = ROOT / "tools" / "gitleaks.toml"
    findings = []
    for path in files:
        relative = PurePosixPath(path)
        if relative.name in PRIVATE_NAMES or ".badge_state" in relative.parts:
            findings.append({"rule": "private-file-tracked", "file": path, "line": 1})

    with tempfile.TemporaryDirectory(prefix="badge-secret-audit-") as temporary:
        folder = Path(temporary)
        current = folder / "current"
        current.mkdir()
        for path in files:
            destination = current / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, destination)
            findings.extend(wifi_literals(destination.read_bytes(), path))
        findings.extend(scan(scanner, "dir", current, folder / "current.json", config))
        findings.extend(scan(scanner, "git", ROOT, folder / "history.json", config))

        historical = folder / "historical"
        historical.mkdir()
        blobs = 0
        objects = git("rev-list", "--objects", "--all").decode().splitlines()
        process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for record in objects:
                object_id, separator, path = record.partition(" ")
                if not separator:
                    continue
                process.stdin.write((object_id + "\n").encode())
                process.stdin.flush()
                header = process.stdout.readline().decode().split()
                size = int(header[2])
                content = process.stdout.read(size)
                process.stdout.read(1)
                if header[1] != "blob":
                    continue
                blobs += 1
                destination = historical / (object_id + "-" + PurePosixPath(path).name)
                destination.write_bytes(content)
                for finding in wifi_literals(content, path):
                    finding["blob"] = object_id
                    findings.append(finding)
            process.stdin.close()
            process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
        messages = git("log", "--all", "--format=COMMIT %H%n%B")
        (historical / "commit-messages.txt").write_bytes(messages)
        findings.extend(scan(scanner, "dir", historical, folder / "snapshots.json", config))
        for finding in findings:
            for prefix in (str(current), str(historical)):
                finding["file"] = finding["file"].replace(prefix, "").lstrip("/\\")

    return {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "head": head, "reachable_commits": len(commits), "historical_blobs": blobs,
        "publishable_files": len(files), "finding_count": len(findings), "findings": findings,
        "scope": "working tree, all reachable refs, full historical blobs, commit messages",
        "limitations": "No OCR of images; no unreachable or deleted remote refs; detection is not a guarantee.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", default=shutil.which("gitleaks"))
    parser.add_argument("--summary", type=Path, help="Optional redacted JSON summary path")
    arguments = parser.parse_args()
    if not arguments.gitleaks:
        parser.error("Install Gitleaks or pass --gitleaks with its executable path.")
    result = audit(str(Path(arguments.gitleaks).resolve()))
    output = json.dumps(result, indent=2)
    print(output)
    if arguments.summary:
        arguments.summary.parent.mkdir(parents=True, exist_ok=True)
        arguments.summary.write_text(output + "\n", encoding="utf-8")
    return 1 if result["finding_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())