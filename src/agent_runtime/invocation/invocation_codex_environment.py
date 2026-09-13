"""Prepare one CLI-owned private state; never interpret or copy credentials."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator, Mapping


_ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})


class CodexEnvironmentCleanupError(RuntimeError):
    """Private state was retained; its locator belongs in private diagnostics."""

    def __init__(self, state: Path) -> None:
        self.recovery_path = state
        super().__init__(f"Codex private state retained at {state}; inspect it before authentication reuse")


@contextmanager
def prepare_codex_environment(
    *, workspace_root: Path, auth_file: Path | None = None,
    source_environment: Mapping[str, str],
) -> Iterator[dict[str, str]]:
    """Yield a fresh child environment backed by one file authentication source.

    An explicit auth_file wins; otherwise resolve CODEX_HOME/auth.json, or the
    user's standard .codex/auth.json when CODEX_HOME is unset. No alternate
    source, keychain, login or token parsing is attempted. State must be outside
    the caller-owned workspace cleanup tree before mounting the reference.

    Normal exit removes only the owned link and state. A replaced auth entry is
    retained in its private directory, even on timeout/cancellation. The caller
    must keep this context inside Adapter cleanup and outside workspace cleanup.
    Retention is local temporary recovery, not a durable credential backup.
    """
    if auth_file is None:
        provider_root = source_environment.get("CODEX_HOME")
        if provider_root is not None:
            if not provider_root.strip():
                raise ValueError("CODEX_HOME must name the selected credential directory")
            auth_file = Path(provider_root) / "auth.json"
        else:
            home = source_environment.get("HOME")
            auth_file = (Path(home) if home else Path.home()) / ".codex" / "auth.json"
    source = Path(auth_file).expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError("Codex file credential source is unavailable; provide one regular auth_file")

    state = Path(tempfile.mkdtemp(prefix="agent-runtime-codex-state-")).resolve()
    if state.is_relative_to(Path(workspace_root).resolve()):
        state.rmdir()  # No credential reference has been installed yet.
        raise ValueError("Codex private state must be outside the workspace cleanup tree")
    reference = state / "auth.json"
    mounted = False
    try:
        reference.symlink_to(source)
        mounted = True
        environment = {key: value for key, value in source_environment.items() if key in _ENVIRONMENT_KEYS}
        environment["CODEX_HOME"] = str(state)
        yield environment
    finally:
        try:
            if mounted:
                if reference.is_symlink() and reference.readlink() == source:
                    reference.unlink()
                elif os.path.lexists(reference):
                    # Do not let an enclosing cleanup erase a refreshed credential.
                    raise CodexEnvironmentCleanupError(state)
            shutil.rmtree(state)
        except OSError as exc:
            raise CodexEnvironmentCleanupError(state) from exc


__all__: list[str] = []
