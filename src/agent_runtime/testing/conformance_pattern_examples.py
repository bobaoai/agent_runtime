"""Four ordinary Runtime usage patterns as small, directly runnable examples.

    python -m agent_runtime.testing.conformance_pattern_examples run \\
        --pattern file_review --transport claude_cli --root "EXAMPLE_ROOT"

Patterns: structured_output (tool-free structured output), file_review (frozen
task material -> a written and read-back review file), code_change (a tiny
project the model fixes and checks with a bounded command) and mcp_data (a
declared MCP connection -> a saved, read-back data file -> a derived file).
native_project (claude_cli or codex_cli) runs an mcp_data-shaped Module with the
per-call native_project choice (Codex in host mode) inside a generated project
holding CLAUDE.md/AGENTS.md, two skills for each CLI and a Python script; the
check reads the marker the project instructions require, the skill steps and
Claude's own init skill list in the returned log, the fixture audit and the
script's artifact.

Each pattern is one ordinary Module that goes through the same public path a
host uses, with no machine field written by hand: the task is an ordinary v4
source folder (source/.claude/skills/runtime-pattern-examples/runtime_modules/
<module>); register_module reads its identity from that folder, registers it
with module_requirements_preset(<pattern>) and reads it back;
selection_from_registration turns the returned result into the code-written
selection.json; file_review's resources.json comes from prepare_material_files
over the selected material file; run_local_workflow_test runs exactly that
selection (the entry behind agent-runtime-test-run --selection) and the record
is saved with write_test_run_record and read with inspect_test_run_record.
This module adds no executor, tool routing, permission rule, sandbox or
supervision: the selected CLI plans, reads and writes files, runs commands and
calls MCP tools itself, under its own native permissions. After the run, check
compares the actual file effects, the Inspection log returned in the record
and, for MCP, the fixture server's own audit with the task. A model's self-report is never counted as evidence; for
code_change the pass evidence is the host's own rerun and the logged command
result, never a hash or nonce restated by the model.

Every prepare creates a new folder <root>/<pattern>-<cli>-<UTC time>-<hex> that
holds its own task source (source/), Runtime host root (host/), Attempt folders
(attempts/), input, materials or project, so re-running never collides with a
registered definition version and never touches another folder, configuration
or project.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys

from ..registry.registry_module_authoring import MODULE_REQUIREMENT_PRESETS

_MODULE = "agent_runtime.testing.conformance_pattern_examples"
PATTERN_NAMES = MODULE_REQUIREMENT_PRESETS  # One example per requirements preset, with the same IDs.
# The per-call native project execution choice, shown on an mcp_data-shaped Module in a generated native
# project (instructions and two skills for both CLIs, a project script and the MCP fixture).
NATIVE_PROJECT_PATTERN = "native_project"
EXAMPLE_PATTERNS = (*PATTERN_NAMES, NATIVE_PROJECT_PATTERN)
NATIVE_PROJECT_TRANSPORTS = ("claude_cli", "codex_cli")
TRANSPORTS = ("claude_cli", "codex_cli", "antigravity_cli")
# The first Antigravity binding admits only these two requirement shapes.
ANTIGRAVITY_PATTERNS = ("structured_output", "file_review")
EXAMPLE_VERSION = "v1"
MCP_CONNECTION = "example_data"
MCP_TOOL = "get_stock_snapshot"
_SKILL_ID = "runtime-pattern-examples"
_OWNER = """# Runtime usage pattern examples

Small synthetic tasks that show the four ordinary usage patterns. Use only the
supplied input, task materials, project directory and declared connections.
The example check reads actual files, the returned execution log and the
fixture audit; a model's own report does not prove that a step happened.
No production data, credentials or business approval are involved.

Modules: example_structured_output, example_file_review, example_code_change
and example_mcp_data.
"""
_OWNER_REF = "owner-contract-sha256:" + hashlib.sha256(_OWNER.encode()).hexdigest()
_OWNER_PATH = "docs/runtime_pattern_examples.md"
_SKILL = """# Runtime pattern examples

Example task sources for the four ordinary usage patterns: example_structured_output,
example_file_review, example_code_change and example_mcp_data. Each Module folder
holds its prompt and input/output schemas; Runtime registers them unchanged.
"""
_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}


def _object(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


PATTERN_DEFINITIONS = {
    "structured_output": {
        "preset": {},
        "instruction": (
            "Extract the order in the input note. Use only the input; no tools are available. Return order_id "
            "exactly as given, one entry in lines per SKU with the quantity the note asks for, total_quantity as "
            "the sum of those quantities, and urgent true only when the note asks for urgent handling.\n"),
        "input": _object(order_id=_STRING, note=_STRING),
        "output": _object(order_id=_STRING, lines={"type": "array", "items": _object(sku=_STRING, quantity=_INTEGER)},
                          total_quantity=_INTEGER, urgent=_BOOLEAN),
    },
    "file_review": {
        "preset": {},
        "instruction": (
            "Review the task material named by material_path. It is listed under Runtime Input Resources; its "
            "contents are not in this prompt, so read it with your file tools or a read-only shell command.\n"
            "1. Create the file named by review_file directly in your writable scratch area (not in a subfolder). "
            "Line 1 is '# Review: ' followed by material_path. Line 2 is 'Release code: ' followed by the exact "
            "release code stated in the material. Then write one bullet line starting with '- ' for each "
            "inconsistency or risk you find in the material (at least one).\n"
            "2. In a separate step, read the saved file back from disk with one plain read that succeeds: a "
            "file-reading tool, or a read-only shell command on its own (for example Get-Content review.md in "
            "PowerShell, cat review.md in a POSIX shell). Do not rely on what you meant to write. Keep that read "
            "command simple: do not add other statements or build objects in the shell (PowerShell may run in "
            "constrained language mode, where constructs such as [pscustomobject] fail the whole command). If a "
            "read command fails, run a clean plain read again; a failed command does not count as reading back.\n"
            "3. Return review_file as the file name, release_code and readback_first_line exactly as read back, "
            "and finding_count as the number of bullet lines in the saved file. Your answer is the structured "
            "output of this task, not something a shell command has to produce.\n"),
        "input": _object(material_path=_STRING, review_file=_STRING),
        "output": _object(review_file=_STRING, release_code=_STRING, finding_count=_INTEGER,
                          readback_first_line=_STRING),
    },
    "code_change": {
        "preset": {},
        "instruction": (
            "Your working directory is a small Python project. The command in check_command currently fails "
            "because the file named by edit_file has a bug. Fix that bug by editing only edit_file; do not change, "
            "add or delete any other file. After your final edit, run check_command exactly as written, once, "
            "from the working directory, as its own command (no cd, no wrapper and nothing chained). Return "
            "changed_files (the files you edited), check_command as you ran it and passed (true only if the "
            "last line the command printed starts with CHECK PASSED).\n"),
        "input": _object(check_command=_STRING, edit_file=_STRING),
        "output": _object(changed_files={"type": "array", "items": _STRING}, check_command=_STRING,
                          passed=_BOOLEAN),
    },
    "mcp_data": {
        "preset": {"mcp_connections": (MCP_CONNECTION,)},
        "instruction": (
            f"1. Call the MCP tool {MCP_TOOL} of the {MCP_CONNECTION} connection exactly once, with warehouse and "
            "request_tag from the input.\n"
            "2. Save the complete data object the tool returned, as JSON, to the file named by data_file in your "
            "working directory: every field of the payload (response_id, warehouse, request_tag, items and "
            "source), taken from the structured content when the tool returns it, otherwise from the JSON in its "
            "text content. Do not save the MCP protocol wrapper around it (content, structuredContent, isError).\n"
            "3. In a separate step, read data_file back from disk with one plain read that succeeds: a "
            "file-reading tool, or a read-only shell command on its own (for example Get-Content in PowerShell, "
            "cat in a POSIX shell). Do not rely on memory, and do not add other statements or build objects in "
            "that command (PowerShell may run in constrained language mode). If a read command fails, run a "
            "clean plain read again; a failed command does not count as reading back.\n"
            "4. Using only the values read back, write the file named by summary_file in your working directory "
            "as a JSON object with response_id, warehouse, total_units (the sum of every item's units) and "
            "sku_count (the number of items).\n"
            "5. Return response_id, total_units, sku_count, data_file and summary_file.\n"),
        "input": _object(warehouse=_STRING, request_tag=_STRING, data_file=_STRING, summary_file=_STRING),
        "output": _object(response_id=_STRING, total_units=_INTEGER, sku_count=_INTEGER, data_file=_STRING,
                          summary_file=_STRING),
    },
    NATIVE_PROJECT_PATTERN: {
        "preset_id": "mcp_data",
        "preset": {"mcp_connections": (MCP_CONNECTION,)},
        "skill": """# Runtime pattern examples

