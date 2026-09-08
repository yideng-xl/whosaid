"""Fail packaging if local user data is tracked or present in bundled resources."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_NAMES = {"vocabulary.json", "config.json", "svc.pid", ".env", ".env.local"}
PRIVATE_DIRS = {"recordings", "personal-data", ".incomplete", ".completed"}


def private_path(path: str) -> bool:
    parts = Path(path).parts
    return (Path(path).name in PRIVATE_NAMES or
            any(part in PRIVATE_DIRS for part in parts) or
            path.endswith((".transcript.json", ".diarization.json")))


def check() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    bad = [p for p in tracked if p and private_path(p)]
    # Only application source is allowed in the core resource tree, never data dirs.
    core = ROOT / "desktop/src-tauri/core"
    if core.exists():
        bad += [str(p.relative_to(ROOT)) for p in core.rglob("*")
                if p.is_file() and (private_path(str(p.relative_to(core))) or
                                   p.suffix not in {".py", ".pyc"})]
    if bad:
        # Report filenames only; never print vocabulary content or secrets.
        raise SystemExit("Private data/package allowlist check failed:\n" + "\n".join(bad))
    print("Private data check: passed (no user vocabulary/config/recordings)")


if __name__ == "__main__":
    check()
