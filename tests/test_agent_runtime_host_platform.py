from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_runtime.invocation import invocation_host_platform_selection as selection
from agent_runtime.invocation.invocation_host_platform_selection import (
    HOST_PLATFORM,
    UnsupportedHostError,
    UnsupportedHostPlatform,
    select_host_platform,
)


_WINDOWS_MODULE = "agent_runtime.invocation.invocation_windows_host_operation"
_POSIX_MODULE = "agent_runtime.invocation.invocation_posix_host_operation"
_LEASE_MODULE = (
    Path(__file__).resolve().parents[1]
    / "src/agent_runtime/invocation/invocation_workspace_preparation.py"
)


class _Implementation:
    def acquire_lease_lock(self, handle) -> None:
        pass

    def release_lease_lock(self, handle) -> None:
        pass


@pytest.mark.deterministic
@pytest.mark.parametrize(
    ("platform", "expected_module"),
    [("win32", _WINDOWS_MODULE), ("linux", _POSIX_MODULE), ("darwin", _POSIX_MODULE)],
)
def test_selection_maps_windows_and_other_hosts(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected_module: str,
) -> None:
    # The other host's module cannot import here, so the returned type cannot
    # show the mapping; record the module name selection asks for instead.
    requested: list[str] = []

    def record(name: str):
        requested.append(name)
        return SimpleNamespace(HOST_PLATFORM_IMPLEMENTATION=_Implementation)

    monkeypatch.setattr(selection.importlib, "import_module", record)

    chosen = select_host_platform(platform)

    assert requested == [expected_module]
    assert isinstance(chosen, _Implementation)


@pytest.mark.deterministic
def test_selection_returns_unsupported_platform_when_module_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str):
        raise ModuleNotFoundError(f"simulated missing host module: {name}")

    monkeypatch.setattr(selection.importlib, "import_module", unavailable)

    assert isinstance(select_host_platform("win32"), UnsupportedHostPlatform)
    assert isinstance(select_host_platform("linux"), UnsupportedHostPlatform)


@pytest.mark.deterministic
def test_unsupported_platform_refuses_both_operations() -> None:
    unsupported = UnsupportedHostPlatform()

    with pytest.raises(UnsupportedHostError, match="POSIX host"):
        unsupported.acquire_lease_lock(object())
    with pytest.raises(UnsupportedHostError, match="POSIX host"):
        unsupported.release_lease_lock(object())


@pytest.mark.deterministic
def test_current_host_selects_its_native_implementation() -> None:
    if sys.platform == "win32":
        from agent_runtime.invocation.invocation_windows_host_operation import (
            WindowsHostPlatform as native,
        )
    else:
        from agent_runtime.invocation.invocation_posix_host_operation import (
            PosixHostPlatform as native,
        )

    assert type(HOST_PLATFORM) is native


@pytest.mark.deterministic
def test_lease_module_contains_no_platform_dispatch() -> None:
    source = _LEASE_MODULE.read_text(encoding="utf-8")

    for platform_knowledge in ("sys.platform", "os.name", "fcntl", "msvcrt"):
        assert platform_knowledge not in source, platform_knowledge
