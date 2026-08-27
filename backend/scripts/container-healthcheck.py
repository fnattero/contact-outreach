from __future__ import annotations

import subprocess
import sys
import urllib.request

EXPECTED_PROCESSES = {"api", "worker-general", "worker-maintenance", "beat"}


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
        with urllib.request.urlopen("http://127.0.0.1:8000/health/ready/", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
