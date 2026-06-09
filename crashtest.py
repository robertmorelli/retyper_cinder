import json
import subprocess

with open("data/benchmark_locations.json") as f:
    sources = json.load(f)

failures = []
for bench, variants in sources.items():
    for variant in variants:
        r = subprocess.run(
            ["python3", "detyper.py", bench, variant],
            capture_output=True,
            text=True,
        )
        # TODO: pipe back into python3 to check runtime validity
        if r.returncode != 0:
            err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
            failures.append(f"{bench}/{variant}  {err}")

print("\n".join(failures))
