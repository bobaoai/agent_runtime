"""Host directory controls use real OS access control and synthetic state."""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import sys

import pytest

from agent_runtime.invocation import invocation_host_platform_selection as selection
import windows_junction_helpers as junction_cases  # Synthetic native junction helpers.


pytestmark = pytest.mark.deterministic
_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="native Windows handle-bound disposal")


def displayed_chain(failure):
    """Follow an exception chain exactly as tracebacks display it."""
    displayed = []
    while failure is not None and len(displayed) < 12:
        displayed.append(failure)
        failure = failure.__cause__ if failure.__cause__ is not None else (
            None if failure.__suppress_context__ else failure.__context__)
    return displayed


def scratch_external(tmp_path):
    """External synthetic canaries, verified to sit inside this test's own fresh scratch root."""
    external = junction_cases.make_external(tmp_path)
    assert external.parent == tmp_path
    return external, junction_cases.folder_bytes(external)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL")
def test_windows_private_dacl_and_child_inheritance(tmp_path):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    directory = selection.HOST_PLATFORM.create_private_directory(prefix="private-", parent=tmp_path)
    try:
        protected, entries = native.inspect_directory(directory)
        expected = {native.current_user_sid(), "S-1-5-18", "S-1-5-32-544"}
        assert protected and {row[0] for row in entries} == expected
        assert all(mask == 0x1F01FF and flags == 3 for _, mask, flags in entries)
        child = directory / "child"
        child.mkdir()
        child_protected, inherited = native.inspect_directory(child)
        assert {row[0] for row in inherited} == expected
        assert all(mask == 0x1F01FF and flags & 0x10 for _, mask, flags in inherited)
        child.rmdir()
    finally:
        directory.rmdir()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL")
