from __future__ import annotations

import importlib.metadata
import subprocess
import sys
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


def main() -> None:
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


if __name__ == "__main__":
    main()
