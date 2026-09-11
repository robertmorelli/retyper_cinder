"""Run a benchmark experiment durably across hosts in ``data/cloudlab.json``.

The controller has no server component.  ``submit`` uploads an immutable source
snapshot to each selected host and starts a detached worker.  ``status`` and
``collect`` reconnect later using the local manifest in ``.cloudlab/jobs``.
"""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from fnmatch import fnmatch
from hashlib import sha256
from json import dump, load
from pathlib import Path
from shlex import quote
from subprocess import PIPE, run
from tempfile import TemporaryDirectory
import os
import tarfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOSTS = ROOT / "data" / "cloudlab.json"
STATE_ROOT = ROOT / ".cloudlab" / "jobs"
REMOTE_ROOT = ".one_true_detyper/jobs"
SSH_OPTIONS = (
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
)
EXCLUDES = (
    ".git", ".cloudlab", ".venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "scratch", "_cinderx",
)


def read_hosts(path):
    document = load_json(path)
    values = document.get("hosts") if isinstance(document, dict) else None
    if not isinstance(values, list):
        raise ValueError(f"{path} must contain a JSON object with a hosts list")
    hosts = []
    for host in values:
        if (
            not isinstance(host, str)
            or not host.strip()
            or any(character.isspace() for character in host)
        ):
            raise ValueError(f"invalid SSH destination in {path}: {host!r}")
        host = host.strip()
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        raise ValueError(f"no hosts found in {path}")
    return hosts


def experiment_path(value):
    path = ROOT / (value if value.startswith("exp_") else f"exp_{value}")
    if not (path / "sample_plan.json").is_file():
        raise ValueError(f"experiment not found: {path}")
    return path


def load_json(path):
    with Path(path).open() as file:
        return load(file)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as file:
        dump(value, file, indent=2)
        file.write("\n")
    temporary.replace(path)


def excluded(relative, experiment_name):
    parts = relative.parts
    if relative == Path("data/cloudlab.json"):
        return True
    if any(part in EXCLUDES for part in parts):
        return True
    return parts and fnmatch(parts[0], "exp_*") and parts[0] != experiment_name


def make_snapshot(destination, experiment):
    """Archive the working tree, including uncommitted source changes."""
    with tarfile.open(destination, "w:gz", compresslevel=6) as archive:
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            if excluded(relative, experiment.name):
                continue
            archive.add(path, arcname=relative, recursive=False)


def execute(command, *, input_bytes=None, check=True):
    completed = run(command, input=input_bytes, stdout=PIPE, stderr=PIPE)
    if check and completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or f"command exited {completed.returncode}")
    return completed


def ssh(host, script, *, check=True):
    return execute(["ssh", *SSH_OPTIONS, host, script], check=check)


def upload(host, local, remote):
    execute(["scp", *SSH_OPTIONS, str(local), f"{host}:{remote}"])


def split_range(start, end, hosts):
    if not hosts:
        raise ValueError("at least one host is required")
    positions = list(range(start, end + 1))
    selected = hosts[:min(len(hosts), len(positions))]
    base, extra = divmod(len(positions), len(selected))
    assignments = {}
    offset = 0
    for index, host in enumerate(selected):
        size = base + (index < extra)
        chunk = positions[offset:offset + size]
        assignments[host] = [chunk[0], chunk[-1]]
        offset += size
    return assignments


def remote_job_dir(job_id):
    return f"{REMOTE_ROOT}/{job_id}"


def worker_script(job_id, experiment, start, end, kind="timing"):
    job = remote_job_dir(job_id)
    if kind == "typecheck":
        commands = [
            "$HOME/.one_true_detyper/runtime/bin/python "
            f"benchmarking/sample_tc.py {quote(experiment)}"
        ]
    else:
        commands = [
            "$HOME/.one_true_detyper/runtime/bin/python "
            f"benchmarking/run_exp.py --which {start} {end} "
            f"--timestamp {quote(experiment)}"
        ]
    body = " && ".join(commands)
    return (
        f"cd \"$HOME/{job}/repo\"; "
        f"printf running > \"$HOME/{job}/state\"; "
        f"({body}); rc=$?; printf '%s\\n' \"$rc\" > \"$HOME/{job}/exit_code\"; "
        f"if [ \"$rc\" -eq 0 ]; then printf succeeded; else printf failed; fi "
        f"> \"$HOME/{job}/state\"; exit \"$rc\""
    )


