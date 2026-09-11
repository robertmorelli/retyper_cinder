from benchmarking.cloudlab import merge_timing_samples, read_hosts, split_range, worker_script


def test_read_hosts_accepts_json_and_removes_duplicates(tmp_path):
    hosts = tmp_path / "hosts.json"
    hosts.write_text('{"hosts": ["user@one", "user@two", "user@one"]}\n')
    assert read_hosts(hosts) == ["user@one", "user@two"]


def test_read_hosts_rejects_shell_commands(tmp_path):
    hosts = tmp_path / "hosts.json"
    hosts.write_text('{"hosts": ["ssh user@one"]}\n')
    try:
        read_hosts(hosts)
    except ValueError as exception:
        assert "invalid SSH destination" in str(exception)
    else:
        raise AssertionError("shell command should not be accepted as a host")


def test_splits_which_range_across_workers():
    assignments = split_range(1, 5, ["h1", "h2", "h3"])
    assert assignments == {"h1": [1, 2], "h2": [3, 4], "h3": [5, 5]}


def test_worker_is_detached_from_controller_and_records_completion():
    script = worker_script("job", "exp_one", 2, 5)
    assert "benchmarking/run_exp.py --which 2 5" in script
    assert "exit_code" in script
    assert "succeeded" in script


def test_typecheck_worker_runs_only_typechecking():
    script = worker_script("job", "exp_one", 2, 5, "typecheck")
    assert "benchmarking/sample_tc.py exp_one" in script
    assert "benchmarking/run_exp.py" not in script


def test_merge_timing_samples_fills_only_nonempty_masks():
    merged = {"b": {"v": {"1": [], "2": [2.0]}}}
    shard = {"b": {"v": {"1": [1.0], "2": []}}}
    merge_timing_samples(merged, shard)
    assert merged == {"b": {"v": {"1": [1.0], "2": [2.0]}}}
