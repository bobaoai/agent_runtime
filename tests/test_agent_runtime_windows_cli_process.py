"""The started CLI process on Windows, with controlled synthetic native Python processes.

Runtime starts one native program, stops that process with TerminateProcess and
a bounded wait, and reports exit, stop confirmation and log completeness as
separate facts. Processes the CLI starts itself are not supervised. Each
synthetic process records its own PID and creation time; test hygiene stops only
those identities and an independently started unrelated process must survive.
No model credentials, process-name scans or user terminals are involved.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from threading import Event, Timer
from types import SimpleNamespace

import psutil
import pytest

from agent_runtime.invocation import invocation_process_execution as processes
from agent_runtime.invocation.invocation_host_platform_selection import HOST_PLATFORM as HOST
from agent_runtime.invocation.invocation_process_execution import run_cli_process
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="native Windows process control")
pytestmark = pytest.mark.deterministic
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

DESCENDANT = r'''
import os, subprocess, sys, time
from pathlib import Path
import psutil
record, label, spawn_late = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "late"
def mark(name, text):
    temporary = record / (name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, record / name)
mark(label + ".pid", "%d %r" % (os.getpid(), psutil.Process().create_time()))
if spawn_late:
    time.sleep(0.2)  # Started after the tree is already running.
    subprocess.Popen([sys.executable, __file__, str(record), "late_canary", ""], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(60)  # Bounded lifetime even if a regression leaves it running.
'''

ROOT = r'''
import os, subprocess, sys, time
from pathlib import Path
import psutil
record, descendant, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
def mark(name, text):
    temporary = record / (name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, record / name)
mark("root.pid", "%d %r" % (os.getpid(), psutil.Process().create_time()))
quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if mode == "quiet_exit":
    subprocess.Popen([sys.executable, descendant, str(record), "child", "late"], **quiet)
else:
    # This child holds the captured stdout pipe after the root exits and starts a late canary.
    subprocess.Popen([sys.executable, descendant, str(record), "child", "late"], stdin=subprocess.DEVNULL,
                     stdout=sys.stdout.fileno(), stderr=subprocess.DEVNULL)
subprocess.Popen([sys.executable, descendant, str(record), "detached", ""],
                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, **quiet)
if mode != "quiet_exit":
    try:
        subprocess.Popen([sys.executable, descendant, str(record), "breakaway", ""],
                         creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB, **quiet)
        mark("breakaway.result", "started")
    except OSError as error:
        mark("breakaway.result", "refused:%s" % getattr(error, "winerror", None))
deadline = time.monotonic() + 20
while time.monotonic() < deadline and not all(
        (record / (name + ".pid")).exists() for name in ("child", "detached", "late_canary")):
    time.sleep(0.02)
sys.stdout.buffer.write(b"tree ready\n")
sys.stdout.flush()
if mode == "exit":
    # Exit before every descendant, once the test has sampled the tree.
    while time.monotonic() < deadline and not (record / "release").exists():
        time.sleep(0.02)
if mode == "flood":
    while True:
        sys.stdout.buffer.write(b"x" * 65536)
if mode == "sleep":
    time.sleep(60)
'''

TREE = ("root", "child", "detached", "late_canary")


def wait_for(paths, seconds=20.0):
    deadline = time.monotonic() + seconds
    while not all(path.exists() for path in paths):
        if time.monotonic() > deadline:
            raise AssertionError("synthetic process did not report: " + ", ".join(p.name for p in paths if not p.exists()))
        time.sleep(0.02)


def owned_identity(record, label):
    """psutil handle for a synthetic process that recorded its own PID and creation time, else None."""
    pid, created = (record / f"{label}.pid").read_text().split()
    try:
        process = psutil.Process(int(pid))
        return process if abs(process.create_time() - float(created)) < 0.001 else None
    except psutil.NoSuchProcess:
        return None


def gone(identity, seconds=5.0) -> bool:
    if identity is None:
        return True
    _, alive = psutil.wait_procs([identity], timeout=seconds)
    return not alive


def recorded_gone(pid, created, seconds=5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if abs(psutil.Process(pid).create_time() - created) >= 0.001:
                return True  # The PID now names some other process; ours is gone.
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def tree(tmp_path):
    record = tmp_path / "record"
    record.mkdir()
    (tmp_path / "descendant.py").write_text(DESCENDANT)
    (tmp_path / "root.py").write_text(ROOT)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = SimpleNamespace(record=record, descendant=tmp_path / "descendant.py", root=tmp_path / "root.py",
                            unrelated=unrelated, owned={})
    try:
        yield state
    finally:
        # Hygiene after a failed regression only; never counted as Runtime cleanup.
        for label in ("root", "child", "detached", "late_canary", "breakaway", "probe_descendant"):
            if (record / f"{label}.pid").exists():
                identity = owned_identity(record, label)
                if identity is not None and identity.is_running():
                    identity.kill()
        unrelated.kill()
        unrelated.wait(timeout=10)


def tree_argv(tree, mode):
    return [sys.executable, str(tree.root), str(tree.record), str(tree.descendant), mode]


@WINDOWS
def test_root_exit_code_is_reported_without_supervising_its_descendant(tree):
    record = tree.record
    script = ("import subprocess,sys,time; from pathlib import Path;"
              f"subprocess.Popen([sys.executable,{str(tree.descendant)!r},{str(record)!r},'child',''],"
              "stdin=-3,stdout=-3,stderr=-3);"
              f"[time.sleep(.02) for _ in range(1000) if not Path({str(record)!r},'child.pid').exists()];"
              "sys.exit(7)")
    result = run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=record, timeout_seconds=30,
                             environment=dict(os.environ))
    # The started process's own exit code; its descendant is the CLI's business (test hygiene stops it).
    assert result.returncode == 7 and result.output_streams_closed is True


@WINDOWS
def test_descendant_holding_stdout_is_a_log_gap_not_a_stop_failure(tree):
    record = tree.record
    script = ("import subprocess,sys,time; from pathlib import Path;"
              f"subprocess.Popen([sys.executable,{str(tree.descendant)!r},{str(record)!r},'child',''],"
              "stdin=-3,stdout=sys.stdout.fileno(),stderr=-3);"
              f"[time.sleep(.02) for _ in range(1000) if not Path({str(record)!r},'child.pid').exists()];"
              "sys.stdout.write('root done\\n'); sys.stdout.flush()")
    started = time.monotonic()
    result = run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=record, timeout_seconds=30,
                             environment=dict(os.environ))
    assert result.returncode == 0 and result.stdout_bytes.startswith(b"root done")
    assert result.output_streams_closed is False  # A CLI-internal process still holds the pipe: a log gap.
    assert time.monotonic() - started < 15  # Bounded join, no wait for the descendant's 60 s lifetime.
    assert owned_identity(record, "child") is not None  # Not supervised by Runtime; hygiene stops it.


@WINDOWS
def test_timeout_and_cancel_stop_the_started_process(tmp_path):
    script = "import sys,time; sys.stdout.buffer.write(b'alive\\n'); sys.stdout.flush(); time.sleep(60)"
    with pytest.raises(processes.CliProcessTimeout) as timed_out:
        run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=tmp_path, timeout_seconds=1,
                        environment=dict(os.environ))
    assert timed_out.value.cleanup_error is None and timed_out.value.returncode is not None
    assert timed_out.value.stdout_bytes == b"alive\n"
    cancel = Event()
    Timer(0.5, cancel.set).start()
    with pytest.raises(processes.CliProcessInterrupted) as interrupted:
        run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=tmp_path, timeout_seconds=30,
                        environment=dict(os.environ), user_cancel_requested=cancel.is_set)
    assert interrupted.value.cleanup_error is None and interrupted.value.returncode is not None


@WINDOWS
@pytest.mark.parametrize("code", [0, 7, 259, 0xC0000005])
def test_actual_native_exit_code_is_reported_after_the_handle_is_signaled(tmp_path, code):
    """259 is STILL_ACTIVE: it is a real exit code here because it is read only after the wait."""
    script = f"import ctypes; ctypes.WinDLL('kernel32').ExitProcess(ctypes.c_uint({code}))"
    result = run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=tmp_path, timeout_seconds=10,
                             environment=dict(os.environ))
    assert result.returncode == code


CANARY = "import sys; from pathlib import Path; Path(sys.argv[1]).write_text('executed')"


@WINDOWS
def test_failed_start_creates_no_process(tmp_path):
    image = tmp_path / "not_native.exe"
    image.write_bytes(b"MZ synthetic bytes that are not a Windows image")
    with pytest.raises(processes.CliProcessStartupError) as error:
        run_cli_process(argv=[str(image)], prompt="", cwd=tmp_path, timeout_seconds=10, environment=dict(os.environ))
    assert error.value.process_created is False and error.value.cleanup_error is None


@WINDOWS
def test_script_launchers_are_refused_before_any_process(tmp_path):
    canary = tmp_path / "canary"
    launcher = tmp_path / "provider.cmd"
    launcher.write_text(f'@echo executed> "{canary}"\r\n')
    with pytest.raises(processes.CliProcessStartupError) as explicit:
        run_cli_process(argv=[str(launcher)], prompt="", cwd=tmp_path, timeout_seconds=10, environment=dict(os.environ))
    with pytest.raises(processes.CliProcessStartupError) as bare:
        run_cli_process(argv=["provider"], prompt="", cwd=tmp_path, timeout_seconds=10,
                        environment={**os.environ, "PATH": str(tmp_path)})
    assert explicit.value.process_created is False and explicit.value.cleanup_error is None
    # Argument fidelity, not a security policy: the launcher's interpreter would re-parse argv.
    assert "re-parses its arguments" in str(explicit.value) and "native" in str(explicit.value)
    assert "only a script launcher exists" in str(bare.value) and bare.value.process_created is False
    assert HOST.resolve_cli_program("provider", {"PATH": str(tmp_path)}) is None
    found = run_cli_process(argv=[Path(sys.executable).stem, "-c", "import sys; sys.stdout.buffer.write(sys.executable.encode())"],
                            prompt="", cwd=tmp_path, timeout_seconds=10,
                            environment={**os.environ, "PATH": str(Path(sys.executable).parent)})
    assert Path(found.stdout_bytes.decode()).parent == Path(sys.executable).parent
    time.sleep(0.3)
    assert not canary.exists()


ECHO = r'''
import hashlib, json, os, sys
data = sys.stdin.buffer.read()
sys.stdout.buffer.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "environment": dict(os.environ),
    "stdin_sha256": hashlib.sha256(data).hexdigest(), "stdin_bytes": len(data)}).encode() + b"\n\xff\xfe\x00\x80")
sys.stderr.buffer.write(b"\x81\xfe raw diagnostic\r\n")
'''


@WINDOWS
def test_exact_argv_cwd_environment_large_stdin_and_raw_bytes(tmp_path):
    import hashlib
    cwd = tmp_path / "dír with spaces 名前"
    cwd.mkdir()
    arguments = ["plain", "with space", 'quote"inside', "trailing\\", 'back\\\\slash\\"', "",
                 "ünïcødé 字", "%PATH%", "^&|<>"]
    environment = {"SYSTEMROOT": os.environ["SYSTEMROOT"], "RUNTIME_SYNTHETIC": "välue with spaces"}
    prompt = "é" * 3_000_000
    result = run_cli_process(argv=[sys.executable, "-c", ECHO, *arguments], prompt=prompt, cwd=cwd,
                             timeout_seconds=60, environment=environment)
    body, tail = result.stdout_bytes.rsplit(b"\n", 1)
    observed = json.loads(body)
    assert observed["argv"] == arguments and observed["cwd"] == str(cwd)
    assert {key.upper(): value for key, value in observed["environment"].items()} == {
        key.upper(): value for key, value in environment.items()}
    expected = prompt.encode("utf-8")
    assert observed["stdin_bytes"] == len(expected) and observed["stdin_sha256"] == hashlib.sha256(expected).hexdigest()
    assert tail == b"\xff\xfe\x00\x80" and result.stderr_bytes == b"\x81\xfe raw diagnostic\r\n"


@WINDOWS
@pytest.mark.parametrize("live", [False, True])
def test_unconfirmed_stop_is_never_reported_as_success(tmp_path, monkeypatch, live):
    original_stop = HOST.stop_cli_process

    def uncertain(process):
        original_stop(process)
        raise RuntimeError("synthetic stop confirmation failure")

    monkeypatch.setattr(HOST, "stop_cli_process", uncertain)
    script = "import sys; sys.stdout.buffer.write(b'evidence\\n'); sys.stdout.flush()"
    if live:
        script += "; import time; time.sleep(60)"
    with pytest.raises((processes.CliProcessError, processes.CliProcessTimeout)) as error:
        run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=tmp_path,
                        timeout_seconds=1 if live else 10, environment=dict(os.environ))
    assert error.value.stdout_bytes == b"evidence\n"
    assert "synthetic stop confirmation failure" in error.value.cleanup_error
    assert processes.cli_process_stop_confirmed(error.value) is False
    if live:
        assert type(error.value) is processes.CliProcessTimeout and error.value.stop_reason == "timeout"
    else:
        assert type(error.value) is processes.CliProcessError and error.value.stop_reason == "cleanup_error"


@WINDOWS
@pytest.mark.parametrize("failure", ["exception", "interrupt", "different_result"])
def test_guard_failure_after_creation_stops_the_created_process(tmp_path, monkeypatch, failure):
    created, started = [], tmp_path / "started"
    original_create = HOST.create_cli_process

    def spy(argv, **kwargs):
        process = original_create(argv, **kwargs)
        created.append(psutil.Process(process.pid))
        return process

    def guard(create):
        create()
        wait_for([started])  # The child really executed inside the guard.
        if failure == "exception":
            raise PermissionError("synthetic guard failure after creation")
        if failure == "interrupt":
            raise KeyboardInterrupt
        return object()

    monkeypatch.setattr(HOST, "create_cli_process", spy)
    script = f"from pathlib import Path; import time; Path({str(started)!r}).write_text('1'); time.sleep(60)"
    expected = processes.CliProcessInterrupted if failure == "interrupt" else processes.CliProcessStartupError
    with pytest.raises(expected) as error:
        run_cli_process(argv=[sys.executable, "-c", script], prompt="", cwd=tmp_path, timeout_seconds=30,
                        environment=dict(os.environ), launch_guard=guard)
    assert error.value.cleanup_error is None
    if expected is processes.CliProcessStartupError:
        assert error.value.process_created is True
    assert len(created) == 1 and gone(created[0])


def test_shared_runner_contains_no_operating_system_dispatch():
    source = Path(processes.__file__).read_text(encoding="utf-8")
    for platform_knowledge in ("os.name", "sys.platform", "killpg", "psutil", "subprocess.Popen"):
        assert platform_knowledge not in source, platform_knowledge


def test_posix_host_keeps_session_group_launch_and_stop_mapping_without_native_posix(monkeypatch, tmp_path):
    """Maps the POSIX operations on this host; actual POSIX enforcement needs a POSIX host."""
    path = Path(processes.__file__).with_name("invocation_posix_host_operation.py")
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("posix_process_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "psutil" not in Path(path).read_text(encoding="utf-8")
    launches, seen = [], []
    monkeypatch.setattr(module.subprocess, "Popen", lambda argv, **kwargs: launches.append((argv, kwargs)) or "process")
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: seen.append(("killpg", pid)), raising=False)
    monkeypatch.setattr(module.signal, "SIGKILL", 9, raising=False)
    host = module.PosixHostPlatform()
    assert host.create_cli_process(["cli", "--flag"], cwd=tmp_path, environment={"A": "1"}) == "process"
    assert launches == [(["cli", "--flag"], {"cwd": tmp_path, "env": {"A": "1"}, "stdin": subprocess.PIPE,
                                             "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                             "start_new_session": True})]
    waited = SimpleNamespace(pid=41, poll=lambda: None, wait=lambda timeout: seen.append(("wait", timeout)))
    host.stop_cli_process(waited)
    assert seen == [("killpg", 41), ("wait", 3)]
    stuck = SimpleNamespace(pid=42, poll=lambda: None,
                            wait=lambda timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired("cli", timeout)))
    with pytest.raises(RuntimeError, match="did not exit"):
        host.stop_cli_process(stuck)
    assert not hasattr(host, "observe_cli_process") and not hasattr(host, "release_cli_process")


PROBE = r'''
import subprocess, sys, time
from pathlib import Path
record, behavior, descendant = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if behavior == "descendant":
    subprocess.Popen([sys.executable, descendant, str(record), "probe_descendant", ""], creationflags=0x8 | 0x200,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not (record / "probe_descendant.pid").exists():
        time.sleep(0.02)
    print("2.1.999 (synthetic probe)")  # Text mode: CRLF on Windows.
elif behavior == "nonzero":
    print("synthetic probe failure")
    sys.exit(3)
else:
    time.sleep(60)
'''


@WINDOWS
@pytest.mark.parametrize("case", ["exits_before_descendant", "nonzero", "deadline", "expired", "refused", "uncertain"])
def test_claude_preflight_probe_runs_in_the_owned_runner(tree, monkeypatch, case):
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    record = tree.record
    probe = record.parent / "probe.py"
    probe.write_text(PROBE)
    behavior = {"exits_before_descendant": "descendant", "nonzero": "nonzero"}.get(case, "sleep")
    argv = [sys.executable, str(probe), str(record), behavior, str(tree.descendant)]
    created, original_create, original_stop = [], HOST.create_cli_process, HOST.stop_cli_process

    def spy(argv, **kwargs):
        created.append(argv)
        return original_create(argv, **kwargs)

    monkeypatch.setattr(HOST, "create_cli_process", spy)
    deadline = None
    if case == "deadline":
        deadline = time.monotonic() + 0.5
    if case == "expired":
        deadline = time.monotonic() - 1
    if case == "refused":
        argv = [str(record.parent / "claude.cmd"), "--version"]
        Path(argv[0]).write_text(f'@echo executed> "{record / "canary"}"\r\n')
    if case == "uncertain":
        argv[3] = "nonzero"

        def uncertain(process):
            original_stop(process)
            raise RuntimeError("synthetic probe cleanup uncertainty")
        monkeypatch.setattr(HOST, "stop_cli_process", uncertain)
    started = time.monotonic()
    if case == "exits_before_descendant":
        assert claude._run_cli_preflight(argv, environment=dict(os.environ), deadline=None).strip() == \
            "2.1.999 (synthetic probe)"
        return  # The probe's own detached descendant is not supervised; fixture hygiene stops it.
    expected = {"nonzero": subprocess.CalledProcessError, "deadline": processes.CliProcessTimeout,
                "expired": processes.CliProcessTimeout, "refused": processes.CliProcessStartupError,
                "uncertain": processes.CliProcessError}[case]
    with pytest.raises(expected) as error:
        claude._run_cli_preflight(argv, environment=dict(os.environ), deadline=deadline)
    if case == "nonzero":
        assert error.value.returncode == 3 and error.value.output == "synthetic probe failure\n"
    if case == "deadline":
        assert time.monotonic() - started < 10 and error.value.cleanup_error is None  # Not the 30 s limit.
    if case == "expired":
        assert created == [] and error.value.returncode is None
    if case == "refused":
        assert error.value.process_created is False and created == [argv]
        time.sleep(0.3)
        assert not (record / "canary").exists()
    if case == "uncertain":
        assert error.value.stop_reason == "cleanup_error"
        assert "synthetic probe cleanup uncertainty" in error.value.cleanup_error


FAKE_CLAUDE = r'''
import json, subprocess, sys, time
from pathlib import Path
config = json.loads(Path(__file__).with_name("fake_claude.json").read_text())
probe = sys.argv[-1]
behavior = config.get(probe, "ok")
if behavior == "sleep":
    time.sleep(60)
if probe == "--version":
    if behavior == "descendant":
        subprocess.Popen([config["python"], config["descendant"], config["record"], "probe_descendant", ""],
                         creationflags=0x8 | 0x200, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        while not Path(config["record"], "probe_descendant.pid").exists():
            time.sleep(0.02)
    print("2.1.999 (Claude Code synthetic)")
    sys.exit(3 if behavior == "nonzero" else 0)
print("--safe-mode --restricted --tools --allowedTools --permission-mode --settings --effort --json-schema --setting-sources --strict-mcp-config --add-dir")
'''


def _claude_adapter(tree, monkeypatch, behaviors, *, cli_name="claude", stop_after=None):
    """A direct Claude adapter whose CLI is a synthetic script run through an explicit native interpreter.

    The rewrite isolates Provider syntax from native process behavior, as the Codex
    launch test does; the preflight and its Job are real. The main call uses an
    in-process fake runner and records whether it started.
    """
    import test_agent_runtime_claude_native_tools as claude_cases
    import test_agent_runtime_native_structured_output as native_cases
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    folder = tree.record.parent / "cli"
    folder.mkdir()
    cli = folder / cli_name
    cli.write_text(FAKE_CLAUDE)
    (folder / "fake_claude.json").write_text(json.dumps({**behaviors, "python": sys.executable,
        "descendant": str(tree.descendant), "record": str(tree.record)}))
    events, probes = [], {}
    original_create, original_stop = HOST.create_cli_process, HOST.stop_cli_process

    def create(argv, **kwargs):
        if argv[0] == str(cli) and cli.suffix == "":
            events.append(("preflight", list(argv), kwargs["cwd"], dict(kwargs["environment"])))
            process = original_create([sys.executable, str(cli), *argv[1:]], **kwargs)
            probes[id(process)] = argv[-1]
            return process
        return original_create(argv, **kwargs)

    def stop(process):
        original_stop(process)
        if stop_after is not None and probes.get(id(process)) == stop_after:
            raise RuntimeError("synthetic preflight cleanup uncertainty")

    monkeypatch.setattr(HOST, "create_cli_process", create)
    monkeypatch.setattr(HOST, "stop_cli_process", stop)

    def main(**fields):
        events.append(("main", list(fields["argv"])))
        lines = [json.dumps(event) for event in (claude_cases._init(()), claude_cases._result())]
        for line in lines:
            assert fields["on_stdout_line"](line)
        result = subprocess.CompletedProcess(fields["argv"], 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result

    compiled, registry, cell, _, _ = claude_cases._environment(tree.record.parent / "module", tools=(),
                                                               mode="tool_free", workspace="none")
    prompt = native_cases._evaluation_prompt(cell, compiled, suffix="owned_preflight")
    request = native_cases._direct_adapter_request(compiled, prompt, suffix="owned_preflight")
    adapter = claude.ClaudeAdapter(release_registry=registry, artifact_host=cell,
                                   workspace_root=tree.record.parent / "attempts", cli_path=cli, process_runner=main)
    return SimpleNamespace(adapter=adapter, request=request, cell=cell, cli=cli, events=events,
                           host_class=native_cases._RecordingHost, detail=native_cases._direct_failure_detail)


def _adapter_trace(env, result):
    return json.loads(env.cell.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256))


@WINDOWS
def test_claude_adapter_preflight_sites_use_the_owned_runner(tree, monkeypatch):
    (tree.record.parent / "module").mkdir()
    env = _claude_adapter(tree, monkeypatch, {"--version": "descendant"})
    result = env.adapter.execute(env.request, env.host_class())
    trace = _adapter_trace(env, result)
    assert result.terminal_status == "completed"
    preflights = [event for event in env.events if event[0] == "preflight"]
    assert [event[1] for event in preflights] == [[str(env.cli), "--version"], [str(env.cli), "--help"]]
    assert all(event[2] == Path.cwd() and event[3] == trace["environment"] for event in preflights)
    assert [event[0] for event in env.events] == ["preflight", "preflight", "main"]
    assert trace["cli_version"] == "2.1.999 (Claude Code synthetic)"  # CRLF text parsed as before.


@WINDOWS
@pytest.mark.parametrize("case", ["nonzero", "deadline", "refused", "uncertain"])
def test_failed_or_uncertain_claude_preflight_never_starts_the_main_invocation(tree, monkeypatch, case):
    (tree.record.parent / "module").mkdir()
    behaviors = {"nonzero": {"--version": "nonzero"}, "deadline": {"--help": "sleep"}}.get(case, {})
    env = _claude_adapter(tree, monkeypatch, behaviors, cli_name="claude.cmd" if case == "refused" else "claude",
                          stop_after="--version" if case == "uncertain" else None)
    if case == "refused":
        env.cli.write_text(f'@echo executed> "{tree.record / "canary"}"\r\n')
    host = env.host_class()
    if case == "deadline":
        deadline = time.monotonic() + 3
        host.run_deadline = lambda request: deadline
    started = time.monotonic()
    result = env.adapter.execute(env.request, host)
    trace = _adapter_trace(env, result)
    detail = env.detail(result, env.cell)
    assert result.terminal_status == "failed" and not [event for event in env.events if event[0] == "main"]
    assert trace["stage"] == "cli_preflight"
    if case == "deadline":
        assert result.failure.failure_class == "timeout" and detail["failure_code"] == "claude_cli_timeout"
        assert time.monotonic() - started < 15  # The live deadline, not the 30 s probe limit.
    elif case == "uncertain":
        # An unconfirmed stop retains the private crt- folder and reports where (plan 4.8, R7).
        assert result.failure.failure_class == "transport" and detail["failure_code"] == "claude_cli_cleanup_failed"
        assert "retained at" in detail["provider_error_message"]
    else:
        assert result.failure.failure_class == "dependency_unavailable"
        assert detail["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
    if case == "nonzero":
        assert trace["exit_code"] == 3 and [event[1][-1] for event in env.events] == ["--version"]
    if case == "refused":
        assert trace["process_created"] is False and env.events == []
        time.sleep(0.3)
        assert not (tree.record / "canary").exists()
    if case == "uncertain":
        assert "synthetic preflight cleanup uncertainty" in trace["cleanup_error"]
        assert [event[1][-1] for event in env.events] == ["--version"]


@WINDOWS
def test_codex_version_probe_uncertainty_blocks_exec(tmp_path, monkeypatch):
    import test_agent_runtime_codex_execution_outcomes as cases
    from agent_runtime.invocation import invocation_codex_module_invocation as codex
    executable = tmp_path / "synthetic_codex"
    executable.write_text("import sys\n"
                          "if sys.argv[1:] == ['--version']:\n"
                          "    print('codex synthetic-local-process')\n"
                          "else:\n"
                          "    raise SystemExit(3)\n")
    env = cases.environment(tmp_path, lambda _: pytest.fail("injected invoker must not execute"))
    env.executor._invoker = codex._default_invoke
    env.executor._codex_bin = str(executable)
    order, probes = [], {}
    original_create, original_stop = HOST.create_cli_process, HOST.stop_cli_process

    def create(argv, **kwargs):
        if str(argv[0]) == str(executable):
            kind = "version" if "--version" in argv else "exec"
            order.append(kind)
            process = original_create([sys.executable, str(executable), *argv[1:]], **kwargs)
            probes[id(process)] = kind
            return process
        return original_create(argv, **kwargs)

    def stop(process):
        original_stop(process)
        if probes.get(id(process)) == "version":
            raise RuntimeError("synthetic version-probe cleanup uncertainty")

    monkeypatch.setattr(HOST, "create_cli_process", create)
    monkeypatch.setattr(HOST, "stop_cli_process", stop)
    result, trace = cases.execute(env)
    # The unconfirmed probe stop is reported and the main exec never starts. The private
    # schema folder is retained at its reported location (plan 4.8), so the result is a
    # cleanup failure rather than a plain process failure. No private login state exists.
    assert order == ["version"] and result.terminal_status == "failed"
    assert "synthetic version-probe cleanup uncertainty" in trace["cleanup_error"]
    detail = cases.detail(env, result)
    assert detail["failure_code"] == "codex_cli_cleanup_failed"
    assert "retained at" in detail["provider_error_message"] and "not confirmed stopped" in detail["provider_error_message"]
    retained = [path for path in (tmp_path / "workspaces").iterdir() if path.name.startswith(".")
                and "_schema_" in path.name]
    assert len(retained) == 1 and (retained[0] / "output_schema.json").is_file()