def deploy_worker(host, archive, job_id, experiment, start, end,
                  kind="timing"):
    job = remote_job_dir(job_id)
    remote_archive = f"{job_id}.tar.gz"
    ssh(host, f"mkdir -p \"$HOME/{job}/repo\"")
    upload(host, archive, remote_archive)
    ssh(host, (
        f"tar -xzf {quote(remote_archive)} -C \"$HOME/{job}/repo\" && "
        f"rm -f {quote(remote_archive)}"
    ))
    script = worker_script(job_id, experiment, start, end, kind)
    launch = (
        f"nohup sh -c {quote(script)} > \"$HOME/{job}/worker.log\" 2>&1 "
        f"< /dev/null & echo $!"
    )
    pid = ssh(host, launch).stdout.decode().strip()
    return pid


def submit(args):
    experiment = experiment_path(args.experiment)
    plan = load_json(experiment / "sample_plan.json")
    hosts = read_hosts(args.hosts)[:args.limit]
    if len(hosts) < 2:
        raise ValueError("submit needs at least two hosts: one typechecker and one timer")
    typecheck_host = hosts[0]
    assignments = split_range(args.start, args.end, hosts[1:])
    workers = {
        typecheck_host: {"kind": "typecheck"},
        **{
            host: {"kind": "timing", "range": values}
            for host, values in assignments.items()
        },
    }
    not_ready = []
    with ThreadPoolExecutor(max_workers=min(args.parallel, len(workers))) as pool:
        futures = {pool.submit(runtime_probe, host): host for host in workers}
        for future in as_completed(futures):
            if future.result().returncode:
                not_ready.append(futures[future])
    if not_ready:
        raise ValueError(
            "Python 3.14+CinderX runtime is not ready on: " + ", ".join(not_ready)
            + "; run the prepare command, then check"
        )
    job_id = args.job_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    state = STATE_ROOT / job_id
    if state.exists():
        raise ValueError(f"job already exists: {job_id}")
    state.mkdir(parents=True)
    with TemporaryDirectory() as temporary:
        archive = Path(temporary) / "source.tar.gz"
        make_snapshot(archive, experiment)
        digest = sha256(archive.read_bytes()).hexdigest()
        manifest = {
            "version": 1, "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "experiment": experiment.name, "range": [args.start, args.end],
            "snapshot_sha256": digest,
            "workers": workers,
        }
        save_json(state / "manifest.json", manifest)
        failures = []
        with ThreadPoolExecutor(max_workers=min(args.parallel, len(workers))) as pool:
            futures = {
                pool.submit(deploy_worker, host, archive, job_id, experiment.name,
                            *(worker.get("range") or [args.start, args.end]),
                            worker["kind"]): host
                for host, worker in workers.items()
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    manifest["workers"][host]["pid"] = future.result()
                    worker = workers[host]
                    work = (
                        "all masks" if worker["kind"] == "typecheck"
                        else f"--which {worker['range'][0]} {worker['range'][1]}"
                    )
                    print(f"submitted {host} [{worker['kind']}]: {work}")
                except Exception as exception:
                    manifest["workers"][host]["submit_error"] = str(exception)
                    failures.append(host)
                    print(f"FAILED {host}: {exception}")
                save_json(state / "manifest.json", manifest)
    print(f"job {job_id}: {len(workers) - len(failures)}/{len(workers)} workers submitted")
    if failures:
        raise SystemExit(1)


def job_manifest(job_id):
    path = STATE_ROOT / job_id / "manifest.json"
    if not path.is_file():
        raise ValueError(f"unknown job: {job_id}")
    return path, load_json(path)


def worker_status(host, job_id):
    job = remote_job_dir(job_id)
    completed = ssh(host, (
        f"state=$(cat \"$HOME/{job}/state\" 2>/dev/null || printf missing); "
        f"code=$(cat \"$HOME/{job}/exit_code\" 2>/dev/null || true); "
        f"printf '%s %s' \"$state\" \"$code\""
    ), check=False)
    if completed.returncode:
        return "unreachable", None
    words = completed.stdout.decode().split()
    return (words[0] if words else "unknown"), (words[1] if len(words) > 1 else None)


def status(args):
    _, manifest = job_manifest(args.job_id)
    for host, worker in manifest["workers"].items():
        state, code = worker_status(host, args.job_id)
        suffix = f" (exit {code})" if code is not None else ""
        work = (
            "all masks" if worker["kind"] == "typecheck"
            else f"--which {worker['range'][0]} {worker['range'][1]}"
        )
        print(f"{host} [{worker['kind']}]: {state}{suffix} — {work}")


def download_result(host, job_id, experiment, filename, destination):
    remote = f"{host}:{remote_job_dir(job_id)}/repo/{experiment}/{filename}"
    execute(["scp", *SSH_OPTIONS, remote, str(destination)])


def collect(args):
    _, manifest = job_manifest(args.job_id)
    experiment = experiment_path(manifest["experiment"])
    plan = load_json(experiment / "sample_plan.json")
    merged = load_json(experiment / "sample_results.json")
    merged_typechecks = load_json(experiment / "sample_tc.json")
    collected = STATE_ROOT / args.job_id / "results"
    collected.mkdir(parents=True, exist_ok=True)
    for host, worker in manifest["workers"].items():
        state, _ = worker_status(host, args.job_id)
        if state != "succeeded" and not args.partial:
            print(f"skipping {host}: {state}")
            continue
        destination = collected / f"{host.split('@')[-1]}.json"
        try:
            filename = "sample_tc.json" if worker["kind"] == "typecheck" else "sample_results.json"
            download_result(host, args.job_id, experiment.name, filename, destination)
            result = load_json(destination)
            if result.keys() != plan.keys():
                raise ValueError("worker result does not match the experiment plan")
            if worker["kind"] == "typecheck":
                merged_typechecks = result
                print(f"collected {host}: typecheck results")
            else:
                merge_timing_samples(merged, result)
                print(
                    f"collected {host}: --which "
                    f"{worker['range'][0]} {worker['range'][1]}"
                )
        except Exception as exception:
            print(f"FAILED {host}: {exception}")
    save_json(experiment / "sample_results.json", merged)
    save_json(experiment / "sample_tc.json", merged_typechecks)
    print(f"merged results into {experiment / 'sample_results.json'}")


def merge_timing_samples(merged, shard):
    for benchmark, variants in shard.items():
        for variant, masks in variants.items():
            for mask, samples in masks.items():
                if not samples:
                    continue
                current = merged[benchmark][variant][mask]
                if current and current != samples:
                    raise ValueError(
                        f"conflicting timing samples for {benchmark}/{variant}/{mask}"
                    )
                merged[benchmark][variant][mask] = samples


def check(args):
    hosts = read_hosts(args.hosts)[:args.limit]
    with ThreadPoolExecutor(max_workers=min(args.parallel, len(hosts))) as pool:
        futures = {pool.submit(runtime_probe, host): host for host in hosts}
        failed = False
        for future in as_completed(futures):
            host = futures[future]
            result = future.result()
            output = (result.stdout or result.stderr).decode(errors="replace").strip().splitlines()
            detail = output[-1] if output else f"exit {result.returncode}"
            label = "ready" if result.returncode == 0 else "not ready"
            failed |= result.returncode != 0
            print(f"{host}: {label} ({detail})")
    if failed:
        raise SystemExit(1)


def runtime_probe(host):
    probe = (
        "p=$HOME/.one_true_detyper/runtime/bin/python; "
        'if [ -x "$p" ]; then "$p" -c '
        + quote("import sys, cinderx, __static__; assert sys.version_info[:2] == (3, 14); print(sys.version.split()[0])")
        + "; else printf missing; exit 1; fi"
    )
    return ssh(host, probe, check=False)


def prepare_worker(host, archive):
    root = ".one_true_detyper/bootstrap"
    upload(host, archive, "one_true_detyper-bootstrap.tar.gz")
    ssh(host, (
        f"mkdir -p \"$HOME/{root}/repo\" && "
        f"tar -xzf one_true_detyper-bootstrap.tar.gz -C \"$HOME/{root}/repo\" && "
        "rm -f one_true_detyper-bootstrap.tar.gz"
    ))
    install = (
        "set -eu; "
        "sudo env DEBIAN_FRONTEND=noninteractive apt-get update; "
        "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "software-properties-common; "
        "sudo add-apt-repository -y ppa:ubuntu-toolchain-r/test; "
        "sudo env DEBIAN_FRONTEND=noninteractive apt-get update; "
        "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "build-essential gcc-14 g++-14 cmake ninja-build pkg-config curl libssl-dev "
        "zlib1g-dev libffi-dev libbz2-dev libreadline-dev libsqlite3-dev; "
        "cd \"$HOME/.one_true_detyper/bootstrap/repo\"; "
        "curl -LsSf https://astral.sh/uv/install.sh | sh; "
        "UV=\"$HOME/.local/bin/uv\"; "
        "\"$UV\" python install 3.14; "
        "\"$UV\" venv --clear --python 3.14 .venv; "
        "\"$UV\" pip install --python .venv/bin/python cmake ninja scipy; "
        "PATH=\"$PWD/.venv/bin:$PATH\" CC=gcc-14 CXX=g++-14 "
        "\"$UV\" pip install --python .venv/bin/python ./cinderx; "
        "rm -rf \"$HOME/.one_true_detyper/runtime\"; "
        "mv .venv \"$HOME/.one_true_detyper/runtime\"; "
        "\"$HOME/.one_true_detyper/runtime/bin/python\" -c "
        + quote("import sys, cinderx, __static__; assert sys.version_info[:2] == (3, 14)")
    )
    wrapped = (
        f"printf running > \"$HOME/{root}/state\"; "
        f"sh -c {quote(install)}; rc=$?; "
        f"printf '%s\\n' \"$rc\" > \"$HOME/{root}/exit_code\"; "
        f"if [ \"$rc\" -eq 0 ]; then printf succeeded; else printf failed; fi > \"$HOME/{root}/state\""
    )
    launched = ssh(host, (
        f"nohup sh -c {quote(wrapped)} > \"$HOME/{root}/prepare.log\" 2>&1 "
        "< /dev/null & echo $!"
    ))
    return launched.stdout.decode().strip()


def prepare(args):
    hosts = read_hosts(args.hosts)[:args.limit]
    candidates = sorted(path for path in ROOT.glob("exp_*") if path.is_dir())
    if not candidates:
        raise ValueError("an experiment directory is needed to create the source snapshot")
    with TemporaryDirectory() as temporary:
        archive = Path(temporary) / "source.tar.gz"
        make_snapshot(archive, candidates[-1])
        with ThreadPoolExecutor(max_workers=min(args.parallel, len(hosts))) as pool:
            futures = {pool.submit(prepare_worker, host, archive): host for host in hosts}
            failed = False
            for future in as_completed(futures):
                host = futures[future]
                try:
                    print(f"preparing {host} (pid {future.result()})")
                except Exception as exception:
                    failed = True
                    print(f"FAILED {host}: {exception}")
    print("preparation continues remotely; use `cloudlab.py check` when it finishes")
    if failed:
        raise SystemExit(1)


def parser():
    result = ArgumentParser(description=__doc__)
    common = ArgumentParser(add_help=False)
    common.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    common.add_argument("--limit", type=int, default=10_000)
    common.add_argument("--parallel", type=int, default=8)
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", parents=[common])
    prepare_parser.set_defaults(function=prepare)
    check_parser = commands.add_parser("check", parents=[common])
    check_parser.set_defaults(function=check)
    submit_parser = commands.add_parser("submit", parents=[common])
    submit_parser.add_argument("experiment")
    submit_parser.add_argument("--which", nargs=2, type=int, required=True,
                               metavar=("START", "END"))
    submit_parser.add_argument("--job-id")
    submit_parser.set_defaults(function=submit)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("job_id")
    status_parser.set_defaults(function=status)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("job_id")
    collect_parser.add_argument("--partial", action="store_true")
    collect_parser.set_defaults(function=collect)
    return result


def main():
    args = parser().parse_args()
    if hasattr(args, "which"):
        args.start, args.end = args.which
        if args.start < 1 or args.end < args.start:
            raise SystemExit("--which must satisfy 1 <= START <= END")
    try:
        args.function(args)
    except (OSError, RuntimeError, ValueError) as exception:
        raise SystemExit(str(exception)) from exception


if __name__ == "__main__":
    main()