@pytest.mark.parametrize("failure", ["assignment", "readback"])
def test_directory_setup_failure_does_not_return_or_keep_ready_state(tmp_path, monkeypatch, failure):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    if failure == "assignment":
        monkeypatch.setattr(native, "_set", lambda *args: 5)
    else:
        monkeypatch.setattr(native, "_security_view", lambda handle: (False, ()))
    with pytest.raises(OSError):
        native.create_private_directory(prefix="refused-", parent=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction")
def test_reparse_target_is_rejected_without_changing_target_acl(tmp_path):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    target = tmp_path / "target"
    target.mkdir()
    before = native.inspect_directory(target)
    link = tmp_path / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    try:
        with pytest.raises(OSError, match="ordinary directory"):
            native.protect_directory(link)
        assert native.inspect_directory(target) == before
    finally:
        link.rmdir()
        target.rmdir()


def test_common_directory_lifecycle_and_error_cleanup(tmp_path):
    for fail_body in (False, True):
        created = None
        try:
            with selection.private_temporary_directory(prefix="lifecycle-", parent=tmp_path) as directory:
                created = directory
                assert directory.parent == tmp_path and directory.is_absolute()
                (directory / "synthetic").write_text("owned")
                (directory / "nested" / "deeper").mkdir(parents=True)
                (directory / "nested" / "record.bin").write_bytes(b"synthetic nested record")
                (directory / "nested" / "deeper" / "log.txt").write_text("synthetic log")
                (directory / "empty").mkdir()
                if fail_body:
                    raise ValueError("synthetic caller failure")
        except ValueError:
            assert fail_body
        assert created is not None and not created.exists()


def test_common_cleanup_preserves_replaced_directory(tmp_path):
    relocated = None
    with pytest.raises(OSError, match="replaced"):
        with selection.private_temporary_directory(prefix="replacement-", parent=tmp_path) as directory:
            relocated = directory.with_name(directory.name + "-original")
            directory.rename(relocated)
            directory.mkdir()
            (directory / "keep").write_text("foreign replacement")
    assert (directory / "keep").read_text() == "foreign replacement"
    (directory / "keep").unlink()
    directory.rmdir()
    relocated.rmdir()


def test_common_context_captures_identity_before_yield_and_delegates_disposal(tmp_path, monkeypatch):
    calls = []

    class _RecordingHost:
        def create_private_directory(self, **kwargs):
            calls.append(("create", kwargs))
            directory = tmp_path / "allocated"
            directory.mkdir()
            return directory

        def private_directory_identity(self, directory):
            calls.append(("identity", directory))
            return ("synthetic identity", directory.name)

        def remove_private_directory(self, directory, identity):
            calls.append(("remove", directory, identity))

    monkeypatch.setattr(selection, "HOST_PLATFORM", _RecordingHost())
    allocated = tmp_path / "allocated"
    for fail_body in (False, True):
        calls.clear()
        try:
            with selection.private_temporary_directory(prefix="delegated-", short_path=True,
                                                       parent=tmp_path) as directory:
                calls.append(("body", directory))
                if fail_body:
                    raise ValueError("synthetic caller failure")
        except ValueError:
            assert fail_body
        assert calls == [("create", {"prefix": "delegated-", "short_path": True, "parent": tmp_path}),
                         ("identity", allocated), ("body", allocated),
                         ("remove", allocated, ("synthetic identity", "allocated"))]
        allocated.rmdir()


def test_identity_capture_failure_yields_nothing_and_retains_the_allocation(tmp_path, monkeypatch):
    def refuse(directory):
        raise OSError("synthetic identity refusal")

    monkeypatch.setattr(selection.HOST_PLATFORM, "private_directory_identity", refuse)
    monkeypatch.setattr(selection.HOST_PLATFORM, "remove_private_directory",
                        lambda *args: pytest.fail("disposal attempted without confirmed ownership"))
    with pytest.raises(OSError, match="identity could not be captured") as refused:
        with selection.private_temporary_directory(prefix="unidentified-", parent=tmp_path):
            pytest.fail("directory yielded without a captured identity")
    retained = list(tmp_path.iterdir())
    assert len(retained) == 1 and list(retained[0].iterdir()) == []
    assert retained[0].name in str(refused.value)
    assert "synthetic identity refusal" in str(refused.value.__cause__)
    retained[0].rmdir()


@pytest.mark.deterministic
def test_unsupported_host_refuses_new_operations():
    unsupported = selection.UnsupportedHostPlatform()
    for call in (lambda: unsupported.create_private_directory(prefix="x"),
                 lambda: unsupported.provider_environment({}, ()),
                 lambda: unsupported.private_directory_identity(None),
                 lambda: unsupported.remove_private_directory(None, None),
                 lambda: unsupported.resolve_cli_program("x", {}),
                 lambda: unsupported.create_cli_process(["x"], cwd=None, environment={}),
                 lambda: unsupported.stop_cli_process(None),
                 lambda: unsupported.read_material_file(None, ()),
                 lambda: unsupported.write_material_tree(None, ()),
                 lambda: unsupported.material_tree_state(None)):
        with pytest.raises(selection.UnsupportedHostError):
            call()
    for removed in ("observe_cli_process", "release_cli_process", "mount_codex_credential",
                    "remove_codex_state", "protected_read_roots", "credential_locations"):
        assert not hasattr(unsupported, removed) and not hasattr(selection.HostPlatform, removed)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows environment")
def test_windows_environment_only_adds_host_startup_keys():
    source = {"systemroot": r"C:\Windows", "USERPROFILE": r"C:\Users\Synthetic",
        "COMSPEC": r"C:\Windows\System32\cmd.exe", "LANG": "en_US.UTF-8",
        "OPENAI_API_KEY": "synthetic-private", "UNRELATED_SECRET": "synthetic-private",
        "CODEX_HOME": r"C:\wrong-provider-state"}
    result = selection.HOST_PLATFORM.provider_environment(source, {"LANG"})
    assert set(result) == {"systemroot", "USERPROFILE", "COMSPEC", "LANG"}
    assert "synthetic-private" not in repr(result)


def test_posix_environment_and_short_directory_contract_without_native_posix(monkeypatch, tmp_path):
    import importlib.util
    from types import SimpleNamespace
    path = Path(selection.__file__).with_name("invocation_posix_host_operation.py")
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("posix_host_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: calls.append(kwargs) or str(tmp_path))
    host = module.PosixHostPlatform()
    assert host.create_private_directory(prefix="short-", short_path=True) == tmp_path.resolve()
    assert calls == [{"prefix": "short-", "dir": "/tmp"}]
    assert host.provider_environment({"HOME":"chosen","SECRET":"excluded"}, {"HOME"}) == {"HOME":"chosen"}


def test_posix_private_directory_disposal_keeps_existing_contract_without_native_posix(monkeypatch, tmp_path):
    """Symlink refusal needs POSIX symlinks; the identity, tree and absent-path rules run on every host."""
    import importlib.util
    from types import SimpleNamespace
    path = Path(selection.__file__).with_name("invocation_posix_host_operation.py")
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("posix_host_directory_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    host = module.PosixHostPlatform()
    allocated, other = tmp_path / "allocated", tmp_path / "other"
    (allocated / "nested").mkdir(parents=True)
    (allocated / "nested" / "synthetic").write_text("owned")
    other.mkdir()
    identity = host.private_directory_identity(allocated)
    with pytest.raises(OSError, match="replaced"):
        host.remove_private_directory(allocated, host.private_directory_identity(other))
    assert (allocated / "nested" / "synthetic").read_text() == "owned"
    host.remove_private_directory(allocated, identity)
    assert not allocated.exists() and other.is_dir()
    assert host.remove_private_directory(allocated, identity) is None  # Existing POSIX no-op.



@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL")
def test_setup_cleanup_failure_retains_only_its_new_empty_directory(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    original = Path.rmdir
    with monkeypatch.context() as patch:
        patch.setattr(native, "_set", lambda *args: 5)
        def refuse_owned(path):
            if path.parent == tmp_path:
                raise PermissionError("synthetic cleanup refusal")
            return original(path)
        patch.setattr(Path, "rmdir", refuse_owned)
        with pytest.raises(OSError, match="retained"):
            native.create_private_directory(prefix="retained-", parent=tmp_path)
    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 1 and list(remaining[0].iterdir()) == []
    remaining[0].rmdir()


@_WINDOWS_ONLY
@pytest.mark.parametrize("attack", ["rename_to_junction", "in_place_reparse"])
def test_root_redirect_after_disposal_validation_never_reaches_external_files(tmp_path, monkeypatch, attack):
    """Redirect the allocated root after cleanup's last validation, whichever disposal runs.

    The hook fires once, right after the held-root validation of handle-bound disposal or,
    for a path-based disposal, right after shutil.rmtree's last link check (the window of
    the native probe in PRIMARY_PUBLIC_CLEANUP_PROBE_20261114_R2.json).
    """
    from agent_runtime.invocation import invocation_windows_host_operation as host
    external, before = scratch_external(tmp_path)
    allocated, observed, fired = [], {}, []

    def redirect():
        fired.append(True)
        directory = allocated[0]
        if attack == "rename_to_junction":
            moved = directory.with_name(directory.name + "-moved")
            try:
                os.rename(directory, moved)
            except PermissionError as refused:
                observed["rename_refused"] = refused.winerror
            else:
                observed["moved"] = moved
                junction_cases.create_junction(directory, external)
        else:
            (directory / "synthetic.bin").unlink()  # A mount point needs an empty directory.
            junction_cases.mount_in_place(directory, external)
            observed["converted"] = directory.is_junction()

    original_remove = host.remove_held_directory

    def after_held_root_validation(handle, identity, **options):
        if not fired:
            redirect()
        return original_remove(handle, identity, **options)

    monkeypatch.setattr(host, "remove_held_directory", after_held_root_validation)
    original_check = getattr(shutil, "_rmtree_islink", None)
    if original_check is not None:
        def after_last_path_check(path):
            result = original_check(path)
            if not fired and allocated and Path(path) == allocated[0]:
                redirect()
            return result
        monkeypatch.setattr(shutil, "_rmtree_islink", after_last_path_check)
    reported = None
    try:
        try:
            with selection.private_temporary_directory(prefix="redirect-", parent=tmp_path) as directory:
                assert directory.parent == tmp_path
                allocated.append(directory)
                (directory / "synthetic.bin").write_bytes(b"\x00owned synthetic state\xff")
        except OSError as error:
            reported = error
        assert fired == [True]
        assert junction_cases.folder_bytes(external) == before
        if attack == "rename_to_junction":
            # The held root refuses rename, so disposal of the owned allocation completes.
            assert observed == {"rename_refused": 32} and reported is None
            assert not os.path.lexists(directory)
        else:
            assert observed == {"converted": True} and directory.is_junction()
            assert reported is not None and directory.name in str(reported)
    finally:
        if allocated and allocated[0].is_junction():
            junction_cases.remove_test_junction(allocated[0])
        moved = observed.get("moved")
        if moved is not None and moved.exists():
            assert moved.parent == tmp_path
            shutil.rmtree(moved)
    assert junction_cases.folder_bytes(external) == before


@_WINDOWS_ONLY
@pytest.mark.parametrize("attack", ["rename_to_junction", "in_place_reparse"])
def test_nested_redirect_during_disposal_is_retained_without_following(tmp_path, monkeypatch, attack):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    external, before = scratch_external(tmp_path)
    moved, attacked, allocated = tmp_path / "moved_nested", [], []
    if attack == "rename_to_junction":
        original_open = native.open_child

        def replace_before_open(parent, name, *, directory):
            if name == "nested" and not attacked:
                os.rename(allocated[0] / "nested", moved)
                junction_cases.create_junction(allocated[0] / "nested", external)
                attacked.append(name)
            return original_open(parent, name, directory=directory)
        monkeypatch.setattr(native, "open_child", replace_before_open)
    else:
        original_remove = native._remove_contents

        def convert_held_child(handle, depth, *options):
            if depth == 1 and not attacked:
                (allocated[0] / "nested" / "record.bin").unlink()
                junction_cases.mount_in_place(allocated[0] / "nested", external)
                attacked.append(depth)
            return original_remove(handle, depth, *options)
        monkeypatch.setattr(native, "_remove_contents", convert_held_child)
    try:
        with pytest.raises(OSError) as reported:
            with selection.private_temporary_directory(prefix="nested-redirect-", parent=tmp_path) as directory:
                allocated.append(directory)
                (directory / "nested").mkdir()
                (directory / "nested" / "record.bin").write_bytes(b"synthetic nested record")
        assert attacked and directory.name in str(reported.value)
        assert (directory / "nested").is_junction()
        assert junction_cases.folder_bytes(external) == before
        if attack == "rename_to_junction":
            assert (moved / "record.bin").read_bytes() == b"synthetic nested record"
    finally:
        if allocated and os.path.lexists(allocated[0] / "nested") and (allocated[0] / "nested").is_junction():
            junction_cases.remove_test_junction(allocated[0] / "nested")
        for owned in (*allocated, moved):
            if owned.exists():
                assert owned.parent == tmp_path
                shutil.rmtree(owned)
    assert junction_cases.folder_bytes(external) == before


@_WINDOWS_ONLY
@pytest.mark.parametrize("change", ["in_place_junction", "replacement_junction", "replacement_directory"])
def test_root_changed_before_disposal_is_retained_without_touching_targets(tmp_path, change):
    external, before = scratch_external(tmp_path)
    allocated, moved = [], None
    try:
        with pytest.raises(OSError) as reported:
            with selection.private_temporary_directory(prefix="changed-", parent=tmp_path) as directory:
                allocated.append(directory)
                (directory / "synthetic.bin").write_bytes(b"synthetic owned state")
                if change == "in_place_junction":
                    (directory / "synthetic.bin").unlink()
                    junction_cases.mount_in_place(directory, external)
                else:
                    moved = directory.with_name(directory.name + "-moved")
                    directory.rename(moved)
                    if change == "replacement_junction":
                        junction_cases.create_junction(directory, external)
                    else:
                        directory.mkdir()
                        (directory / "foreign.bin").write_bytes(b"foreign replacement")
        message = str(reported.value)
        assert directory.name in message
        if change == "replacement_directory":
            assert "replaced" in message
            assert (directory / "foreign.bin").read_bytes() == b"foreign replacement"
        else:
            assert "not an ordinary owned entry" in message and directory.is_junction()
        if moved is not None:
            assert (moved / "synthetic.bin").read_bytes() == b"synthetic owned state"
        assert junction_cases.folder_bytes(external) == before
    finally:
        if allocated and allocated[0].is_junction():
            junction_cases.remove_test_junction(allocated[0])
        for owned in (*allocated, *((moved,) if moved is not None else ())):
            if owned.exists():
                assert owned.parent == tmp_path
                shutil.rmtree(owned)
    assert junction_cases.folder_bytes(external) == before


@_WINDOWS_ONLY
@pytest.mark.parametrize("body_fails", [False, True])
@pytest.mark.parametrize("change", ["moved", "deleted"])
def test_root_missing_from_allocated_path_is_reported_and_keeps_body_failure(tmp_path, change, body_fails):
    moved = None
    try:
        with pytest.raises(OSError) as reported:
            with selection.private_temporary_directory(prefix="missing-", parent=tmp_path) as directory:
                (directory / "nested").mkdir()
                (directory / "nested" / "record.bin").write_bytes(b"synthetic record")
                if change == "moved":
                    moved = directory.with_name(directory.name + "-moved")
                    directory.rename(moved)
                else:
                    shutil.rmtree(directory)  # The caller removed its own synthetic allocation.
                if body_fails:
                    raise ValueError("caller failure")
        assert "allocated path" in str(reported.value) and directory.name in str(reported.value)
        assert not os.path.lexists(directory)
        if moved is not None:
            assert (moved / "nested" / "record.bin").read_bytes() == b"synthetic record"
        chain = displayed_chain(reported.value)
        assert any(isinstance(item, ValueError) and str(item) == "caller failure" for item in chain) is body_fails
    finally:
        if moved is not None and moved.exists():
            assert moved.parent == tmp_path
            shutil.rmtree(moved)


@_WINDOWS_ONLY
@pytest.mark.parametrize("entry", ["hard_link", "junction", "readonly"])
def test_unconfirmable_entries_are_retained_without_forcing_or_touching_targets(tmp_path, entry):
    external, before = scratch_external(tmp_path)
    allocated = []
    try:
        with pytest.raises(OSError) as reported:
            with selection.private_temporary_directory(prefix="unsafe-", parent=tmp_path) as directory:
                allocated.append(directory)
                (directory / "nested").mkdir()
                if entry == "hard_link":
                    os.link(external / "sentinel.bin", directory / "nested" / "linked.bin")
                elif entry == "junction":
                    junction_cases.create_junction(directory / "nested" / "linked", external)
                else:
                    (directory / "nested" / "readonly.bin").write_bytes(b"synthetic read-only state")
                    (directory / "nested" / "readonly.bin").chmod(stat.S_IREAD)
        assert directory.name in str(reported.value) and directory.is_dir()
        assert junction_cases.folder_bytes(external) == before
        if entry == "readonly":
            kept = directory / "nested" / "readonly.bin"
            assert kept.read_bytes() == b"synthetic read-only state"
            assert os.stat(kept).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        if allocated and allocated[0].exists():
            nested = allocated[0] / "nested"
            if os.path.lexists(nested / "linked") and (nested / "linked").is_junction():
                junction_cases.remove_test_junction(nested / "linked")
            if os.path.lexists(nested / "linked.bin"):
                os.unlink(nested / "linked.bin")  # Removes only this extra name of a synthetic file.
            if os.path.lexists(nested / "readonly.bin"):
                (nested / "readonly.bin").chmod(stat.S_IREAD | stat.S_IWRITE)
            assert allocated[0].parent == tmp_path
            shutil.rmtree(allocated[0])
    assert junction_cases.folder_bytes(external) == before


@_WINDOWS_ONLY
@pytest.mark.parametrize("body_fails", [False, True])
def test_failed_native_delete_reports_location_retains_remainder_and_keeps_body_failure(
        tmp_path, monkeypatch, body_fails):
    from agent_runtime.invocation import invocation_windows_directory_preparation as native
    original, calls = native.dispose_entry, []

    def refuse_second(handle, **options):
        calls.append(handle)
        if len(calls) == 2:
            raise PermissionError(13, "synthetic delete refusal")
        return original(handle, **options)

    monkeypatch.setattr(native, "dispose_entry", refuse_second)
    allocated = []
    try:
        with pytest.raises(OSError, match="synthetic delete refusal") as reported:
            with selection.private_temporary_directory(prefix="refused-", parent=tmp_path) as directory:
                allocated.append(directory)
                for name in ("first.bin", "second.bin"):
                    (directory / name).write_bytes(b"synthetic " + name.encode())
                if body_fails:
                    raise ValueError("caller failure")
        assert directory.name in str(reported.value) and len(calls) == 2
        # Disposal is not atomic: one verified owned file is gone; the other and the root remain.
        assert directory.is_dir() and len(list(directory.iterdir())) == 1
        chain = displayed_chain(reported.value)
        assert any(isinstance(item, ValueError) and str(item) == "caller failure" for item in chain) is body_fails
    finally:
        if allocated and allocated[0].exists():
            assert allocated[0].parent == tmp_path
            shutil.rmtree(allocated[0])

