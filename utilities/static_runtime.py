"""Locate the Python runtime used for Static Python benchmark subprocesses."""

from functools import lru_cache
from pathlib import Path
from subprocess import run
from sys import executable


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def static_python():
    candidates = [Path(executable), ROOT / ".venv" / "bin" / "python"]
    tried = set()
    for candidate in candidates:
        candidate_string = str(candidate)
        if candidate_string in tried or not candidate.exists():
            continue
        tried.add(candidate_string)
        probe = run(
            [candidate_string, "-c", "import cinderx, __static__"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate_string
    raise RuntimeError(
        "no Python with CinderX installed; activate .venv or run setup.sh"
    )
