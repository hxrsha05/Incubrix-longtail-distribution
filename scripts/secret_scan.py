"""Look for leaked secrets in the repository files and, optionally, in the service logs. Writes a report as proof.

    python -m scripts.secret_scan [--docker-logs] [--out evidence/secret-scan.txt]

What counts as a leak:
  * the real value of any secret-looking setting in your local .env (PASSWORD, SECRET, KEY, TOKEN with 8+ characters);
  * the secret values in the seed connections file, when the api container is running;
  * a Ghost admin key shape (24 hex : 64 hex), a JWT (eyJ...), or a "password": "..." with a real-looking value.
The deliberately public fake keys of the mock platforms (mock-*) are not secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "node_modules", ".venv", "venv"}
SKIP_FILES = {".env", "secret-scan.txt"}  # .env is local and git-ignored; the report itself names the patterns
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg", ".ini", ".sh", ".example", ".env"}
PATTERNS = {
    "ghost admin key shape": re.compile(r"\b[0-9a-f]{24}:[0-9a-f]{64}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "password with a value": re.compile(r"\"password\"\s*:\s*\"(?!\*+\")[^\"]{6,}\""),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
}
# Logs of the services WE wrote. Lemmy, Ghost and their databases are third-party test containers: Lemmy's own Postgres
# prints its (throwaway, local) session tokens when two logins collide, which is outside our code.
OUR_SERVICES = ["api", "mock-platforms", "seed"]
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|KEY|TOKEN)", re.I)


def env_secrets() -> set[str]:
    values: set[str] = set()
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, _, value = line.partition("=")
                value = value.split("#")[0].strip().strip('"').strip("'")
                if SECRET_NAME.search(name) and len(value) >= 8 and not value.startswith(("mock-", "local-dev-")) and value != "change-me":
                    values.add(value)
    return values


def connection_secrets() -> set[str]:
    try:
        out = subprocess.run(["docker", "compose", "exec", "-T", "api", "cat", "/seed/connections.json"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out)
    except Exception:  # noqa: BLE001 - not running, or not json: nothing to add
        return set()
    return {str(v) for entry in data.values() for k, v in entry.items() if SECRET_NAME.search(k) and len(str(v)) >= 8}


def public_defaults() -> set[str]:
    """Values written in docker-compose.yml as ${NAME:-default}: published on purpose, they only protect throwaway local containers."""
    compose = ROOT / "docker-compose.yml"
    return set(re.findall(r":-([^}]+)\}", compose.read_text(encoding="utf-8"))) if compose.is_file() else set()


def scan_text(label: str, text: str, secrets: set[str]) -> list[str]:
    found = []
    for secret in secrets:
        if secret in text:
            found.append(f"{label}: contains a real secret value from .env or the seed file (length {len(secret)})")
    for name, pattern in PATTERNS.items():
        if pattern.search(text):
            found.append(f"{label}: matches pattern '{name}'")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker-logs", action="store_true", help="also scan `docker compose logs`")
    parser.add_argument("--out", default="evidence/secret-scan.txt")
    args = parser.parse_args()

    secrets = (env_secrets() | (connection_secrets() if args.docker_logs else set())) - public_defaults()
    findings, files = [], 0
    for path in ROOT.rglob("*"):
        if path.is_dir() or SKIP_DIRS & set(path.relative_to(ROOT).parts) or path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore", ".dockerignore"}:
            continue
        files += 1
        findings += scan_text(str(path.relative_to(ROOT)).replace("\\", "/"), path.read_text(encoding="utf-8", errors="ignore"), secrets)
    log_note = "not scanned"
    if args.docker_logs:
        logs = subprocess.run(["docker", "compose", "logs", "--no-color", *OUR_SERVICES], cwd=ROOT, capture_output=True, text=True, timeout=120)
        text = (logs.stdout or "") + (logs.stderr or "")
        findings += scan_text("docker compose logs", text, secrets)
        log_note = f"scanned {len(text.splitlines())} lines from {', '.join(OUR_SERVICES)} (third-party test containers excluded)"

    report = [f"Secret scan  {datetime.now(timezone.utc).isoformat(timespec='seconds')}", f"files scanned: {files}",
              f"secret values checked for: {len(secrets)} (values are never printed)", f"service logs: {log_note}",
              f"RESULT: {'CLEAN' if not findings else 'FOUND ' + str(len(findings))}"] + [f"  - {f}" for f in findings]
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
