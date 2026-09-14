from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_command(command: list[str], dry_run: bool) -> None:
    printable = " ".join(f'"{part}"' if " " in part else part for part in command)
    print(f"> {printable}")
    if not dry_run:
        subprocess.run(command, check=True)


def get_venv_python(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the deployment virtual environment and install IIS host dependencies.",
    )
    parser.add_argument(
        "--venv-path",
        default=".venv",
        help="Virtual environment path relative to the deployed site root.",
    )
    parser.add_argument(
        "--requirements",
        default="requirements.txt",
        help="Requirements file path relative to the deployed site root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without executing them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    site_root = Path(__file__).resolve().parent
    venv_path = (site_root / args.venv_path).resolve()
    requirements_path = (site_root / args.requirements).resolve()

    if not requirements_path.exists():
        print(f"Requirements file not found: {requirements_path}", file=sys.stderr)
        return 1

    run_command([sys.executable, "-m", "venv", str(venv_path)], args.dry_run)

    venv_python = get_venv_python(venv_path)
    run_command([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], args.dry_run)
    run_command([str(venv_python), "-m", "pip", "install", "-r", str(requirements_path)], args.dry_run)

    print("Host dependency setup complete.")
    print(f"Virtual environment: {venv_path}")
    print("Next: point IIS at this folder and recycle the app pool after deployment changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
