from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PILOT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PILOT_ROOT.parent


def load_env() -> dict[str, str]:
    environment = dict(os.environ)
    env_path = PILOT_ROOT / ".env"
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            environment[key.strip()] = value.strip().strip('"').strip("'")
    environment["RAG_VERSION"] = "v2"
    environment["LLM_PROVIDER"] = environment.get("LLM_PROVIDER", "mistral")
    environment["POSTGRES_V2_URL"] = environment.get(
        "POSTGRES_V2_URL",
        "postgresql://pilot:pilot_local_only@127.0.0.1:55432/ai_riviera_embedding_pilot",
    )
    return environment


def main() -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(PILOT_ROOT / "database" / "compose.yaml"), "up", "-d", "--wait"],
        cwd=PROJECT_ROOT, check=True,
    )
    command = [
        sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app" / "ui.py"),
        "--server.port", "8502", "--browser.gatherUsageStats", "false",
    ]
    raise SystemExit(subprocess.call(command, cwd=PROJECT_ROOT, env=load_env()))


if __name__ == "__main__":
    main()
