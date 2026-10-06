from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit

EXPECTED_PROCESSES = {"api", "worker-general", "worker-maintenance", "beat"}


def readiness_request() -> urllib.request.Request:
    """Probe the local API as the frontend proxy would, i.e. with the public Host header.

    Production ALLOWED_HOSTS only lists the public host, so a bare 127.0.0.1 Host is refused.
    """
    port = os.environ.get("PORT", "8000")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health/ready/")
    public_host = urlsplit(os.environ.get("PUBLIC_BASE_URL", "")).netloc
    if public_host:
        request.add_header("Host", public_host)
    return request


def main() -> int:
    status = subprocess.run(
        ["supervisorctl", "-c", "/app/supervisord.conf", "status"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if status.returncode != 0:
        return 1
    running = {
        line.split(maxsplit=1)[0]
        for line in status.stdout.splitlines()
        if " RUNNING " in f" {line} "
    }
    if running != EXPECTED_PROCESSES:
        return 1
    try:
        with urllib.request.urlopen(readiness_request(), timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