Example task source for the native project example: example_native_project. Its Module folder
holds its prompt and input/output schemas; Runtime registers them unchanged.
""",
        "owner": """# Runtime native project example

A synthetic warehouse project run with native_project: the CLI loads the project's own
instructions (CLAUDE.md, AGENTS.md) and skills, calls the declared MCP fixture and runs the
project's Python script. The check reads actual files, the returned execution log and the
fixture audit. No production data, credentials or business approval are involved.

Module: example_native_project.
""",
        "instruction": (
            "Complete the request in the input inside this project. Follow the project's own instructions and use "
            "the project's skills and tools as they fit the request. Return report_file (the path of the report you "
            "saved, relative to the project) and response_id, total_units and sku_count exactly as the project's "
            "summary file gives them.\n"),
        "input": _object(request=_STRING),
        "output": _object(report_file=_STRING, response_id=_STRING, total_units=_INTEGER, sku_count=_INTEGER),
    },
}


def module_id_for(pattern: str) -> str:
    """Return the Module and one-node Workflow ID registered for a pattern."""
    _require_pattern(pattern)
    return "example_" + pattern


def _schema_text(ref, body) -> str:
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": ref, **body},
                      sort_keys=True, separators=(",", ":")) + "\n"


def pattern_source(pattern: str):
    """Return the pattern's v4 task source exactly as loading its written source folder yields it.

    Pure: no IO. write_pattern_source writes the same values as files; the
    loader reads them back to an equal ModuleRegistrationSource.
    """
    from ..registry.registry_module_loading import ModuleRegistrationSource
    module_id = module_id_for(pattern)
    definition = PATTERN_DEFINITIONS[pattern]
    input_ref, output_ref = f"schema:{module_id}_input@v1", f"schema:{module_id}_output@v1"
    owner = definition.get("owner", _OWNER)
    return ModuleRegistrationSource(
        skill_id=_SKILL_ID, module_id=module_id,
        owner_contract_ref="owner-contract-sha256:" + hashlib.sha256(owner.encode()).hexdigest(),
        owner_contract_content=owner,
        instruction_text=definition["instruction"],
        input_schema_ref=input_ref, input_schema_document=_schema_text(input_ref, definition["input"]),
        output_schema_ref=output_ref, output_schema_document=_schema_text(output_ref, definition["output"]),
        schema_version="runtime_module_registration_v4",
    )


def pattern_requirements(pattern: str):
    """The Module requirements the pattern registers: module_requirements_preset(<pattern>), max_attempts 1.

    native_project registers the mcp_data preset with the example_data connection; the native
    choice is per call, never part of the definition.
    """
    from ..registry.registry_module_authoring import module_requirements_preset
    _require_pattern(pattern)
    definition = PATTERN_DEFINITIONS[pattern]
    return module_requirements_preset(definition.get("preset_id", pattern), **definition["preset"])


def write_pattern_source(project_root: Path, pattern: str) -> Path:
    """Write the pattern as an ordinary v4 task source below project_root and return its Module folder.

    Writes .claude/skills/runtime-pattern-examples/SKILL.md, the owner document
    and runtime_modules/example_<pattern>/ (module_registration.json, prompt.md
    and schemas/). Existing files are never overwritten (exclusive create).
    """
    source = pattern_source(pattern)
    project_root = Path(project_root)
    skill = project_root / ".claude" / "skills" / _SKILL_ID
    module = skill / "runtime_modules" / source.module_id
    (module / "schemas").mkdir(parents=True)
    (project_root / _OWNER_PATH).parent.mkdir(parents=True, exist_ok=True)

    def create(path: Path, text: str) -> None:
        with open(path, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    if not (skill / "SKILL.md").exists():
        create(skill / "SKILL.md", PATTERN_DEFINITIONS[pattern].get("skill", _SKILL))
    if not (project_root / _OWNER_PATH).exists():
        create(project_root / _OWNER_PATH, source.owner_contract_content)
    create(module / "module_registration.json", json.dumps({
        "schema_version": "runtime_module_registration_v4", "skill_id": source.skill_id,
        "module_id": source.module_id, "owner_contract_path": _OWNER_PATH,
        "input_schema_ref": source.input_schema_ref, "input_schema_path": "schemas/input.schema.json",
        "output_schema_ref": source.output_schema_ref, "output_schema_path": "schemas/output.schema.json",
    }, indent=2, sort_keys=True) + "\n")
    create(module / "prompt.md", source.instruction_text)
    create(module / "schemas" / "input.schema.json", source.input_schema_document)
    create(module / "schemas" / "output.schema.json", source.output_schema_document)
    return module


def build_pattern_workflow(pattern: str):
    """Build one pattern's ordinary Module and one-node Workflow without IO.

    Args:
        pattern: One of PATTERN_NAMES.
    Returns:
        WorkflowExport of Module example_<pattern> at version v1, with its
        fixed instruction and input/output schemas (pattern_source). Its
        execution requirements are exactly module_requirements_preset(<pattern>)
        with max_attempts 1 (mcp_data with the example_data connection). The
        same definition serves claude_cli and codex_cli, and equals what
        register_module produces from the written source folder.
    Raises:
        ValueError: Unknown pattern.
    Effects:
        Pure construction; no registration, model selection or execution.
    """
    from ..registry.registry_module_authoring import Module

    exported = Module(pattern_source(pattern), execution_requirements=pattern_requirements(pattern)).export(
        module_version=EXAMPLE_VERSION)
    return Module.to_workflow(exported).export()


def _require_pattern(pattern):
    if pattern not in EXAMPLE_PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}; choose one of {', '.join(EXAMPLE_PATTERNS)}")


def _require_transport(transport, pattern=None):
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}; choose claude_cli, codex_cli or antigravity_cli")
    if transport == "antigravity_cli" and pattern is not None and pattern not in ANTIGRAVITY_PATTERNS:
        raise ValueError(f"antigravity_cli supports only the {' and '.join(ANTIGRAVITY_PATTERNS)} patterns in this "
                         f"binding; {pattern} is not available for it")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, text: str) -> bytes:
    """Write UTF-8 with LF line endings on every host so hashes and shell scripts are stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return data


