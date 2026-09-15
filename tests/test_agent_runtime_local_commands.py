"""Local Runtime commands: real sandbox/process/IPC, no model or Product grants."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import site
import subprocess
import sys
import threading
import time
import venv
from types import SimpleNamespace

import pytest

from agent_runtime import evaluate_local_workflow_module
from agent_runtime.contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from agent_runtime.invocation.invocation_local_command_execution import LocalCommandSession, LOCAL_COMMAND_CLI_TOOL_NAME
from agent_runtime.invocation.invocation_local_command_mcp import exchange
from agent_runtime.invocation.invocation_local_resource_preparation import (
    capture_local_resources, materialize_local_resources, LOCAL_RESOURCES_SCHEMA_REF,
    LOCAL_RESOURCES_SCHEMA_SHA256, LOCAL_RESOURCES_MEDIA_TYPE, LOCAL_RESOURCES_LOGICAL_NAME,
)
from test_agent_runtime_claude_native_tools import _environment


class LiveHostDouble:
    """A narrow live host double, not a ProductAuthorization decision or receipt."""
    def __init__(self):
        self.active, self.launches = True, 0
        self.lock = threading.RLock()
        self.expected = None

    def validate_self_test_binding(self, request, **values):
        if not self.active or (request, values) != self.expected:
            raise SelfTestResourceUnavailableError("test resources are closed or mismatched")

    def guard_self_test_launch(self, request, launch, **values):
        with self.lock:
            self.validate_self_test_binding(request, **values)
            process = launch()
            self.launches += 1
            return process

    def close(self):
        with self.lock:
            self.active = False

    def read_authorized_input(self, handle):
        assert handle == "control"
        return self.body


def session_fixture(tmp_path, commands, *, source_files=None):
    profile = _environment(tmp_path)[0].execution_profile
    original = tmp_path / "original"
    original.mkdir()
    if source_files is None:
        source_files = {"module.py": (b"VALUE = 17\n", False)}
    material_files = []
    for name, (content, executable) in source_files.items():
        target = original / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if executable else 0o644)
        material_files.append({"relative_path": name, "sha256": hashlib.sha256(content).hexdigest(),
                               "executable": executable})
    dependencies = (Path(sys.base_prefix).resolve(),)
    body = capture_local_resources(profile=profile, material_root=original,
        material_files=tuple(material_files),
        read_only_dependencies=dependencies, commands=tuple(commands))
    workspace = tmp_path / "attempt"
    materials, scratch = workspace / "work/materials", workspace / "work/scratch"
    materials.mkdir(parents=True)
    scratch.mkdir()
    materialize_local_resources(body, materials_root=materials)
    control = SimpleNamespace(schema_ref=LOCAL_RESOURCES_SCHEMA_REF, schema_sha256=LOCAL_RESOURCES_SCHEMA_SHA256,
        logical_name=LOCAL_RESOURCES_LOGICAL_NAME, media_type=LOCAL_RESOURCES_MEDIA_TYPE,
        input_sha256=hashlib.sha256(body).hexdigest(), local_handle="control")
    request, adapter, artifacts = SimpleNamespace(authorized_inputs=(control,), execution_profile_ref=profile.release_ref,
        execution_profile_sha256=profile.release_sha256), object(), object()
    host = LiveHostDouble()
    host.body = body
    host.expected = (request, {"adapter": adapter, "artifact_host": artifacts,
        "workspace_root": workspace.resolve(), "read_only_dependencies": dependencies})
    session = LocalCommandSession(request=request, profile=profile, host=host, adapter=adapter, artifact_host=artifacts,
        workspace_root=workspace, resources_body=body, source_root=materials / "source", scratch_root=scratch,
        read_only_dependencies=dependencies)
    return SimpleNamespace(session=session, host=host, materials=materials, scratch=scratch, workspace=workspace,
                           dependencies=dependencies, original=original, body=body)


def command(identity, script, *, cwd="scratch", timeout=5):
    return {"command_id": identity, "argv": [sys.executable, "-I", "-B", "-c", script], "cwd": cwd, "timeout_seconds": timeout}


def test_real_command_identity_exit_and_byte_output_are_kept(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "import os;os.write(1,b'out\\xff');os.write(2,b'err\\xfe')"),
                                    command("nonzero", "import sys;print('failed check');sys.exit(7)")])
    with env.session as session:
        ok, nonzero = session.invoke("ok"), session.invoke("nonzero")
        session.validate_completion()
        assert ok["allowed"] and ok["returncode"] == 0 and ok["process_output_complete"]
        assert base64.b64decode(ok["raw_streams"]["stdout"]["data"]) == b"out\xff"
        assert base64.b64decode(ok["raw_streams"]["stderr"]["data"]) == b"err\xfe"
        assert ok["cwd"] == str(env.scratch) and ok["argv"][0] == str(Path(sys.executable).absolute())
        assert nonzero["returncode"] == 7 and nonzero["process_output_complete"] and nonzero["failure"] is None
        assert [row["status"] for row in session.records] == ["completed", "failed"]
        assert [row["response"]["local_call_id"] for row in session.records] == ["local_command_1", "local_command_2"]
    assert not session.private_root.exists()


def test_unknown_command_refusal_is_not_an_invocation_failure(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        rejected = session.invoke("unknown")
        assert rejected["allowed"] is False and rejected["returncode"] is None and rejected["local_call_id"]
        assert env.host.launches == 0
        assert session.invoke("ok")["returncode"] == 0
        session.validate_completion()
        assert env.host.launches == 1


def test_actual_candidate_import_works_with_read_only_source(tmp_path):
    env = session_fixture(tmp_path, [command("import", "import sys;sys.path.insert(0,'.');from module import VALUE;print(VALUE)", cwd="source")])
    with env.session as session:
        response = session.invoke("import")
        assert response["returncode"] == 0 and response["stdout"] == "17\n"
        assert response["cwd"] == str(env.materials / "source")
        assert not (env.materials / "source/__pycache__").exists()
        session.validate_completion()


def test_runtime_prepares_declared_scratch_subdirectory(tmp_path):
    env = session_fixture(tmp_path, [command("nested", "from pathlib import Path;Path('proof').write_text('done');print('done')",
                                            cwd="scratch/nested/test_output")])
    with env.session as session:
        response = session.invoke("nested")
        assert response["returncode"] == 0 and response["cwd"] == str(env.scratch / "nested/test_output")
        assert (env.scratch / "nested/test_output/proof").read_text() == "done"


@pytest.mark.parametrize(("cwd", "program", "script_path"), [
    ("source", "./run.sh", "run.sh"),
    ("source", "nested/run.sh", "nested/run.sh"),
    ("source/nested", "../run.sh", "run.sh"),
])
def test_relative_executable_uses_the_declared_command_directory(tmp_path, cwd, program, script_path):
    files = {script_path: (b"#!/bin/sh\nprintf 'relative-script-ok\\n'\n", True),
             "nested/marker": (b"declared source directory", False)}
    definition = {"command_id": "script", "argv": [program], "cwd": cwd, "timeout_seconds": 5}
    env = session_fixture(tmp_path, [definition], source_files=files)
    with env.session as session:
        response = session.invoke("script")
        session.validate_completion()
        assert response["returncode"] == 0 and response["stdout"] == "relative-script-ok\n"
        assert Path(response["argv"][0]).samefile(env.materials / "source" / script_path)
        assert response["declared_argv"] == [program] and response["declared_cwd"] == cwd
        assert response["cwd"] == str(env.materials / cwd)


@pytest.mark.parametrize("program", ["python", "python3", sys.executable])
def test_python_default_is_the_running_runtime_environment(tmp_path, program):
    definition = command("python", "import sys;print(sys.prefix)")
    definition["argv"][0] = program
    env = session_fixture(tmp_path, [definition])
    with env.session as session:
        response = session.invoke("python")
        assert response["returncode"] == 0, response
        assert response["stdout"].strip() == sys.prefix
        assert response["argv"][0] == str(Path(sys.executable).absolute())
        assert session.mcp_config["mcpServers"]["runtime_commands"]["command"] == str(Path(sys.executable).absolute())
        assert session._environment["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).absolute().parent)


def test_current_python_venv_dependency_survives_direct_and_shell_launch(tmp_path):
    from agent_runtime.invocation import invocation_local_command_execution as implementation
    environment = tmp_path / "runtime_python"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    assert python.is_symlink()
    library = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    (library / "runtime_venv_probe.py").write_text("VALUE = 'only-in-current-runtime-venv'\n")
    # The parent Runtime really starts in the new venv. Existing test libraries
    # only support this parent fixture; the sandboxed commands must import the
    # marker from that venv's own site-packages without a PYTHONPATH override.
    paths = [str(Path(implementation.__file__).parents[2]), str(Path(__file__).parent), *site.getsitepackages()]
    child = """import json,sys
