"""Resolve model selection and synchronous run budget from four layers before Profile preparation.

Layer 4 is this call's explicit arguments, layer 3 the Workflow parameter file,
layer 2 the workspace parameter file and layer 1 the Runtime default applied by
``_execution_profile_for_requirements``. Each parameter independently takes the
highest layer that wrote a value. Parameter files hold execution parameters only;
they never select a definition version, and they sit outside the registration
folders so registration loading never reads them as versions.

    root/.runtime/execution_parameters/workspace.json
    root/.runtime/execution_parameters/workflows/<workflow_id>.json

File format: ``{"schema_version": "runtime_execution_parameters_v1",
"transport_kind": ..., "model_id": ..., "reasoning_profile": ...}``. The v2 format
additionally accepts integer run_timeout_seconds; v1 keeps its original fields. Each
parameter optional. A missing file means that layer wrote nothing. A present
file that is not a regular file, is not valid JSON, has another schema version,
unknown keys (including version or definition-target keys) or empty values
fails preparation; resolution never skips to a lower layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat

from ..foundation.foundation_contract_validation import validate_snake_case_name


LEGACY_PARAMETER_FILE_FORMAT = "runtime_execution_parameters_v1"
PARAMETER_FILE_FORMAT = "runtime_execution_parameters_v2"
DEFAULT_RUN_TIMEOUT_SECONDS = 1200
# One owner for the supported parameter set and its accepted Python/JSON types.
PARAMETER_TYPES = {"transport_kind": str, "model_id": str, "reasoning_profile": str,
                   "run_timeout_seconds": int}
PARAMETER_NAMES = tuple(PARAMETER_TYPES)


def _validate_parameter(name, value):
    expected = PARAMETER_TYPES[name]
    if type(value) is not expected:
        raise ValueError(f"Execution parameter {name} must be {expected.__name__}")
    if expected is str and not value.strip():
        raise ValueError(f"Execution parameter {name} must be a non-empty string")
    if name == "run_timeout_seconds" and not 1 <= value <= 86400:
        raise ValueError("run_timeout_seconds must be between 1 and 86400")
SOURCE_CALL = "call"
SOURCE_WORKFLOW_FILE = "workflow_file"
SOURCE_WORKSPACE_FILE = "workspace_file"
SOURCE_RUNTIME_DEFAULT = "runtime_default"
_LAYER_ORDER = (SOURCE_CALL, SOURCE_WORKFLOW_FILE, SOURCE_WORKSPACE_FILE, SOURCE_RUNTIME_DEFAULT)
_DIRECTORY = (".runtime", "execution_parameters")


@dataclass(frozen=True)
class ExecutionParameterSource:
    """Where one resolved parameter came from; file fields are None for call/default."""

    layer: str
    file: str | None = None
    file_sha256: str | None = None

    def as_record(self) -> dict[str, str | None]:
        return {"layer": self.layer, "file": self.file, "file_sha256": self.file_sha256}


@dataclass(frozen=True)
class ResolvedExecutionParameters:
    """Values passed to Profile preparation; None leaves the Runtime default in place."""

    transport_kind: str | None
    model_id: str | None
    reasoning_profile: str | None
    sources: tuple[tuple[str, ExecutionParameterSource], ...]
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS

    def source(self, name: str) -> ExecutionParameterSource:
        return dict(self.sources)[name]

    def as_record(self) -> dict[str, dict[str, str | None]]:
        """JSON-compatible provenance for execution records, never Profile payload."""
        return {name: source.as_record() for name, source in self.sources}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate execution parameter key: {key}")
        result[key] = value
    return result


def _read_parameter_file(root: Path, parts: tuple[str, ...]) -> tuple[dict, str, str] | None:
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Execution parameter path is a symlink: {path}")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise ValueError(f"Execution parameter file is not a regular file: {path}")
    body = path.read_bytes()
    try:
        document = json.loads(body, object_pairs_hook=_unique_object)
    except ValueError as exc:
        raise ValueError(f"Execution parameter file is not valid JSON: {path}: {exc}") from exc
    if type(document) is not dict or document.get("schema_version") not in {PARAMETER_FILE_FORMAT, LEGACY_PARAMETER_FILE_FORMAT}:
        raise ValueError(f"Execution parameter file requires schema_version {PARAMETER_FILE_FORMAT}: {path}")
    allowed = set(PARAMETER_NAMES)
    if document["schema_version"] == LEGACY_PARAMETER_FILE_FORMAT:
        allowed.remove("run_timeout_seconds")
    unknown = sorted(set(document) - {"schema_version", *allowed})
    if unknown:
        raise ValueError(
            f"Execution parameter file accepts only {', '.join(PARAMETER_NAMES)}; "
            f"definition versions and targets are chosen per call: {path}: {unknown}")
    values = {}
    for name in PARAMETER_NAMES:
        if name in document:
            value = document[name]
            _validate_parameter(name, value)
            values[name] = value
    return values, "/".join(parts), hashlib.sha256(body).hexdigest()


def resolve_execution_parameters(
    root: Path, workflow_subject_id: str, *, transport_kind: str | None = None,
    model_id: str | None = None, reasoning_profile: str | None = None,
    run_timeout_seconds: int | None = None,
) -> ResolvedExecutionParameters:
    """Take each parameter from this call, the Workflow file, the workspace file or the default.

    Args:
        root: Host root; only the two parameter files below .runtime are read.
        workflow_subject_id: Workflow ID locating the layer-3 file. For a PG
            definition this is the loaded Workflow's own ID.
        run_timeout_seconds: Total synchronous work budget, 1..86400 seconds;
            None resolves lower layers and finally the 1200-second default.
        transport_kind, model_id, reasoning_profile: This call's explicit values;
            None means this call did not choose that parameter.
    Returns:
        The values to pass to Profile preparation and each value's source layer,
        file path relative to root and file SHA-256. A value left as None is
        filled by the Runtime default during Profile preparation.
    Raises:
        ValueError: Invalid parameter file, or a model/effort written in a
            layer whose effective transport differs from the final transport.
            The effective transport of a layer is the transport resolved from
            that layer and the layers below it, ending at the Runtime default.
            A model/effort left to the Runtime default is checked by Profile
            preparation, which applies Claude defaults only to claude_cli.
    Effects:
        Reads at most two files and writes nothing. Does not choose a version,
        open provider resources or validate Module capabilities; Profile
        preparation applies the same capability checks as for explicit values.
    """
    from .execution_local_invocation import _RUNTIME_DEFAULT_TRANSPORT

    validate_snake_case_name("workflow_subject_id", workflow_subject_id)
    root = Path(root).resolve()
    files = {
        SOURCE_WORKFLOW_FILE: _read_parameter_file(root, (*_DIRECTORY, "workflows", workflow_subject_id + ".json")),
        SOURCE_WORKSPACE_FILE: _read_parameter_file(root, (*_DIRECTORY, "workspace.json")),
    }
    explicit = {"transport_kind": transport_kind, "model_id": model_id, "reasoning_profile": reasoning_profile,
                "run_timeout_seconds": run_timeout_seconds}
    for name, value in explicit.items():
        if value is not None:
            _validate_parameter(name, value)

    def written(layer: str, name: str):
        if layer == SOURCE_CALL:
            return explicit[name]
        loaded = files.get(layer)
        return None if loaded is None else loaded[0].get(name)

    def effective_transport(from_layer: str) -> str:
        for layer in _LAYER_ORDER[_LAYER_ORDER.index(from_layer):]:
            if layer == SOURCE_RUNTIME_DEFAULT:
                return _RUNTIME_DEFAULT_TRANSPORT
            value = written(layer, "transport_kind")
            if value is not None:
                return value
        raise AssertionError("unreachable")

    values, sources = {}, []
    for name in PARAMETER_NAMES:
        for layer in _LAYER_ORDER:
            value = None if layer == SOURCE_RUNTIME_DEFAULT else written(layer, name)
            if value is not None or layer == SOURCE_RUNTIME_DEFAULT:
                loaded = files.get(layer)
                values[name] = DEFAULT_RUN_TIMEOUT_SECONDS if name == "run_timeout_seconds" and value is None else value
                sources.append((name, ExecutionParameterSource(
                    layer, None if loaded is None else loaded[1], None if loaded is None else loaded[2])))
                break
    layers = dict((name, source.layer) for name, source in sources)
    final_transport = effective_transport(layers["transport_kind"])
    # A Runtime-default model/effort is checked by Profile preparation itself:
    # Claude defaults apply only to claude_cli and other transports reject them.
    mixed = [name for name in ("model_id", "reasoning_profile")
             if layers[name] != SOURCE_RUNTIME_DEFAULT
             and effective_transport(layers[name]) != final_transport]
    if mixed:
        raise ValueError(
            "Execution parameters mix transports: transport_kind resolves to "
            f"{final_transport} from {layers['transport_kind']}, but "
            + ", ".join(f"{name} comes from {layers[name]} whose transport is {effective_transport(layers[name])}"
                        for name in mixed)
            + "; write model_id and reasoning_profile in the same layer as transport_kind or above it")
    return ResolvedExecutionParameters(values["transport_kind"], values["model_id"],
                                       values["reasoning_profile"], tuple(sources), values["run_timeout_seconds"])