def _write_json(path: Path, value) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path):
    """Read JSON written by Python or redirected by PowerShell (UTF-8 with/without BOM, or UTF-16)."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return json.loads(data.decode("utf-16"))
    return json.loads(data.decode("utf-8-sig"))


def _powershell_quote(value) -> str:
    """Quote one PowerShell argument when it is not a plain word (spaces, quotes, operators)."""
    text = str(value)
    if text and all(character.isalnum() or character in "-_.:/\\=" for character in text):
        return text
    return "'" + text.replace("'", "''") + "'"


# --- Per-pattern task material --------------------------------------------------------------

_MATERIAL_PATH = "release_notes.md"
_RELEASE_NOTES = """# Release notes: stock sync service

Release code: {code}

- Adds a nightly reconciliation of warehouse stock counts.
- Rollback window: 30 minutes after the release starts.
- Owner: platform operations.

## Rollout runbook

1. Announce the release in the operations channel.
2. If more than 2% of sync jobs fail, roll back within 15 minutes.
3. Confirm the next morning that stock counts match the warehouse system.
"""

_PRICING = '''"""Order pricing helpers for the example project."""


def line_total(unit_price_cents: int, quantity: int) -> int:
    """Return the price of one order line in cents."""
    return unit_price_cents + quantity


def order_total(lines: list[tuple[int, int]]) -> int:
    """Return the price of all order lines in cents."""
    return sum(line_total(price, quantity) for price, quantity in lines)
'''

_CHECK = '''"""Bounded check for pricing.py. Prints one result line; exit 0 only when every case passes."""
import hashlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
NONCE = "{nonce}"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pricing  # noqa: E402

CASES = [((250, 4), 1000), ((199, 3), 597), ((0, 7), 0)]
failures = ["line_total%r = %r, expected %r" % (args, pricing.line_total(*args), expected)
            for args, expected in CASES if pricing.line_total(*args) != expected]
if pricing.order_total([(250, 4), (199, 3)]) != 1597:
    failures.append("order_total is not 1597")
digest = hashlib.sha256((HERE / "pricing.py").read_bytes()).hexdigest()
if failures:
    print("CHECK FAILED nonce=%s pricing_sha256=%s: %s" % (NONCE, digest, "; ".join(failures)))
    sys.exit(1)
print("CHECK PASSED nonce=%s pricing_sha256=%s" % (NONCE, digest))
'''

_SHELL_CHECK = """#!/bin/sh
# Runs the bounded pricing check with the Python interpreter that prepared this example.
exec {interpreter} check_pricing.py
"""


def _check_command(transport: str, project: Path) -> tuple[str, tuple[str, ...]]:
    """Return the check command written into the task input and the Claude permits for it.

    Claude's Bash tool runs a POSIX shell (Git Bash on Windows) and needs a host
    permit; the fixed relative command keeps the permit exact whatever the
    interpreter path contains. Codex has no per-command permits; its shell is
    PowerShell on Windows, so the interpreter is started with the call operator.
    """
    if transport == "claude_cli":
        interpreter = Path(sys.executable).as_posix() if os.name == "nt" else sys.executable
        _write(project / "run_check.sh", _SHELL_CHECK.format(interpreter=shlex.quote(interpreter)))
        return "sh run_check.sh", ("sh run_check.sh",)
    if os.name == "nt":
        return f"& {_powershell_quote(sys.executable)} check_pricing.py", ()
    return f"{shlex.quote(sys.executable)} check_pricing.py", ()


def _host_check(project: Path) -> dict:
    """Run check_pricing.py directly with this interpreter, independently of any model."""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run([sys.executable, "check_pricing.py"], cwd=project, capture_output=True,
                               text=True, timeout=120, env=environment)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {"returncode": completed.returncode, "last_line": lines[-1] if lines else "",
            "stderr_tail": completed.stderr[-400:]}


def _project_hashes(project: Path) -> dict[str, str]:
    return {path.relative_to(project).as_posix(): _sha256(path.read_bytes())
            for path in sorted(project.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def _mcp_connection(transport: str, audit: Path) -> dict:
    """The fixture's ordinary MCP connection entry, in the selected CLI's native form."""
    if importlib.util.find_spec("mcp") is None:
        raise ModuleNotFoundError("The mcp_data example's fixture server needs the mcp package in this Python; "
                                  "install agent-runtime-core[cli_tools] (or mcp) first")
    server = Path(__file__).resolve().with_name("conformance_fixture_mcp_serving.py")
    config = {"command": sys.executable, "args": [str(server), "--audit", str(audit)]}
    if transport == "codex_cli":
        config["default_tools_approval_mode"] = "approve"
    return {"mcp_servers": {transport: {MCP_CONNECTION: {"config": config, "environment_keys": []}}}}


_NATIVE_INSTRUCTIONS = """# Warehouse operations project

Reports produced in this project are saved under reports/ and start with the line
"Project-Marker: {marker}" so that reviewers can tell they came from this project.

Run project Python scripts with this project's interpreter: {interpreter}
Data fetched for a report is kept under data/.
"""
_NATIVE_SKILLS = {
    "stock-report": """---
name: stock-report
description: Prepare a warehouse stock report. Use when someone asks for a stock or inventory report for a warehouse; fetches the snapshot through the example_data connection, computes totals with the project script and saves the report.
---

# Stock report

1. Call the get_stock_snapshot tool of the example_data MCP connection once, with the warehouse and
   request_tag named in the request.
2. Save the complete data object the tool returned (response_id, warehouse, request_tag, items and
   source; not the MCP wrapper around it) as JSON to data/snapshot.json.
3. Compute the totals with the project script, using the project's interpreter:
   `{interpreter} scripts/summarize_stock.py data/snapshot.json reports/stock_summary.json`
   Do not compute the totals by hand.
4. Write reports/stock_report.md following the project's report rules, with the warehouse, response_id,
   total_units and sku_count read from reports/stock_summary.json.
""",
    "meeting-notes": """---
name: meeting-notes
description: Record meeting notes. Use when someone asks to write up or summarize a meeting; saves the notes under notes/.
---

# Meeting notes

