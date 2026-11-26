"""agent-runtime-setup against fake native CLIs: program choice, config preservation, MCP selection, status."""
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import sys
import zipfile

import pytest

from agent_runtime import load_runtime_config
from agent_runtime.testing.conformance_host_setup import main


# Answers by argv; every call is appended to <name>.calls.jsonl beside the program.
_FAKE_CLI = """
import json, sys
from pathlib import Path
here = Path(sys.argv[0])
with open(here.with_name(here.stem + ".calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
answers = json.loads(here.with_name(here.stem + ".answers.json").read_text(encoding="utf-8"))
reply = answers.get(" ".join(sys.argv[1:]), {"code": 2})
sys.stdout.write(reply.get("stdout", ""))
sys.exit(reply.get("code", 0))
"""


def _fake_cli(folder: Path, name: str, answers: dict, *, exe: bool = True) -> Path:
    """A real native program (Windows: pip's distlib launcher running a zipped script) with fixed answers."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.answers.json").write_text(json.dumps(answers), encoding="utf-8")
    if os.name != "nt":
        path = folder / (name + (".exe" if exe else ""))
        path.write_text(f"#!{sys.executable}\n{_FAKE_CLI}", encoding="utf-8")
        path.chmod(0o755)
        return path
    distlib = importlib.util.find_spec("pip._vendor.distlib")
    template = Path(distlib.origin).parent / ("t64-arm.exe" if platform.machine().lower() == "arm64" else "t64.exe")
    if not template.is_file():
        pytest.skip("pip's native launcher template is unavailable for a fake native CLI")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("__main__.py", _FAKE_CLI)
    path = folder / f"{name}.exe"
    path.write_bytes(template.read_bytes() + b'#!"' + sys.executable.encode() + b'"\n' + archive.getvalue())
    return path


def _calls(program: Path) -> list[list[str]]:
    log = program.with_name(program.stem + ".calls.jsonl")
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


_CLAUDE_READY = {"--version": {"stdout": "2.1.330 (Claude Code)\n"},
                 "auth status --json": {"stdout": json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                              "email": "person@example.invalid"})}}
_CODEX_READY = {"--version": {"stdout": "codex-cli 0.166.0\n"}, "login status": {"code": 0}}


def _setup(capsys, *arguments) -> tuple[int, dict]:
    code = main([*map(str, arguments)])
    return code, json.loads(capsys.readouterr().out)


def _config(root: Path) -> dict:
    return json.loads((root / ".runtime" / "config.json").read_text(encoding="utf-8"))


@pytest.mark.fake_run
def test_fresh_project_records_the_npm_package_program_and_repeat_writes_nothing(tmp_path, monkeypatch, capsys):
    npm = tmp_path / "npm"
    npm.mkdir()
    (npm / "claude.cmd").write_text("@exit /b 9\r\n")  # The launcher marks the folder; it is never started.
    program = _fake_cli(npm / "node_modules/@anthropic-ai/claude-code/bin", "claude", _CLAUDE_READY)
    monkeypatch.setenv("PATH", str(npm))
    root = tmp_path / "fresh project"

    code, report = _setup(capsys, "--root", root, "--provider", "claude")

    assert code == 0 and report["status"] == "prepared" and report["missing"] == []
    assert report["providers"]["claude_cli"] == {"program": str(program), "source": "npm_package", "started": True,
        "version": "2.1.330 (Claude Code)", "login": "logged_in", "auth_method": "claude.ai"}
    assert "person@example.invalid" not in json.dumps(report)
    assert _config(root) == {"provider_cli_paths": {"claude_cli": str(program)}}
    for host in (".agents", ".claude"):
        for skill in ("agent-runtime-usage", "agent-runtime-registration", "agent-runtime-test-run"):
            assert (root / host / "skills" / skill / "SKILL.md").is_file()
    assert any("conformance_pattern_examples" in step and str(program) in step for step in report["next_steps"])
    before = (root / ".runtime" / "config.json").read_bytes()

    code, again = _setup(capsys, "--root", root, "--provider", "claude")

    assert code == 0 and again["changed_paths"] == []
    assert (root / ".runtime" / "config.json").read_bytes() == before
    assert sorted(map(tuple, _calls(program))) == sorted([("--version",), ("auth", "status", "--json")] * 2)


@pytest.mark.fake_run
def test_configured_program_wins_until_an_explicit_path_replaces_it(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    configured = _fake_cli(root / "tools", "claude", _CLAUDE_READY)
    on_path = _fake_cli(tmp_path / "bin", "claude", _CLAUDE_READY)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    document = {"provider_cli_paths": {"claude_cli": "tools/" + configured.name},
                "read_only_dependencies": ["library"], "attempt_workspace_root": "attempts",
                "mcp_servers": {"codex_cli": {"other": {"config": {"url": "https://other.invalid/mcp"}}}},
                "release_stores": {"main": {"database_url_env": "DSN", "schema": "runtime"}}}
    (root / ".runtime").mkdir(parents=True)
    body = json.dumps(document).encode()
    (root / ".runtime" / "config.json").write_bytes(body)

    code, report = _setup(capsys, "--root", root, "--provider", "claude")
    assert code == 0 and report["providers"]["claude_cli"]["source"] == "configured"
    assert (root / ".runtime" / "config.json").read_bytes() == body  # Nothing selected changed.

    configured.unlink()
    code, report = _setup(capsys, "--root", root, "--provider", "claude")
    assert code == 1 and report["status"] == "incomplete"
    assert report["providers"]["claude_cli"]["started"] is False
    assert any("configured provider_cli_paths" in item and "kept" in item for item in report["missing"])
    assert (root / ".runtime" / "config.json").read_bytes() == body  # Not silently replaced by PATH.
    assert _calls(on_path) == []

    code, report = _setup(capsys, "--root", root, "--provider", "claude", "--claude-path", on_path)
    assert code == 0 and report["providers"]["claude_cli"]["source"] == "explicit"
    assert _config(root) == {**document, "provider_cli_paths": {"claude_cli": str(on_path)}}
    assert load_runtime_config(root)["attempt_workspace_root"] == root.resolve() / "attempts"


@pytest.mark.fake_run
def test_missing_login_is_reported_and_setup_never_starts_a_login(tmp_path, monkeypatch, capsys):
    claude = _fake_cli(tmp_path / "bin", "claude", {**_CLAUDE_READY,
                       "auth status --json": {"code": 1, "stdout": json.dumps({"loggedIn": False})}})
    codex = _fake_cli(tmp_path / "bin", "codex", {**_CODEX_READY, "login status": {"code": 1,
                      "stdout": "Not logged in\n"}})
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    code, report = _setup(capsys, "--root", tmp_path / "project", "--provider", "both")

    assert code == 1 and report["status"] == "incomplete"
    assert {name: value["login"] for name, value in report["providers"].items()} == {
        "claude_cli": "not_logged_in", "codex_cli": "not_logged_in"}
    assert sum("signs in natively" in item for item in report["missing"]) == 2
    assert any(step.startswith("Human: sign in to codex_cli") for step in report["next_steps"])
    assert _calls(claude) == [["--version"], ["auth", "status", "--json"]]
    assert _calls(codex) == [["--version"], ["login", "status"]]
    assert _config(tmp_path / "project")["provider_cli_paths"] == {"claude_cli": str(claude), "codex_cli": str(codex)}


@pytest.mark.fake_run
def test_codex_native_selection_keeps_project_choices_and_refuses_literal_settings(tmp_path, monkeypatch, capsys):
    listing = {"name": "data", "enabled": True, "disabled_reason": None, "enabled_tools": None,
               "disabled_tools": None, "startup_timeout_sec": None, "tool_timeout_sec": None,
               "transport": {"type": "streamable_http", "url": "https://data.invalid/mcp/v2",
                             "bearer_token_env_var": None, "http_headers": None, "env_http_headers": None,
                             "http_headers_helper": None}}
    secret = {**listing, "name": "secret", "transport": {**listing["transport"],
              "http_headers": {"Authorization": "Bearer LITERAL-SECRET-VALUE"}}}
    plain = {**listing, "name": "plain"}
    codex = _fake_cli(tmp_path / "bin", "codex", {**_CODEX_READY,
        "mcp get data --json": {"stdout": json.dumps(listing)},
        "mcp get secret --json": {"stdout": json.dumps(secret)},
        "mcp get plain --json": {"stdout": json.dumps(plain)},
        "mcp get absent --json": {"code": 1, "stdout": ""}})
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    root = tmp_path / "project"
    (root / ".runtime").mkdir(parents=True)
    chosen = {"config": {"url": "https://data.invalid/mcp", "enabled_tools": ["query"],
                         "default_tools_approval_mode": "approve"}, "environment_keys": []}
    secret_before = {"config": {"url": "https://secret.invalid/mcp"}, "environment_keys": []}
    unselected = {"config": {"command": "server.exe"}, "environment_keys": []}
    (root / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {"codex_cli": {
        "data": chosen, "secret": secret_before, "unselected": unselected}}}))

    code, report = _setup(capsys, "--root", root, "--provider", "codex", "--mcp", "data", "--mcp", "secret",
                          "--mcp", "absent")

    servers = _config(root)["mcp_servers"]["codex_cli"]
    assert servers["data"]["config"] == {"url": "https://data.invalid/mcp/v2", "enabled_tools": ["query"],
                                         "default_tools_approval_mode": "approve"}
    assert servers["secret"] == secret_before and servers["unselected"] == unselected
    assert "absent" not in servers
    connections = report["mcp_servers"]["codex_cli"]
    assert connections["data"] == {"action": "imported", "approval": "approve"}
    assert connections["secret"]["action"] == "refused" and "transport.http_headers" in connections["secret"]["detail"]
    assert connections["absent"]["action"] == "missing"
    assert "LITERAL-SECRET-VALUE" not in json.dumps(report) and code == 1
    assert ["mcp", "get", "unselected", "--json"] not in _calls(codex)

    code, report = _setup(capsys, "--root", root, "--provider", "codex", "--mcp", "plain")

    # Absent approval is informational: nothing is missing and approve is never added.
    assert code == 0 and report["missing"] == []
    assert report["mcp_servers"]["codex_cli"]["plain"]["approval"] == "not_set_in_project_entry"
    assert "default_tools_approval_mode" not in _config(root)["mcp_servers"]["codex_cli"]["plain"]["config"]
    assert any("setup never sets it" in step for step in report["next_steps"])


@pytest.mark.fake_run
def test_mcp_config_file_is_the_only_source_for_selected_entries(tmp_path, monkeypatch, capsys):
    claude = _fake_cli(tmp_path / "bin", "claude", _CLAUDE_READY)
    codex = _fake_cli(tmp_path / "bin", "codex", _CODEX_READY)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    source = tmp_path / "host" / ".runtime" / "config.json"
    source.parent.mkdir(parents=True)
    codex_entry = {"config": {"url": "https://data.invalid/mcp", "enabled_tools": ["query"],
                              "default_tools_approval_mode": "approve"}, "environment_keys": []}
    paused = {"config": {"url": "https://paused.invalid/mcp", "enabled": False}, "environment_keys": []}
    source.write_text(json.dumps({"mcp_servers": {
        "claude_cli": {"data": {"config": {"type": "http", "url": "https://data.invalid/mcp",
                                           "headers": {"Authorization": "Bearer ${DATA_TOKEN}"}}},
                       "other": {"config": {"type": "http", "url": "https://other.invalid/mcp"}}},
        "codex_cli": {"data": codex_entry, "paused": paused}}}))
    root = tmp_path / "project"

    code, report = _setup(capsys, "--root", root, "--provider", "both", "--mcp", "data", "--mcp", "paused",
                          "--mcp-config", source)

    servers = _config(root)["mcp_servers"]
    assert servers["claude_cli"] == {"data": {"config": {"type": "http", "url": "https://data.invalid/mcp",
        "headers": {"Authorization": "Bearer ${DATA_TOKEN}"}}, "environment_keys": ["DATA_TOKEN"]}}
    assert servers["codex_cli"] == {"data": codex_entry, "paused": paused}
    assert report["mcp_servers"]["claude_cli"]["paused"]["action"] == "missing"  # No fallback to another source.
    assert report["mcp_servers"]["codex_cli"]["paused"]["disabled"] is True
    assert code == 1 and any("paused" in item and "disabled" in item for item in report["missing"])
    assert not any(call[:1] == ["mcp"] for call in _calls(codex) + _calls(claude))

    native = tmp_path / "claude-native.json"
    native.write_text(json.dumps({"mcpServers": {"vault": {"type": "stdio", "command": "vault-mcp",
                                                           "env": {"VAULT_TOKEN": "LITERAL-SECRET-VALUE"}}}}))
    code, report = _setup(capsys, "--root", root, "--provider", "claude", "--mcp", "vault", "--mcp-config", native)

    assert code == 1 and report["mcp_servers"]["claude_cli"]["vault"]["action"] == "refused"
    assert "env.VAULT_TOKEN" in report["mcp_servers"]["claude_cli"]["vault"]["detail"]
    assert "LITERAL-SECRET-VALUE" not in json.dumps(report)
    assert "vault" not in _config(root)["mcp_servers"]["claude_cli"]


@pytest.mark.fake_run
def test_local_skill_edit_stops_setup_with_the_existing_conflict(tmp_path, monkeypatch, capsys):
    _fake_cli(tmp_path / "bin", "claude", _CLAUDE_READY)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    edited = tmp_path / "project" / ".claude" / "skills" / "agent-runtime-usage" / "SKILL.md"
    edited.parent.mkdir(parents=True)
    edited.write_text("local notes\n")

    code, report = _setup(capsys, "--root", tmp_path / "project", "--provider", "claude")

    assert code == 1 and report["status"] == "failed" and report["changed_paths"] == []
    assert "local Skill content" in report["error"]["detail"]
    assert edited.read_text() == "local notes\n"
    assert not (tmp_path / "project" / ".runtime" / "config.json").exists()


@pytest.mark.deterministic
def test_usage_errors_exit_before_setup(tmp_path, capsys):
    for arguments in (["--root", str(tmp_path), "--provider", "claude", "--codex-path", "codex.exe"],
                      ["--root", str(tmp_path), "--provider", "codex", "--mcp", "Bad.Name"],
                      ["--root", str(tmp_path), "--provider", "codex", "--mcp-config", str(tmp_path / "absent.json"),
                       "--mcp", "data"]):
        with pytest.raises(SystemExit) as exit_info:
            main(arguments)
        assert exit_info.value.code == 2
    assert list(tmp_path.iterdir()) == []