from pathlib import Path
sys.path[:0]=PATHS
from test_agent_runtime_local_commands import session_fixture, command
case=Path(CASE)
case.mkdir()
code="import sys,runtime_venv_probe; print(sys.prefix); print(runtime_venv_probe.VALUE)"
definitions=[command('direct',code), command('alias',code)]
definitions[1]['argv'][0]='python'
definitions.append({'command_id':'shell','argv':['/bin/sh','-c','python -I -B -c '+__import__('shlex').quote(code)],'cwd':'source','timeout_seconds':5})
env=session_fixture(case,definitions)
with env.session as session:
 results=[session.invoke(item['command_id']) for item in definitions]
 session.validate_completion()
 for result in results:
  assert result['returncode']==0,result
  assert result['stdout'].splitlines()==[sys.prefix,'only-in-current-runtime-venv'],result
 assert results[0]['argv'][0]==sys.executable
 print(json.dumps({'prefix':sys.prefix,'results':results}))
""".replace("PATHS", repr(paths)).replace("CASE", repr(str(tmp_path / "case")))
    process = subprocess.run([str(python), "-I", "-B", "-c", child], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["prefix"] == str(environment)


def test_missing_current_python_does_not_fall_back(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_process_execution as implementation
    monkeypatch.setattr(implementation.sys, "executable", str(tmp_path / "missing/python"))
    with pytest.raises(FileNotFoundError, match="Current Runtime Python"):
        session_fixture(tmp_path, [command("unreachable", "print('must not run')")])


def test_relative_executable_still_cannot_escape_readable_resources(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "print('not executed')")])
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\nexit 0\n")
    outside.chmod(0o755)
    with env.session as session:
        relative = os.path.relpath(outside, env.materials / "source")
        with pytest.raises(PermissionError, match="outside"):
            session._resolve_command({"argv": [relative], "cwd": "source"})
        assert env.host.launches == 0


def test_scratch_subdirectory_symlink_cannot_escape(tmp_path):
    env = session_fixture(tmp_path, [command("nested", "print('must not run')", cwd="scratch/nested")])
    with env.session as session:
        (env.scratch / "nested").rmdir()
        (env.scratch / "nested").symlink_to(tmp_path)
        response = session.invoke("nested")
        assert response["returncode"] is None and env.host.launches == 0
        assert response["failure"]["error_type"] == "PermissionError"


def test_scratch_parent_replacement_cannot_create_an_external_directory(tmp_path, monkeypatch):
    env = session_fixture(tmp_path, [command("nested", "print('must not run')", cwd="scratch/nested/test_output")])
    outside = tmp_path / "outside"
    outside.mkdir()
    original_mkdir = os.mkdir
    replaced = []
    with env.session as session:
        (env.scratch / "nested/test_output").rmdir()
        def replace_parent_before_mkdir(path, mode=0o777, *, dir_fd=None):
            if Path(path).name == "test_output" and not replaced:
                # Same interleaving for both implementations: native scratch
                # work replaces the parent immediately before the actual mkdir.
                (env.scratch / "nested").rename(env.scratch / "moved_nested")
                (env.scratch / "nested").symlink_to(outside)
                replaced.append(True)
            return original_mkdir(path, mode, dir_fd=dir_fd)
        monkeypatch.setattr(os, "mkdir", replace_parent_before_mkdir)
        response = session.invoke("nested")
        assert replaced
        assert list(outside.iterdir()) == []
        assert env.host.launches == 0 and response["allowed"] is False
        assert response["failure"] is not None


@pytest.mark.parametrize("failure_kind", ["cleanup_error", "timeout", "interrupted", "cleanup_reason_only", "closed_interrupted"])
def test_command_cleanup_failure_invalidates_completion_and_keeps_evidence(tmp_path, monkeypatch, failure_kind):
    from agent_runtime.invocation import invocation_local_command_execution as implementation
    from agent_runtime.invocation.invocation_process_execution import CliProcessError, CliProcessTimeout, CliProcessInterrupted
    env = session_fixture(tmp_path, [command("broken", "print('not launched by this fault double')")])
    facts = dict(returncode=-9, output="out\ufffd", stderr="err\ufffd", stdout_bytes=b"out\xff", stderr_bytes=b"err\xfe",
                 cleanup_error="output pipe did not close")
    if failure_kind == "timeout":
        failure = CliProcessTimeout([sys.executable], 1, **facts)
    elif failure_kind in {"interrupted", "closed_interrupted"}:
        failure = CliProcessInterrupted(**facts)
    else:
        if failure_kind == "cleanup_reason_only":
            facts["cleanup_error"] = None
        failure = CliProcessError(cmd=[sys.executable], stop_reason="cleanup_error", message="cleanup failed", **facts)
    def failed_capture(**_):
        if failure_kind == "closed_interrupted":
            env.host.close()
        raise failure
    monkeypatch.setattr(implementation, "run_cli_process", failed_capture)
    with env.session as session:
        response = session.invoke("broken")
        with pytest.raises(type(failure)) as caught:
            session.validate_completion()
        assert caught.value is failure
        assert response["failure"]["stop_reason"] == ("resource_invalid" if failure_kind == "closed_interrupted" else failure.stop_reason)
        assert response["failure"]["cleanup_error"] == facts["cleanup_error"]
        assert not response["process_output_complete"] and response["returncode"] == -9
        assert base64.b64decode(response["raw_streams"]["stdout"]["data"]) == b"out\xff"
        assert base64.b64decode(response["raw_streams"]["stderr"]["data"]) == b"err\xfe"
        assert session.records[0]["response"] == response
    # Completion cannot become valid merely because IPC/control cleanup finished.
    with pytest.raises(type(failure)):
        session.validate_completion()


@pytest.mark.parametrize("boundary", ["source_write", "outside_read", "network"])
def test_command_itself_is_sandboxed(tmp_path, boundary):
    outside = tmp_path / "outside"
    outside.write_text("secret test sentinel")
    scripts = {
        "source_write": "from pathlib import Path;Path('../materials/source/module.py').write_text('changed')",
        "outside_read": "from pathlib import Path;print(Path(" + repr(str(outside)) + ").read_text())",
        "network": "import socket; socket.create_connection(('127.0.0.1',9),timeout=1)",
    }
    env = session_fixture(tmp_path, [command("probe", scripts[boundary])])
    with env.session as session:
        response = session.invoke("probe")
        assert response["returncode"] != 0 and response["process_output_complete"]
        assert "secret test sentinel" not in response["stdout"]
        assert (env.materials / "source/module.py").read_bytes() == b"VALUE = 17\n"
        if boundary == "network":
            assert "Operation not permitted" in response["stderr"]
        session.validate_completion()


def test_generated_command_sandbox_denies_its_actual_control_files(tmp_path):
    from agent_runtime.invocation.invocation_process_execution import run_cli_process
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        private = session.private_root / "sandbox.sb"
        process = run_cli_process(argv=["/usr/bin/sandbox-exec", "-f", str(private),
            str(Path(sys.executable).resolve()), "-I", "-B", "-c",
            "from pathlib import Path;print(Path(" + repr(str(private)) + ").read_text())"],
            cwd=env.scratch, prompt="", environment=session._environment, timeout_seconds=5)
        assert process.returncode != 0 and "(version 1)" not in process.stdout
        assert "Operation not permitted" in process.stderr


def test_timeout_keeps_prefix_and_allows_next_command(tmp_path):
    env = session_fixture(tmp_path, [command("timeout", "import time;print('prefix',flush=True);time.sleep(20)", timeout=1),
                                    command("next", "print('next')")])
    with env.session as session:
        response = session.invoke("timeout")
        assert not response["process_output_complete"] and response["failure"]["stop_reason"] == "timeout"
        assert response["stdout"] == "prefix\n"
        assert session.invoke("next")["returncode"] == 0
        session.validate_completion()


def test_resource_close_cancels_inflight_and_prevents_new_process(tmp_path):
    env = session_fixture(tmp_path, [command("long", "import time;print('started',flush=True);time.sleep(20)", timeout=30)])
    with env.session as session, ThreadPoolExecutor() as pool:
        pending = pool.submit(session.invoke, "long")
        deadline = time.monotonic() + 4
        while env.host.launches == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        assert env.host.launches == 1
        env.host.close()
        result = pending.result(timeout=5)
        assert result["failure"]["stop_reason"] == "resource_invalid"
        assert result["returncode"] is not None and result["returncode"] != 0
        session.invoke("long")
        assert env.host.launches == 1
        with pytest.raises(SelfTestResourceUnavailableError):
            session.validate_completion()


def test_session_close_before_process_creation_prevents_launch(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_local_command_execution as implementation
    env = session_fixture(tmp_path, [command("ok", "print('must not run')")])
    session = env.session
    checking, proceed = threading.Event(), threading.Event()
    original = session._verify_materials
    checks, launches = 0, []
    def verify():
        nonlocal checks
        original()
        checks += 1
        if checks == 2:
            checking.set()
            assert proceed.wait(3)
    def process(**fields):
        def create():
            launches.append(session._closed.is_set())
            return SimpleNamespace(returncode=0, stdout="", stderr="", stdout_bytes=b"", stderr_bytes=b"")
        return fields["launch_guard"](create)
    monkeypatch.setattr(session, "_verify_materials", verify)
    monkeypatch.setattr(implementation, "run_cli_process", process)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            invoking = pool.submit(session.invoke, "ok")
            assert checking.wait(3)
            closing = pool.submit(session.close)
            try:
                assert session._closed.wait(3)
            finally:
                proceed.set()
            response = invoking.result(timeout=5)
            closing.result(timeout=5)
        assert launches == [] and env.host.launches == 0
        assert response["allowed"] is False and response["returncode"] is None
        with pytest.raises(SelfTestResourceUnavailableError):
            session.validate_completion()
    finally:
        proceed.set()
        session.close()


def test_session_close_orders_after_creation_without_locking_command_runtime(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_local_command_execution as implementation
    env = session_fixture(tmp_path, [command("ok", "print('process double')")])
    session = env.session
    creating, permit_creation, finish, closing_started = (threading.Event() for _ in range(4))
    closed_at_create = []
    def process(**fields):
        def create():
            creating.set()
            assert permit_creation.wait(3)
            closed_at_create.append(session._closed.is_set())
            return SimpleNamespace(returncode=0, stdout="", stderr="", stdout_bytes=b"", stderr_bytes=b"")
        result = fields["launch_guard"](create)
        assert finish.wait(3)
        return result
    def close():
        closing_started.set()
        session.close()
    monkeypatch.setattr(implementation, "run_cli_process", process)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            invoking = pool.submit(session.invoke, "ok")
            assert creating.wait(3)
            closing = pool.submit(close)
            try:
                assert closing_started.wait(3)
                assert not session._closed.wait(0.05)
                permit_creation.set()
                # Closing is permitted as soon as Popen returns, before the
                # command finishes; the resource lock never covers its runtime.
                assert session._closed.wait(3)
            finally:
                permit_creation.set()
                finish.set()
            response = invoking.result(timeout=5)
            closing.result(timeout=5)
        assert closed_at_create == [False] and env.host.launches == 1
        assert response["failure"]["stop_reason"] == "resource_invalid"
    finally:
        permit_creation.set()
        finish.set()
        session.close()


def test_material_change_is_not_an_ordinary_command_error(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        target = env.materials / "source/module.py"
        target.chmod(0o644)
        target.write_bytes(b"different")
        response = session.invoke("ok")
        assert response["returncode"] is None and env.host.launches == 0
        with pytest.raises(ValueError, match="material"):
            session.validate_completion()


def test_parallel_repeated_commands_have_distinct_local_call_ids(tmp_path):
    env = session_fixture(tmp_path, [command("same", "print('same')")])
    with env.session as session, ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(session.invoke, ["same"] * 3))
        assert len({row["local_call_id"] for row in results}) == 3
        assert env.host.launches == 3 and all(row["returncode"] == 0 for row in results)
        assert {row["tool_call_id"] for row in session.records} == {row["local_call_id"] for row in results}
        session.validate_completion()


def test_private_protocol_rejects_arbitrary_parameters(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        endpoint = Path(session.mcp_config["mcpServers"]["runtime_commands"]["args"][-1])
        tools = exchange(endpoint, {"method": "definitions"})
        assert [tool["name"] for tool in tools] == ["sandbox_command_execute"]
        with pytest.raises(RuntimeError, match="only"):
            exchange(endpoint, {"method": "invoke", "command_id": "ok", "argv": ["unapproved"]})
        with pytest.raises(RuntimeError):
            exchange(endpoint, {"method": "read", "path": "/etc/passwd"})
        assert env.host.launches == 0
        response = exchange(endpoint, {"method": "invoke", "command_id": "ok"})
        assert response["local_call_id"] == session.records[0]["tool_call_id"]


def test_missing_mcp_extra_does_not_launch_a_command(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_local_command_execution as implementation
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        find = implementation.importlib.util.find_spec
        monkeypatch.setattr(implementation.importlib.util, "find_spec", lambda name: None if name == "mcp" else find(name))
        with pytest.raises(ImportError, match="cli_tools"):
            LocalCommandSession(request=session._request, profile=session._profile, host=env.host, adapter=session._adapter,
                artifact_host=session._artifacts, workspace_root=env.workspace, resources_body=env.body,
                source_root=env.materials / "source", scratch_root=env.scratch, read_only_dependencies=env.dependencies)
        assert env.host.launches == 0


def test_unrecordable_result_keeps_local_id_and_invalidates_completion(tmp_path):
    class FailedRecords(list):
        def append(self, _):
            raise OSError("record storage unavailable")
    env = session_fixture(tmp_path, [command("ok", "print('actual result')")])
    with env.session as session:
        session._records = FailedRecords()
        response = session.invoke("ok")
        assert response["local_call_id"] == "local_command_1" and response["stdout"] == "actual result\n"
        assert response["failure"]["stop_reason"] == "record_failure"
        with pytest.raises(OSError, match="record storage"):
            session.validate_completion()


@pytest.mark.parametrize("corruption", ["body", "metadata", "readback"])
def test_session_requires_the_exact_live_resource_content(tmp_path, corruption):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as original:
        fields = dict(request=original._request, profile=original._profile, host=env.host, adapter=original._adapter, artifact_host=original._artifacts,
            workspace_root=env.workspace, resources_body=env.body, source_root=env.materials / "source",
            scratch_root=env.scratch, read_only_dependencies=env.dependencies)
        if corruption == "body":
            document = json.loads(env.body)
            document["commands"][0]["argv"][-1] = "print('unapproved')"
            fields["resources_body"] = json.dumps(document).encode()
        elif corruption == "metadata":
            original._request.authorized_inputs[0].schema_sha256 = "0" * 64
        else:
            env.host.body = b"different readback"
        with pytest.raises(SelfTestResourceUnavailableError, match="exact authorized"):
            LocalCommandSession(**fields)
        assert env.host.launches == 0


def test_same_resource_bytes_do_not_authorize_a_narrower_profile(tmp_path):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        profile = type(session._profile).build(**{**session._profile._payload(), "tool_policy": ("read",),
                                                  "gateway_access_reasons": ()})
        session._request.execution_profile_sha256 = profile.release_sha256
        with pytest.raises(ValueError, match="require shell"):
            LocalCommandSession(request=session._request, profile=profile, host=env.host, adapter=session._adapter,
                artifact_host=session._artifacts, workspace_root=env.workspace, resources_body=env.body,
                source_root=env.materials / "source", scratch_root=env.scratch, read_only_dependencies=env.dependencies)
        assert env.host.launches == 0


@pytest.mark.parametrize("raw", [b'{"method":"definitions","method":"invoke","command_id":"ok"}\n',
                                 b'{"method":"invoke","command_id":NaN}\n', b"x" * 65537 + b"\n"])
def test_private_protocol_rejects_noncanonical_or_oversized_messages(tmp_path, raw):
    env = session_fixture(tmp_path, [command("ok", "print('ok')")])
    with env.session as session:
        endpoint = session.mcp_config["mcpServers"]["runtime_commands"]["args"][-1]
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(endpoint)
            client.sendall(raw)
            with client.makefile("rb") as response:
                assert "error" in json.loads(response.readline())
        assert env.host.launches == 0


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_public_ordinary_module_uses_real_local_command_resources(tmp_path, monkeypatch, cleanup_failure):
    from agent_runtime import Module, RuntimeModulePlugin, register_runtime_module_plugin
    from agent_runtime.registry import RuntimeReleaseRegistry
    from agent_runtime.execution import execution_local_invocation as local, execution_module_invocation as kernel
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID
    from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result, _tool_use, _tool_result
    if cleanup_failure:
        from agent_runtime.invocation import invocation_process_execution as capture
        original_stop = capture._stop_process_group
        def report_cleanup_failure(process):
            original_stop(process)  # No actual process leak in this fault test.
            raise RuntimeError("injected cleanup confirmation failure")
        monkeypatch.setattr(capture, "_stop_process_group", report_cleanup_failure)
    source = _task_project(tmp_path / "registration", module_id="summarize_note")
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
        execution_requirements=_requirements(timeout_seconds=30, max_attempts=1))
    exported = module.to_workflow(module.export(module_version="v1")).export()
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin("local_command_check", "v1", exported.origin_bundle), root=root)
    tree = tmp_path / "candidate"
    tree.mkdir()
    original = b"value = 13\n"
    (tree / "candidate.py").write_bytes(original)
    seen = []
    def provider(**fields):
        fields["launch_guard"](lambda: None)
        seen.append(fields)
        args = fields["argv"]
        assert "--safe-mode" not in args and args[args.index("--tools") + 1] == "Read,Grep,Bash"
        config = json.loads(args[args.index("--mcp-config") + 1])
        endpoint = Path(config["mcpServers"]["runtime_commands"]["args"][-1])
        files = fields["cwd"].parent / "materials"
        assert {path.name for path in files.iterdir()} == {"task_input", "source"}
        assert (files / "source/candidate.py").read_bytes() == original
        assert "content_base64" not in fields["prompt"] and str(tree) not in fields["prompt"]
        initial = _init()
        initial["tools"].append(LOCAL_COMMAND_CLI_TOOL_NAME)
        initial["mcp_servers"] = [{"name": "runtime_commands", "status": "connected"}]
        events = [initial, _tool_use("provider_command", LOCAL_COMMAND_CLI_TOOL_NAME, command_id="unit")]
        for event in events:
            assert fields["on_stdout_line"](json.dumps(event))
        response = exchange(endpoint, {"method": "invoke", "command_id": "unit"})
        assert response["returncode"] == 0 and response["stdout"] == "real command\n"
        completion = [_tool_result("provider_command", failed=cleanup_failure, content=json.dumps(response)),
                      _result(structured_output={"summary": "checked"})]
        for event in completion:
            assert fields["on_stdout_line"](json.dumps(event))
        raw = "\n".join(json.dumps(event) for event in [*events, *completion]).encode()
        result = subprocess.CompletedProcess(args, 0, raw.decode(), "")
        result.stdout_bytes, result.stderr_bytes = raw, b""
        return result
    original_adapter = claude.ClaudeAdapter
    class TestAdapter(original_adapter):
        def __init__(self, **fields):
            super().__init__(**fields, process_runner=provider)
    # build_command intentionally accepts the exact production LocalCommandSession;
    # the Adapter itself is an ordinary test specialization of its existing port.
    monkeypatch.setattr(claude, "ClaudeAdapter", TestAdapter)
    monkeypatch.setattr(kernel, "_authorize_model_attempt", lambda **_: pytest.fail("No Product authority for this self-test"))
    record = evaluate_local_workflow_module(root, "summarize_note", input_payload={}, cli_path=_fake_cli(tmp_path),
        material_root=tree, material_files=({"relative_path": "candidate.py", "sha256": hashlib.sha256(original).hexdigest(), "executable": False},),
        read_only_dependencies=(Path(sys.base_prefix).resolve(),), commands=(command("unit", "print('real command')"),))
    assert record["status"] == ("failed" if cleanup_failure else "completed"), record["failure_detail"]
    assert record["output"] == (None if cleanup_failure else {"summary": "checked"})
    assert record["managed_runtime"] is True
    assert record["persistence"] == "not_requested" and len(seen) == 1
    trace = record["provider_trace"]
    # Provider and command are different processes. A command cleanup error
    # must not replace the already-captured Provider stream with command bytes.
    provider_events = [json.loads(line) for line in trace["stdout"].splitlines()]
    assert provider_events[-1]["structured_output"] == {"summary": "checked"}
    assert trace["process_output_complete"] and trace["exit_code"] == 0
    assert base64.b64decode(trace["raw_streams"]["stdout"]["data"]).decode() == trace["stdout"]
    assert hashlib.sha256(seen[0]["prompt"].encode()).hexdigest() == trace["prompt_envelope_sha256"]
    assert trace["local_command_calls"][0]["response"]["stdout"] == "real command\n"
    assert trace["local_command_calls"][0]["response"]["local_call_id"] == "local_command_1"
    if cleanup_failure:
        response = trace["local_command_calls"][0]["response"]
        assert response["failure"]["cleanup_error"] == "injected cleanup confirmation failure"
        assert response["returncode"] == 0 and not response["process_output_complete"]
        assert record["execution_log"]["tool_calls"][0]["response"] == response
    assert not Path(trace["cwd"]).exists()
    assert (tree / "candidate.py").read_bytes() == original


def test_real_stdio_mcp_uses_exact_proxy_and_preserves_call_identity(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = session_fixture(tmp_path, [command("ok", "print('mcp actual')")])
    with env.session as session:
        config = session.mcp_config["mcpServers"]["runtime_commands"]
        assert Path(config["args"][2]).resolve() == Path(__file__).resolve().parents[1] / "src/agent_runtime/invocation/invocation_local_command_mcp.py"
        assert config["timeout"] >= 20000
        async def run():
            parameters = StdioServerParameters(command=config["command"], args=config["args"], env={"PATH": os.environ["PATH"]})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                listing = await client.list_tools()
                assert [tool.name for tool in listing.tools] == ["sandbox_command_execute"]
                return await client.call_tool("sandbox_command_execute", {"command_id": "ok"})
        result = asyncio.run(run())
        assert result.isError is False
        assert result.structuredContent["local_call_id"] == session.records[0]["tool_call_id"]
        assert result.structuredContent["stdout"] == "mcp actual\n"
        assert json.loads(result.content[0].text) == result.structuredContent
        assert env.host.launches == 1