Write the notes to notes/<date>-<topic>.md with attendees, decisions and action items.
""",
}
_NATIVE_SCRIPT = '''"""Summarize a saved stock snapshot. Usage: summarize_stock.py SNAPSHOT_JSON OUTPUT_JSON"""
import json
import sys
from pathlib import Path

SCRIPT_NONCE = "{script_nonce}"
snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
items = snapshot["items"]
summary = {{"response_id": snapshot["response_id"], "warehouse": snapshot["warehouse"],
           "total_units": sum(item["units"] for item in items), "sku_count": len(items),
           "python_executable": sys.executable, "script_nonce": SCRIPT_NONCE}}
target = Path(sys.argv[2])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(summary, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(summary))
'''


# The heading of the stock-report skill body; a Codex Shell step that read the file shows it in its output.
_NATIVE_SKILL_HEADING = "# Stock report"


def _write_native_project(project: Path, *, marker: str, script_nonce: str, interpreter: str) -> None:
    """A caller-owned project as each CLI discovers it natively: CLAUDE.md + .claude/skills, AGENTS.md + .agents/skills.

    The marker and the script nonce exist only in project files, never in the task input or prompt.
    The interpreter is written with forward slashes so PowerShell and Git Bash both run it as written.
    """
    instructions = _NATIVE_INSTRUCTIONS.format(marker=marker, interpreter=interpreter)
    _write(project / "CLAUDE.md", instructions)
    _write(project / "AGENTS.md", instructions)
    for name, body in _NATIVE_SKILLS.items():
        text = body.format(interpreter=interpreter) if name == "stock-report" else body
        _write(project / ".claude" / "skills" / name / "SKILL.md", text)
        _write(project / ".agents" / "skills" / name / "SKILL.md", text)
    _write(project / "scripts" / "summarize_stock.py", _NATIVE_SCRIPT.format(script_nonce=script_nonce))


# --- Prepare, run and check -----------------------------------------------------------------

def prepare_pattern_example(root: Path, pattern: str, transport: str, *, cli_path=None, model_id=None,
                            reasoning_profile=None, run_timeout_seconds=None) -> dict:
    """Create one fresh example folder, register its definition and write its task.

    Args:
        root: Example root, created when missing. Each call creates a new
            <pattern>-<cli>-<UTC yymmddHHMMSS>-<hex> folder below it and writes
            only there; an existing folder is never reused or overwritten. Use
            a short ordinary user folder (for example under Documents): Codex's
            Windows sandbox cannot enter protected temporary or redirected
            app-data folders, and that CLI failure is reported as it occurs.
        pattern: One of PATTERN_NAMES.
        transport: claude_cli, codex_cli or antigravity_cli. It shapes only
            per-call inputs (the code_change check command and Claude permit,
            the MCP entry form, the Antigravity file_review read-back permit);
            the registered definition is the same for all. antigravity_cli
            accepts only structured_output and file_review.
        cli_path, model_id, reasoning_profile, run_timeout_seconds: Optional
            values copied into the printed agent-runtime-test-run command.
    Returns:
        The manifest also saved as run.json: run_directory, source_directory
        (the written Module source), host_root, workflow_id, version,
        selection_path (the code-written selection.json), registered (the
        selection's Workflow and Module release refs and hashes, read back by
        code), input/resources/project paths, command_permits, the equivalent
        agent-runtime-test-run argv and PowerShell line (--selection and
        --record-out), the inspection and check commands, and the expectations
        the check uses.
    Raises:
        ValueError: Unknown pattern or transport.
        ModuleNotFoundError: mcp_data without the mcp package for its fixture.
        FileExistsError: The generated run folder already exists.
        RuntimeError: The registered definition differs from the pattern's
            definition, or the code_change project did not start failing.
    Effects:
        Writes the run folder, including the task source; registers it under
        host/ with register_module and writes selection.json; writes
        host/.runtime/config.json with attempt_workspace_root RUN_FOLDER/attempts
        (short Windows paths) and, for mcp_data, the one MCP connection. Never
        starts a model.
    """
    from ..invocation.invocation_local_resource_preparation import prepare_material_files, write_local_resources
    from ..registry.registry_definition_selection import selection_from_registration, write_definition_selection
    from ..registry.registry_plugin_registration import register_module

    _require_pattern(pattern)
    _require_transport(transport, pattern)
    if pattern == NATIVE_PROJECT_PATTERN and transport not in NATIVE_PROJECT_TRANSPORTS:
        raise ValueError("native_project runs with claude_cli or codex_cli only")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    nonce = secrets.token_hex(4)
    stamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    # Short names: Attempt folders nest materials and CLI files below this folder,
    # and Windows paths stay limited to 260 characters unless long paths are enabled.
    run_directory = root / f"{pattern}-{transport.removesuffix('_cli')}-{stamp}-{nonce[:4]}"
    run_directory.mkdir()
    host = run_directory / "host"
    config = {"attempt_workspace_root": str(run_directory / "attempts")}
    # The ordinary path: an approved source folder -> registration and readback -> code-written selection.
    source = write_pattern_source(run_directory / "source", pattern)
    result = register_module(host, source=source, module_version=EXAMPLE_VERSION,
                             execution_requirements=pattern_requirements(pattern))
    selection = selection_from_registration(result, root=host)
    selection_path = write_definition_selection(run_directory / "selection.json", selection)
    registered = {name: getattr(selection, name) for name in (
        "workflow_release_ref", "workflow_release_sha256", "module_release_ref", "module_release_sha256")}
    if selection.workflow_release_sha256 != build_pattern_workflow(pattern).workflow_release.release_sha256:
        raise RuntimeError(f"registered example differs from the pattern definition: {registered}")
    module_id = selection.module_id
    manifest = {"schema_version": "runtime_pattern_example_v2", "pattern": pattern, "transport": transport,
                "nonce": nonce, "run_directory": str(run_directory), "source_directory": str(source),
                "host_root": str(host), "workflow_id": selection.workflow_id, "version": selection.workflow_version,
                "selection_path": str(selection_path), "registered": registered,
                "input_path": str(run_directory / "input.json"), "record_path": str(run_directory / "record.json"),
                "resources_path": None, "project_directory": None, "command_permits": [], "expected": {}}
    if pattern == "structured_output":
        quantities = {sku: 1 + secrets.randbelow(9) for sku in ("A-100", "B-220", "C-310")}
        urgent = secrets.randbelow(2) == 1
        order_id = "ORD-" + nonce.upper()
        note = (f"Order {order_id} from the Harbor Street shop: please send {quantities['A-100']} boxes of A-100, "
                f"{quantities['B-220']} boxes of B-220 and {quantities['C-310']} boxes of C-310. "
                + ("This is urgent: the shop shelves are empty." if urgent else
                   "No rush: the shop still has enough stock for this week."))
        payload = {"order_id": order_id, "note": note}
        manifest["expected"] = {"order_id": order_id, "lines": quantities, "total_quantity": sum(quantities.values()),
                                "urgent": urgent}
    elif pattern == "file_review":
        code = "RC-" + secrets.token_hex(3).upper()
        material = _write(run_directory / "materials" / _MATERIAL_PATH, _RELEASE_NOTES.format(code=code))
        # Code computes the frozen file list from the selected file; the run refuses later drift.
        rows = prepare_material_files(run_directory / "materials", (_MATERIAL_PATH,))
        resources = write_local_resources(run_directory / "resources.json", material_root=run_directory / "materials",
                                          material_files=rows)
        payload = {"material_path": _MATERIAL_PATH, "review_file": "review.md"}
        manifest["resources_path"] = str(resources)
        if transport == "antigravity_cli":
            # agy runs only explicitly permitted commands: the live-tested exact regex rule for the
            # plain read-back the task suggests. Its file tools need no permit.
            from ..invocation.invocation_antigravity_cli_execution import antigravity_command_permit
            manifest["command_permits"] = [antigravity_command_permit("Get-Content " + payload["review_file"])]
        manifest["expected"] = {"material_sha256": _sha256(material)}
    elif pattern == "code_change":
        project = run_directory / "project"
        _write(project / "pricing.py", _PRICING)
        _write(project / "check_pricing.py", _CHECK.format(nonce=nonce))
        command, permits = _check_command(transport, project)
        initial = _host_check(project)
        if initial["returncode"] != 1 or not initial["last_line"].startswith("CHECK FAILED"):
            raise RuntimeError(f"code_change project did not start failing: {initial}")
        payload = {"check_command": command, "edit_file": "pricing.py"}
        manifest.update(project_directory=str(project), command_permits=list(permits))
        manifest["expected"] = {"initial_files": _project_hashes(project), "initial_check": initial}
    elif pattern == "mcp_data":
        project = run_directory / "project"
        project.mkdir()
        audit = run_directory / "mcp_fixture_audit.jsonl"
        config.update(_mcp_connection(transport, audit))
        payload = {"warehouse": "north-" + nonce[:4], "request_tag": "tag-" + nonce,
                   "data_file": "stock_snapshot.json", "summary_file": "stock_summary.json"}
        manifest["project_directory"] = str(project)
        manifest["expected"] = {"audit_path": str(audit)}
    else:
        project = run_directory / "project"
        audit = run_directory / "mcp_fixture_audit.jsonl"
        config.update(_mcp_connection(transport, audit))
        marker, script_nonce = "PRJ-" + secrets.token_hex(4).upper(), secrets.token_hex(4)
        interpreter = Path(sys.executable).as_posix()
        _write_native_project(project, marker=marker, script_nonce=script_nonce, interpreter=interpreter)
        warehouse, tag = "north-" + nonce[:4], "tag-" + nonce
        payload = {"request": f"Please prepare the stock report for warehouse {warehouse} (request tag {tag})."}
        # Per-call choices, never part of the registered definition: Codex runs native projects in host mode.
        manifest.update(project_directory=str(project), native_project=True,
                        codex_execution_mode="host" if transport == "codex_cli" else "configured")
        manifest["expected"] = {"audit_path": str(audit), "marker": marker, "script_nonce": script_nonce,
                                "interpreter": interpreter, "warehouse": warehouse, "request_tag": tag,
                                "skills": sorted(_NATIVE_SKILLS), "used_skill": "stock-report"}
    _write_json(host / ".runtime" / "config.json", config)
    _write_json(run_directory / "input.json", payload)
    argv = ["agent-runtime-test-run", "--root", str(host), "--selection", str(selection_path),
            "--input", manifest["input_path"], "--transport", transport]
    for flag, value in (("--resources", manifest["resources_path"]), ("--project-directory", manifest["project_directory"]),
                        ("--cli-path", cli_path), ("--model", model_id), ("--effort", reasoning_profile),
                        ("--run-timeout-seconds", run_timeout_seconds)):
        if value is not None:
            argv += [flag, str(value)]
    for permit in manifest["command_permits"]:
        argv += ["--command-permit", permit]
    if manifest.get("native_project"):
        argv += ["--native-project"]
    if manifest.get("codex_execution_mode", "configured") != "configured":
        argv += ["--codex-execution-mode", manifest["codex_execution_mode"]]
    argv += ["--record-out", manifest["record_path"]]
    manifest["test_run_argv"] = argv
    manifest["powershell"] = " ".join([argv[0], *map(_powershell_quote, argv[1:])])
    manifest["inspect_argv"] = ["agent-runtime-inspect", "--test-run-record", manifest["record_path"]]
    manifest["check_argv"] = [sys.executable, "-m", _MODULE, "check", "--run-dir", str(run_directory)]
    _write_json(run_directory / "run.json", manifest)
    return manifest


def run_pattern_example(root: Path, pattern: str, transport: str, *, cli_path=None, model_id=None,
                        reasoning_profile=None, run_timeout_seconds=None) -> dict:
    """Prepare one example, run it through run_local_workflow_test and check its actual effects.

    Args:
        root, pattern, transport: As in prepare_pattern_example.
        cli_path: Native claude/codex executable; None uses Runtime's normal
            lookup (the host root's provider_cli_paths, then PATH).
        model_id, reasoning_profile, run_timeout_seconds: Passed unchanged to
            run_local_workflow_test. Codex requires an explicit model and
            effort; Claude falls back to Runtime defaults. Never inferred here.
    Returns:
        The check result (see check_pattern_example). record.json and
        check.json are saved in the run folder.
    Raises:
        Exception: Preparation and run_local_workflow_test errors keep their
            own types (for example a missing CLI or an unsupported option);
            record.json then holds the error document, as --record-out does.
    Effects:
        Starts the selected CLI once through Runtime's ordinary Test Run with
        the code-written selection.json, so exactly the registered version and
        Module run. The Attempt folder stays under RUN_FOLDER/attempts; Runtime
        never deletes the project folder.
    """
    from .conformance_local_test_run import run_local_workflow_test, write_test_run_record

    manifest = prepare_pattern_example(root, pattern, transport, cli_path=cli_path, model_id=model_id,
                                       reasoning_profile=reasoning_profile, run_timeout_seconds=run_timeout_seconds)
    run_directory = Path(manifest["run_directory"])
    project = manifest["project_directory"]
    try:
        record = run_local_workflow_test(
            Path(manifest["host_root"]), selection=Path(manifest["selection_path"]),
            input_payload=_read_json(Path(manifest["input_path"])), transport_kind=transport, model_id=model_id,
            reasoning_profile=reasoning_profile, run_timeout_seconds=run_timeout_seconds, cli_path=cli_path,
            resources_path=None if manifest["resources_path"] is None else Path(manifest["resources_path"]),
            project_directory=None if project is None else Path(project),
            command_permits=tuple(manifest["command_permits"]),
            native_project=manifest.get("native_project", False),
            codex_execution_mode=manifest.get("codex_execution_mode", "configured"))
    except BaseException as exc:
        write_test_run_record(Path(manifest["record_path"]), {
            "error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None), "detail": str(exc)})
        raise
    write_test_run_record(Path(manifest["record_path"]), record)
    return check_pattern_example(run_directory)


class _Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, passed, detail=""):
        self.rows.append({"check": name, "passed": bool(passed), "detail": str(detail)[:600]})
        return bool(passed)


def _tool_calls(record) -> list[dict]:
    attempts = ((record or {}).get("execution_log") or {}).get("attempts") or []
    return [call for attempt in attempts for call in (attempt.get("tool_calls") or []) if isinstance(call, dict)]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _succeeded(call) -> bool:
    """The log recorded the call as completed, without a native error flag or a nonzero exit."""
    if call.get("status") != "completed":
        return False
    response = call.get("response")
    if isinstance(response, dict):
        if isinstance(response.get("tool_result"), dict) and response["tool_result"].get("is_error") is True:
            return False
        if isinstance(response.get("tool_use_result"), dict) and response["tool_use_result"].get("interrupted"):
            return False
        if response.get("exit_code") not in (None, 0):
            return False
    return True


_READ_STEPS = {"Read", "Bash", "command_execution", "read_file", "run_command"}
# Antigravity's stream reports a file read as a summary ("N lines, M bytes"), not its content,
# and a file write by its path only.
_SUMMARY_READ_STEPS = {"read_file"}
_PATH_ONLY_WRITE_STEPS = {"write_file"}


def _readbacks(calls, *, file_name: str, marker: str, after: int = -1) -> list[int]:
    """Indices of completed read steps after `after` that name file_name and whose result, not request, shows marker.

    A summary-only read (Antigravity read_file) cannot show the marker. It counts
    only when it follows the last logged write naming the same file; the file's
    content is then shown by the host's own disk checks, not by the log.
    """
    found = []
    writes = [index for index, call in enumerate(calls)
              if call.get("tool_name") in _PATH_ONLY_WRITE_STEPS and file_name in _text(call.get("request"))]
    for index, call in enumerate(calls):
        if index <= after or call.get("tool_name") not in _READ_STEPS or not _succeeded(call):
            continue
        request = _text(call.get("request"))
        if file_name not in request:
            continue
        if call.get("tool_name") in _SUMMARY_READ_STEPS:
            if writes and index > writes[-1]:
                found.append(index)
        elif marker not in request and marker in _text(call.get("response")):
            found.append(index)
    return found


def _command_results(calls, marker: str) -> list[int]:
    """Indices of completed shell commands whose own output, not request text, carries marker."""
    return [index for index, call in enumerate(calls)
            if call.get("tool_name") in {"Bash", "command_execution"} and _succeeded(call)
            and marker not in _text(call.get("request")) and marker in _text(call.get("response"))]


def _names_file(value, folder: Path, expected: str) -> bool:
    """Whether a returned path names folder/expected (relative to folder, or absolute)."""
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = Path(value.strip())
    candidate = candidate if candidate.is_absolute() else folder / candidate
    return os.path.normcase(os.path.abspath(candidate)) == os.path.normcase(os.path.abspath(folder / expected))


def _attempt_row(record):
    rows = (record or {}).get("attempt_workspaces") or []
    return rows[-1] if rows else {}


def _check_structured_output(checks, manifest, record, calls, output):
    expected = manifest["expected"]
    checks.add("output_order_id", output.get("order_id") == expected["order_id"], output.get("order_id"))
    try:
        lines = {row["sku"]: row["quantity"] for row in output.get("lines") or []}
    except (TypeError, KeyError):
        lines = None
    checks.add("output_lines", lines == expected["lines"], f"got {lines}, expected {expected['lines']}")
    checks.add("output_total_quantity", output.get("total_quantity") == expected["total_quantity"],
               f"got {output.get('total_quantity')}, expected {expected['total_quantity']}")
    checks.add("output_urgent", output.get("urgent") is expected["urgent"],
               f"got {output.get('urgent')}, expected {expected['urgent']}")
    # Claude delivers native structured output through its own StructuredOutput step; that is not a task tool.
    used = [call.get("tool_name") for call in calls if call.get("tool_name") != "StructuredOutput"]
    checks.add("no_task_tools", not used, f"tool calls recorded: {used}")
    return {}


def _check_file_review(checks, manifest, record, calls, output):
    run_directory = Path(manifest["run_directory"])
    payload = _read_json(Path(manifest["input_path"]))
    material = (run_directory / "materials" / payload["material_path"]).read_bytes()
    checks.add("material_unchanged", _sha256(material) == manifest["expected"]["material_sha256"])
    code = next((line.split(":", 1)[1].strip() for line in material.decode("utf-8").splitlines()
                 if line.startswith("Release code:")), "")
    checks.add("material_fact_absent_from_input", code and code not in _text(payload) and code not in _text(
        (record or {}).get("input_payload")), "the release code exists only in the material file")
    workspace = _attempt_row(record).get("workspace_path")
    review = None if not workspace else Path(workspace) / "work" / "scratch" / payload["review_file"]
    text = review.read_text(encoding="utf-8", errors="replace") if review is not None and review.is_file() else None
    checks.add("review_file_written", text is not None, review)
    lines = [] if text is None else [line.rstrip("\r") for line in text.splitlines()]
    first = lines[0].strip() if lines else ""
    bullets = [line for line in lines if line.lstrip().startswith("- ")]
    checks.add("review_first_line", first == f"# Review: {payload['material_path']}", first)
    checks.add("review_contains_material_fact", f"Release code: {code}" in lines, f"expected 'Release code: {code}'")
    checks.add("review_has_findings", len(bullets) >= 1, f"{len(bullets)} bullet lines")
    readbacks = _readbacks(calls, file_name=payload["review_file"], marker=code) if code else []
    summary_only = [index for index in readbacks if calls[index].get("tool_name") in _SUMMARY_READ_STEPS]
    checks.add("review_read_back_in_log", readbacks, f"read steps showing the saved fact: {readbacks}" + (
        f"; summary-only reads after the last write {summary_only}: the provider log does not contain the "
        "read content, which the host file checks above show" if summary_only else ""))
    checks.add("output_review_file", isinstance(output.get("review_file"), str)
               and Path(output["review_file"].strip()).name == payload["review_file"], output.get("review_file"))
    checks.add("output_release_code", output.get("release_code") == code, output.get("release_code"))
    checks.add("output_readback_first_line", str(output.get("readback_first_line", "")).strip() == first,
               output.get("readback_first_line"))
    checks.add("output_finding_count", output.get("finding_count") == len(bullets),
               f"got {output.get('finding_count')}, file has {len(bullets)}")
    return {"review_file": None if review is None else str(review), "material": str(
        run_directory / "materials" / payload["material_path"])}


def _check_code_change(checks, manifest, record, calls, output):
    project = Path(manifest["project_directory"])
    initial_files = manifest["expected"]["initial_files"]
    checks.add("project_started_failing", manifest["expected"]["initial_check"]["returncode"] == 1,
               manifest["expected"]["initial_check"]["last_line"])
    current = _project_hashes(project)
    changed = sorted(name for name, digest in initial_files.items() if current.get(name) != digest)
    checks.add("only_pricing_changed", changed == ["pricing.py"], f"changed: {changed}")
    # The task allows editing pricing.py only; __pycache__ (bytecode) is the one ignored generated folder.
    added = sorted(set(current) - set(initial_files))
    checks.add("no_files_added", not added, f"added: {added}")
    # Code computes the pass line for the final file bytes; the model never has to restate it.
    rerun = _host_check(project)
    nonce = manifest["nonce"]
    marker = f"CHECK PASSED nonce={nonce} pricing_sha256={current.get('pricing.py')}"
    checks.add("host_rerun_passes", rerun["returncode"] == 0 and rerun["last_line"] == marker, rerun)
    results = _command_results(calls, marker)
    checks.add("model_ran_check_on_final_code", results, f"shell calls with the final pass line: {results}")
    command = _read_json(Path(manifest["input_path"]))["check_command"]
    changed_files = output.get("changed_files")
    checks.add("output_changed_files", isinstance(changed_files, list) and bool(changed_files)
               and all(_names_file(entry, project, "pricing.py") for entry in changed_files), changed_files)
    checks.add("output_check_command", str(output.get("check_command", "")).strip() == command,
               f"got {output.get('check_command')!r}, expected {command!r}")
    passed = rerun["returncode"] == 0 and rerun["last_line"] == marker
    checks.add("output_passed_agrees_with_host_rerun", output.get("passed") is passed,
               f"model reported passed={output.get('passed')!r}; host rerun passed={passed}")
    evidence = [{"log_index": index, "tool_name": calls[index].get("tool_name"),
                 "request": calls[index].get("request"), "observed_last_line": marker} for index in results]
    return {"project_directory": str(project), "added_files": added, "host_rerun": rerun,
            "command_evidence": evidence}


def _audit_calls(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") == "tools/call" and row.get("tool") == MCP_TOOL:
            rows.append(row)
    return rows


def _load_json_file(path: Path):
    try:
        return _read_json(path)
    except (OSError, ValueError):
        return None


def _check_mcp_data(checks, manifest, record, calls, output):
    payload = _read_json(Path(manifest["input_path"]))
    project = Path(manifest["project_directory"])
    requested = {"warehouse": payload["warehouse"], "request_tag": payload["request_tag"]}
    received = _audit_calls(Path(manifest["expected"]["audit_path"]))
    # Every audited call the fixture answered for exactly this task's arguments; a CLI may legitimately retry.
    audit = [row for row in received if row.get("arguments") == requested
             and all((row.get("result") or {}).get(key) == value for key, value in requested.items())]
    checks.add("fixture_received_tools_call", audit,
               f"{len(audit)} audited calls with this task's arguments; {len(received) - len(audit)} with other arguments")
    data = _load_json_file(project / payload["data_file"])
    saved_id = data.get("response_id") if isinstance(data, dict) else None
    # Compare with the audited response the saved file names; otherwise with the last one received.
    result = next((row["result"] for row in audit if row["result"].get("response_id") == saved_id),
                  audit[-1]["result"] if audit else {})
    response_id = result.get("response_id", "")
    mcp = [index for index, call in enumerate(calls)
           if str(call.get("tool_name", "")).split("__")[-1] == MCP_TOOL and _succeeded(call)
           and response_id and response_id in _text(call.get("response"))]
    checks.add("mcp_call_in_log", mcp, f"log calls returning the audited response: {mcp}")
    checks.add("data_file_matches_fixture_response", bool(result) and data == result,
               "the saved JSON must equal the complete audited result")
    readbacks = _readbacks(calls, file_name=payload["data_file"], marker=response_id,
                           after=mcp[0] if mcp else -1) if response_id else []
    checks.add("data_file_read_back_in_log", readbacks, f"read steps showing the saved response_id: {readbacks}")
    items = result.get("items") or []
    expected = {"response_id": response_id, "warehouse": result.get("warehouse"),
                "total_units": sum(item.get("units", 0) for item in items), "sku_count": len(items)}
    summary = _load_json_file(project / payload["summary_file"])
    checks.add("summary_file_derived", isinstance(summary, dict) and bool(result)
               and all(summary.get(key) == value for key, value in expected.items()),
               f"got {summary}, expected {expected}")
    checks.add("output_matches_fixture", bool(result) and all(output.get(key) == expected[key]
                                                              for key in ("response_id", "total_units", "sku_count")),
               {key: output.get(key) for key in ("response_id", "total_units", "sku_count")})
    checks.add("output_artifact_paths", _names_file(output.get("data_file"), project, payload["data_file"])
               and _names_file(output.get("summary_file"), project, payload["summary_file"]),
               {key: output.get(key) for key in ("data_file", "summary_file")})
    return {"project_directory": str(project), "audit": manifest["expected"]["audit_path"]}


def _provider_logs(record) -> list[dict]:
    attempts = ((record or {}).get("execution_log") or {}).get("attempts") or []
    return [attempt.get("provider_log") or {} for attempt in attempts if isinstance(attempt, dict)]


def _claude_init_events(logs) -> list[dict]:
    """Claude's own init events from the raw stdout each Attempt retained (observation, not configuration)."""
    events = []
    for log in logs:
        for line in str(log.get("stdout") or "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
                events.append(event)
    return events


def _strings(value):
    """Every string inside a decoded log value (tool output can be nested in lists and objects)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _check_native_project(checks, manifest, record, calls, output):
    expected = manifest["expected"]
    project = Path(manifest["project_directory"])
    transport = manifest["transport"]
    logs = _provider_logs(record)
    configurations = [log.get("effective_configuration") or {} for log in logs]
    enabled = ["claude_md", "skills"] if transport == "claude_cli" else ["agents_md", "skills"]
    suppressed = ["auto_memory", "hooks"] if transport == "claude_cli" else None
    checks.add("native_project_recorded", configurations and all(
        item.get("native_project") is True and item.get("context_enabled") == enabled
        and item.get("command_scope") == "host" and item.get("filesystem_scope") == "task_directed"
        and (suppressed is None or item.get("context_suppressed") == suppressed)
        and (transport != "claude_cli" or {"Skill", "Bash", "PowerShell"} <= set(item.get("allowed_rules") or ()))
        for item in configurations),
        [{key: item.get(key) for key in ("native_project", "context_enabled", "context_suppressed", "command_scope",
                                         "filesystem_scope", "allowed_rules")} for item in configurations])
    marker = expected["marker"]
    sent = _text([log.get("actual_prompt") for log in logs]) + _text(record.get("input_payload"))
    checks.add("project_marker_only_in_project_files", marker not in sent and marker not in _text(
        _read_json(Path(manifest["input_path"]))), "the marker exists only in CLAUDE.md/AGENTS.md")
    report = project / "reports" / "stock_report.md"
    lines = [line.strip() for line in report.read_text(encoding="utf-8", errors="replace").splitlines()
             if line.strip()] if report.is_file() else []
    checks.add("project_instructions_followed", lines[:1] == [f"Project-Marker: {marker}"],
               f"{report}: first line {lines[:1]}")
    if transport == "claude_cli":
        listed = [event.get("skills") or [] for event in _claude_init_events(logs)]
        checks.add("project_skills_discovered", listed and all(set(expected["skills"]) <= set(names) for names in listed),
                   f"init skills include {expected['skills']}: {[sorted(set(expected['skills']) & set(n)) for n in listed]}")
    skill = expected["used_skill"]
    # Claude must invoke the skill through its native Skill tool; Codex has no such tool and must
    # actually read the skill's SKILL.md through its Shell. Reading the file does not count for Claude.
    # A Codex read is proven by the step's own output carrying the skill body: a later command on the
    # same Shell line may still fail the step as a whole after the file was read.
    if transport == "claude_cli":
        used = [index for index, call in enumerate(calls) if _succeeded(call)
                and call.get("tool_name") == "Skill" and skill in _text(call.get("request"))]
    else:
        used = [index for index, call in enumerate(calls) if call.get("tool_name") == "command_execution"
                and skill in _text(call.get("request")) and "SKILL.md" in _text(call.get("request"))
                and any(_NATIVE_SKILL_HEADING in text for text in _strings(call.get("response")))]
    checks.add("relevant_skill_used", used, f"log steps {'invoking' if transport == 'claude_cli' else 'reading'} "
               f"{skill}: " + _text([(index, calls[index].get("tool_name")) for index in used]))
    requested = {"warehouse": expected["warehouse"], "request_tag": expected["request_tag"]}
    audit = [row for row in _audit_calls(Path(expected["audit_path"])) if row.get("arguments") == requested]
    checks.add("fixture_received_tools_call", audit, f"{len(audit)} audited calls with this task's arguments")
    snapshot = _load_json_file(project / "data" / "snapshot.json")
    result = next((row["result"] for row in audit if isinstance(snapshot, dict)
                   and row["result"].get("response_id") == snapshot.get("response_id")),
                  audit[-1]["result"] if audit else {})
    checks.add("snapshot_matches_fixture_response", bool(result) and snapshot == result,
               "data/snapshot.json must equal the complete audited result")
    items = result.get("items") or []
    totals = {"response_id": result.get("response_id"), "warehouse": result.get("warehouse"),
              "total_units": sum(item.get("units", 0) for item in items), "sku_count": len(items)}
    summary = _load_json_file(project / "reports" / "stock_summary.json")
    # The run itself must be in the log: a completed shell step whose command starts Python on the project
    # script and whose own output carries the JSON the script prints (reading the script shows only its
    # SCRIPT_NONCE assignment, not this printed field). The artifact then names the interpreter it ran under.
    printed = f'"script_nonce": "{expected["script_nonce"]}"'
    runs = [index for index, call in enumerate(calls) if _succeeded(call)
            and call.get("tool_name") in {"Bash", "PowerShell", "command_execution"}
            and "summarize_stock.py" in _text(call.get("request")) and "python" in _text(call.get("request")).lower()
            and any(printed in text for text in _strings(call.get("response")))]
    interpreter_matches = isinstance(summary, dict) and summary.get("script_nonce") == expected["script_nonce"] and (
        os.path.normcase(os.path.abspath(str(summary.get("python_executable"))))
        == os.path.normcase(os.path.abspath(expected["interpreter"])))
    checks.add("project_python_script_ran", runs and interpreter_matches,
               {"log_steps_running_the_script": runs, **({key: summary.get(key) for key in (
                   "script_nonce", "python_executable")} if isinstance(summary, dict) else {"summary": summary})})
    checks.add("python_artifact_matches_fixture", bool(result) and isinstance(summary, dict) and all(
        summary.get(key) == value for key, value in totals.items()), f"got {summary}, expected {totals}")
    checks.add("output_matches_artifact", bool(result) and all(
        output.get(key) == totals[key] for key in ("response_id", "total_units", "sku_count")),
        {key: output.get(key) for key in ("response_id", "total_units", "sku_count")})
    checks.add("output_report_file", _names_file(output.get("report_file"), project, "reports/stock_report.md"),
               output.get("report_file"))
    return {"project_directory": str(project), "report": str(report), "audit": expected["audit_path"],
            "skill_steps": used}


_PATTERN_CHECKS = {"structured_output": _check_structured_output, "file_review": _check_file_review,
                   "code_change": _check_code_change, "mcp_data": _check_mcp_data,
                   NATIVE_PROJECT_PATTERN: _check_native_project}


def check_pattern_example(run_directory: Path, record: dict | None = None) -> dict:
    """Check one example run against actual effects, never the model's self-report.

    Args:
        run_directory: Folder created by prepare_pattern_example (holds run.json
            and the code-written selection.json).
        record: The Test Run record; None reads run_directory/record.json, as
            saved by run (write_test_run_record) or agent-runtime-test-run
            --record-out (stdout captures in UTF-8 with or without BOM, or
            UTF-16 from Windows PowerShell redirection, are also read).
    Returns:
        pattern, transport, run_directory, status (the record's technical
        status), passed (all checks passed), checks (name, passed, detail),
        artifacts (paths to inspect), attempt_workspace (the retained Attempt
        folder row) and inspection (inspect_test_run_record's summary).
        Evidence: the executed Workflow and Module identity equal to the
        selection code wrote from the registration, file bytes on disk (only the allowed
        file changed, nothing added; complete saved MCP result), the record's
        Inspection tool-call log, an independent host rerun of the code check
        and the MCP fixture's audit of what it returned for exactly this task's
        arguments. The final output must agree with these facts (artifact names
        and locations, changed files, command, pass state); no model-restated
        hash or nonce is required or trusted.
    Effects:
        Reads the run folder, re-runs check_pricing.py for code_change and
        writes check.json. Changes no other file and starts no model.
    """
    from ..inspection.inspection_test_run_reading import inspect_test_run_record
    from ..registry.registry_definition_selection import read_definition_selection

    run_directory = Path(run_directory)
    manifest = _read_json(run_directory / "run.json")
    _require_pattern(manifest.get("pattern"))
    record_path = Path(manifest.get("record_path") or run_directory / "record.json")
    if record is None:
        record = _read_json(record_path)
    inspection = inspect_test_run_record(record)
    checks = _Checks()
    calls = _tool_calls(record)
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    checks.add("execution_completed", record.get("status") == "completed",
               record.get("failure_detail") or record.get("status") or inspection.get("error"))
    executed = {key: record.get(key) for key in manifest["registered"]}
    if manifest.get("selection_path") is not None:
        selection = read_definition_selection(Path(manifest["selection_path"]))
        selected = {key: getattr(selection, key) for key in manifest["registered"]}
    else:
        # runtime_pattern_example_v1 folders (0.2.0.dev12) recorded the readback refs/hashes in run.json only.
        selected = manifest["registered"]
    checks.add("executed_the_registered_definition", executed == selected == manifest["registered"],
               f"executed {executed}, selection {selected}")
    row = _attempt_row(record)
    checks.add("attempt_folder_retained", row.get("archive_path") and Path(row["archive_path"]).is_file(),
               row.get("workspace_path"))
    artifacts = _PATTERN_CHECKS[manifest["pattern"]](checks, manifest, record, calls, output)
    result = {"pattern": manifest["pattern"], "transport": manifest["transport"],
              "run_directory": str(run_directory), "status": record.get("status"),
              "passed": all(row["passed"] for row in checks.rows), "checks": checks.rows,
              "output": output, "artifacts": artifacts, "attempt_workspace": row,
              "inspection": {key: inspection.get(key) for key in ("outcome", "persistence", "durable_history",
                                                                 "attempts")},
              "record": str(record_path)}
    _write_json(run_directory / "check.json", result)
    return result


def build_parser():
    """Return the prepare/run/check argument grammar."""
    parser = argparse.ArgumentParser(
        prog="python -m " + _MODULE,
        description="Run, or prepare and check, one of the four ordinary Runtime usage-pattern examples or the "
                    "native_project example (claude_cli or codex_cli). run = prepare + run_local_workflow_test + "
                    "check. Each run uses a new folder under --root.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("--pattern", required=True, choices=EXAMPLE_PATTERNS)
        command.add_argument("--transport", required=True, choices=TRANSPORTS,
                             help="The CLI to run; never switched automatically")
        command.add_argument("--root", required=True, type=Path,
                             help="Example root; each run creates a new subfolder (use an ordinary user folder)")
        command.add_argument("--cli-path", type=Path, help="Native claude.exe/codex.exe/agy.exe (default: PATH lookup)")
        command.add_argument("--model", help="Model for this call (required for codex_cli and antigravity_cli)")
        command.add_argument("--effort", help="Effort for this call (required for codex_cli and antigravity_cli)")
        command.add_argument("--run-timeout-seconds", type=int, help="Outer time limit for this call (default 1200)")
    check = commands.add_parser("check")
    check.add_argument("--run-dir", required=True, type=Path, help="Folder created by prepare or run")
    check.add_argument("--record", type=Path, help="Saved Test Run JSON (default RUN_DIR/record.json)")
    return parser


def main(argv=None) -> int:
    """Print JSON on stdout. Exit 0: every check passed (or prepare finished); 1: a check failed or an error."""
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "check":
            record = None if args.record is None else _read_json(args.record)
            result = check_pattern_example(args.run_dir, record)
        else:
            options = dict(cli_path=args.cli_path, model_id=args.model, reasoning_profile=args.effort,
                           run_timeout_seconds=args.run_timeout_seconds)
            entry = prepare_pattern_example if args.command == "prepare" else run_pattern_example
            result = entry(args.root, args.pattern, args.transport, **options)
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "example interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command == "prepare" or result["passed"] else 1


__all__ = ["PATTERN_NAMES", "EXAMPLE_PATTERNS", "NATIVE_PROJECT_PATTERN", "TRANSPORTS", "ANTIGRAVITY_PATTERNS", "EXAMPLE_VERSION", "MCP_CONNECTION", "MCP_TOOL",
           "build_pattern_workflow", "module_id_for", "pattern_source", "pattern_requirements",
           "write_pattern_source", "prepare_pattern_example", "run_pattern_example", "check_pattern_example", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
