#!/usr/bin/env python3
"""Look for personal data before publishing: run in CI and before every push.

Fails on private network addresses (RFC 1918, link-local, CGNAT), MAC addresses (with or
without separators, as Gree writes them) other than the test MACs (aabbccdd...), tracked
log files, and the strings listed in .privacy-local: one per line, never committed
(.gitignore) - your module MACs, Wi-Fi names, host and user names.

  python tools/privacy_check.py            the files in git (or all files, without git)
  python tools/privacy_check.py --history  also every commit and commit message, for the
                                           .privacy-local strings
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, ".privacy-local")
TEST_MAC_PREFIX = "aabbccdd"  # the made-up MACs of the tests
NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",  # privacy: ignore (the private ranges themselves)
        "172.16.0.0/12",  # privacy: ignore
        "192.168.0.0/16",  # privacy: ignore
        "169.254.0.0/16",  # privacy: ignore
        "100.64.0.0/10",  # privacy: ignore
    )
]
# A line with this marker is not checked for addresses or MACs.
IGNORE = "privacy: ignore"
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
MAC = re.compile(r"(?i)(?<![0-9a-z:-])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-z:-])")
# A Gree "mac": 12 hex digits.
BARE_MAC = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{12}(?![0-9A-Za-z])")


def files() -> list[str]:
    """Tracked files and new ones git would add (not ignored)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        return [f for f in out.decode().split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        found = []
        for d, dirs, names in os.walk(ROOT):
            dirs[:] = [
                x for x in dirs if x not in (".git", "__pycache__", ".idea", ".venv")
            ]
            found += [
                os.path.relpath(os.path.join(d, n), ROOT)
                for n in names
                if os.path.join(d, n) != LOCAL
            ]
        return found


def local_strings() -> list[str]:
    if not os.path.exists(LOCAL):
        return []
    with open(LOCAL, encoding="utf-8") as f:
        return [s.strip() for s in f if s.strip() and not s.lstrip().startswith("#")]


def mask(s: str) -> str:
    return s[:2] + "*" * max(len(s) - 2, 1)


def scan_text(text: str, where: str, secrets: list[str]) -> list[str]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for s in secrets:
            if s.lower() in low:
                found.append(f"{where}:{n}: listed in .privacy-local: {mask(s)}")
        if IGNORE in line:
            continue
        for m in IPV4.finditer(line):
            try:
                ip = ipaddress.IPv4Address(m[1])
            except ValueError:
                continue
            if any(ip in net for net in NETWORKS):
                found.append(f"{where}:{n}: private address {mask(m[1])}")
        for m in MAC.finditer(line):
            found.append(f"{where}:{n}: MAC address {mask(m[0])}")
        for m in BARE_MAC.finditer(line):
            if not m[0].startswith(TEST_MAC_PREFIX) and not m[0].isdigit():
                found.append(f"{where}:{n}: MAC address {mask(m[0])}")
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--history",
        action="store_true",
        help="also search all commits for .privacy-local strings",
    )
    a = p.parse_args(argv)
    secrets = local_strings()
    findings = []
    for path in files():
        if path.endswith(".log") or "/logs/" in "/" + path.replace(os.sep, "/"):
            findings.append(f"{path}: log files must not be published")
            continue
        try:
            with open(os.path.join(ROOT, path), "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\0" in data:
            continue
        findings += scan_text(data.decode("utf-8", "replace"), path, secrets)
    if a.history:
        if not secrets:
            print("no .privacy-local: nothing to search in the history")
        else:
            out = subprocess.run(
                ["git", "log", "--all", "-p", "--format=commit %H%n%an <%ae>%n%B"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout.decode("utf-8", "replace")
            findings += [
                f for f in scan_text(out, "history", secrets) if ".privacy-local" in f
            ]
    for f in findings:
        print(f)
    print(
        f"privacy check: {'FAILED' if findings else 'ok'} ({len(secrets)} local string(s) checked)"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
