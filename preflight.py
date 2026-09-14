from __future__ import annotations

import importlib.metadata
import argparse
import json
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from pathlib import Path


PACKAGE_NAMES = {
    "Flask": "flask",
    "wfastcgi": "wfastcgi",
    "scikit-learn": "sklearn",
}


def missing_packages(requirements_path: Path) -> list[str]:
    missing: list[str] = []
    for requirement in requirements_path.read_text(encoding="utf-8").splitlines():
        requirement = requirement.strip()
        if not requirement or requirement.startswith("#"):
            continue
        package_name = requirement.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip()
        import_name = PACKAGE_NAMES.get(package_name, package_name.replace("-", "_"))
        try:
            importlib.metadata.version(package_name)
            __import__(import_name)
        except (importlib.metadata.PackageNotFoundError, ImportError):
            missing.append(requirement)
    return missing


def ollama_is_ready(endpoint: str) -> bool:
    try:
        with urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=3) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def start_ollama(endpoint: str) -> None:
    if ollama_is_ready(endpoint):
        return
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    for _ in range(20):
        time.sleep(0.5)
        if ollama_is_ready(endpoint):
            return
    raise RuntimeError("Ollama service did not become ready at " + endpoint)


def ensure_ollama(model: str, endpoint: str) -> None:
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        raise SystemExit(
            "Ollama is not installed or is not on PATH. Install it from https://ollama.com/download/windows "
            "then rerun preflight.py."
        )
    print(f"Ollama executable: {ollama_path}")
    start_ollama(endpoint)
    print(f"Pulling Ollama model: {model}")
    subprocess.check_call([ollama_path, "pull", model])
    request = Request(
        f"{endpoint.rstrip('/')}/api/generate",
        data=json.dumps({"model": model, "prompt": "Reply with OK.", "stream": False}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("response"):
            raise RuntimeError("Ollama returned an empty response")
    except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Ollama model verification failed: {error}") from error
    print(f"Ollama ready with model: {model}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Python dependencies and configure Ollama for alias filtering.")
    parser.add_argument("--ollama-model", default="llama3.2", help="Ollama model to pull (default: llama3.2).")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama API base URL.")
    parser.add_argument("--skip-ollama", action="store_true", help="Only check Python packages; do not configure Ollama.")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    requirements_path = project_root / "requirements.txt"
    missing = missing_packages(requirements_path)
    if not missing:
        print("Preflight passed: all required packages are installed.")
        return

    print("Installing missing packages:")
    for requirement in missing:
        print(f"  {requirement}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(requirements_path)])
    remaining = missing_packages(requirements_path)
    if remaining:
        raise SystemExit(f"Preflight failed; still missing: {', '.join(remaining)}")
    print("Preflight passed: all required packages are installed.")
    if not args.skip_ollama:
        ensure_ollama(args.ollama_model, args.ollama_url)


if __name__ == "__main__":
    main()
