"""Build and execute explicit Agent Runtime Registry schema migrations."""

from __future__ import annotations

import argparse
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from agent_runtime.contracts.registry_release_definition import (
    ExecutionProfileRelease,
    LegacyReleaseAdmissionRecord,
    ModuleRelease,
    PromptBundleRelease,
    PromptComponentRelease,
    SchemaAssetRelease,
    WorkflowRelease,
)
from agent_runtime.registry import (
    PostgresRuntimeReleaseStore,
    RegistryMigrationCandidateSet,
    RegistrySchemaMigrationPlan,
    RuntimeReleaseBundle,
    compile_registry_migration_candidate_set,
)
from agent_runtime.registry.registry_postgres_persistence import (
    _registry_schema_release_sha256,
    _schema_structure_sha256,
    _source_row_identity_sha256,
)


_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_V1_RELEASE_TABLES = (
    "schema_asset_release",
    "prompt_component_release",
    "prompt_bundle_release",
    "execution_profile_release",
    "runtime_module_release",
    "workflow_release",
)
_SOURCE_DECODERS = {
    "schema_asset_release": ("schema_assets", SchemaAssetRelease.from_dict),
    "prompt_component_release": (
        "prompt_components",
        PromptComponentRelease.from_dict,
    ),
    "prompt_bundle_release": ("prompt_bundles", PromptBundleRelease.from_dict),
    "execution_profile_release": (
        "execution_profiles",
        ExecutionProfileRelease.from_dict,
    ),
    "runtime_module_release": ("modules", ModuleRelease.from_dict),
    "workflow_release": ("workflows", WorkflowRelease.from_dict),
}
_UNCHANGED_TABLES = frozenset(
    {
        "schema_asset_release",
        "prompt_component_release",
        "prompt_bundle_release",
    }
)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_candidate_set(
    candidate_path: Path,
    *,
    expected_sha256: str,
) -> RegistryMigrationCandidateSet:
    """Load one hash-pinned path-free host candidate artifact."""

    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("candidate artifact SHA-256 is invalid")
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate artifact must be valid UTF-8 JSON") from exc
    if type(payload) is not dict:
        raise ValueError("candidate artifact must be one JSON object")
    actual_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("candidate artifact SHA-256 mismatch")
    return RegistryMigrationCandidateSet.from_dict(payload)


def _validate_schema_name(label: str, value: str) -> str:
    if type(value) is not str or _SCHEMA_NAME.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _read_source_snapshot(cursor: Any, source_schema: str) -> dict[str, Any]:
    release_rows: dict[str, list[dict[str, Any]]] = {}
    for table in _V1_RELEASE_TABLES:
        cursor.execute(
            f"SELECT release_ref, release_sha256, payload "
            f"FROM {source_schema}.{table} ORDER BY release_ref"
        )
        release_rows[table] = [
            {
                "release_ref": str(row[0]),
                "release_sha256": str(row[1]),
                "payload": row[2],
            }
            for row in cursor.fetchall()
        ]
    cursor.execute(
        f"SELECT admission_id, admission_sha256, release_ref, "
        f"release_sha256, payload FROM {source_schema}.release_admission "
        "ORDER BY admission_sequence"
    )
    admissions = [
        {
            "admission_id": str(row[0]),
            "admission_sha256": str(row[1]),
            "release_ref": str(row[2]),
            "release_sha256": str(row[3]),
            "payload": row[4],
        }
        for row in cursor.fetchall()
    ]
    cursor.execute(
        f"SELECT subject_kind, subject_id, release_ref, release_sha256 "
        f"FROM {source_schema}.active_release_pointer "
        "ORDER BY subject_kind, subject_id"
    )
    active_pointers = [
        {
            "subject_kind": str(row[0]),
            "subject_id": str(row[1]),
            "release_ref": str(row[2]),
            "release_sha256": str(row[3]),
        }
        for row in cursor.fetchall()
    ]
    return {
        "release_rows": release_rows,
        "admissions": admissions,
        "active_pointers": active_pointers,
    }


def _source_closure_matches_candidate(
    source: dict[str, Any],
    candidate_set: RegistryMigrationCandidateSet,
) -> None:
    decoded_release_identities: set[tuple[str, str]] = set()
    for table, rows in source["release_rows"].items():
        _, decoder = _SOURCE_DECODERS[table]
        for row in rows:
            record = decoder(row["payload"])
            record.validate()
            if (
                record.release_ref != row["release_ref"]
                or record.release_sha256 != row["release_sha256"]
            ):
                raise ValueError("predecessor release payload identity differs")
            decoded_release_identities.add(
                (record.release_ref, record.release_sha256)
            )

    for row in source["admissions"]:
        record = LegacyReleaseAdmissionRecord.from_dict(row["payload"])
        record.validate()
        if (
            record.admission_id != row["admission_id"]
            or record.admission_sha256 != row["admission_sha256"]
            or record.release_ref != row["release_ref"]
            or record.release_sha256 != row["release_sha256"]
        ):
            raise ValueError("predecessor admission payload identity differs")
        if (record.release_ref, record.release_sha256) not in (
            decoded_release_identities
        ):
            raise ValueError("predecessor admission release is unresolved")

    for row in source["active_pointers"]:
        if (row["release_ref"], row["release_sha256"]) not in (
            decoded_release_identities
        ):
            raise ValueError("predecessor active pointer release is unresolved")

    releases = {
        (table, row["release_ref"], row["release_sha256"])
        for table, rows in source["release_rows"].items()
        for row in rows
    }
    dispositions = {
        (
            item.source_table,
            item.source_release_ref,
            item.source_release_sha256,
        )
        for item in candidate_set.release_dispositions
    }
    if dispositions != releases:
        raise ValueError(
            "candidate set does not disposition every predecessor release"
        )
    admissions = {
        (row["admission_id"], row["admission_sha256"])
        for row in source["admissions"]
    }
    admission_dispositions = {
        (item.source_admission_id, item.source_admission_sha256)
        for item in candidate_set.admission_dispositions
    }
    if admission_dispositions != admissions:
        raise ValueError(
            "candidate set does not disposition every predecessor admission"
        )
    pointers = {
        (
            row["subject_kind"],
            row["subject_id"],
            row["release_ref"],
            row["release_sha256"],
        )
        for row in source["active_pointers"]
    }
    pointer_dispositions = {
        (
            item.subject_kind,
            item.subject_id,
            item.source_release_ref,
            item.source_release_sha256,
        )
        for item in candidate_set.active_pointer_dispositions
    }
    if pointer_dispositions != pointers:
        raise ValueError(
            "candidate set does not disposition every predecessor active pointer"
        )


def _unchanged_bundle(
    source: dict[str, Any],
    candidate_set: RegistryMigrationCandidateSet,
) -> RuntimeReleaseBundle:
    release_intents = {
        (item.source_table, item.source_release_ref): item
        for item in candidate_set.release_dispositions
    }
    values: dict[str, list[Any]] = {
        field.name: [] for field in fields(RuntimeReleaseBundle)
    }
    for table, rows in source["release_rows"].items():
        for row in rows:
            intent = release_intents[(table, row["release_ref"])]
            if intent.disposition != "unchanged":
                continue
            if table not in _UNCHANGED_TABLES:
                raise ValueError(
                    f"predecessor release family must be reissued: {table}"
                )
            field_name, decoder = _SOURCE_DECODERS[table]
            record = decoder(row["payload"])
            if (
                record.release_ref != row["release_ref"]
                or record.release_sha256 != row["release_sha256"]
            ):
                raise ValueError("unchanged predecessor release identity differs")
            values[field_name].append(record)

    return RuntimeReleaseBundle(
        **{
            field_name: tuple(records)
            for field_name, records in values.items()
        }
    )


def build_plan_from_snapshot(
    *,
    source_snapshot: dict[str, Any],
    candidate_set: RegistryMigrationCandidateSet,
    migration_id: str,
    source_schema: str,
    target_schema: str,
    source_structure_sha256: str,
    source_row_identity_sha256: str,
    planned_at_utc: str,
) -> RegistrySchemaMigrationPlan:
    """Compile one reproducible Plan from frozen source and host candidates."""

    _source_closure_matches_candidate(source_snapshot, candidate_set)
    compiled = compile_registry_migration_candidate_set(
        candidate_set,
        unchanged_bundle=_unchanged_bundle(source_snapshot, candidate_set),
    )
    return RegistrySchemaMigrationPlan.build(
        migration_id=migration_id,
        source_schema=source_schema,
        target_schema=target_schema,
        source_structure_sha256=source_structure_sha256,
        source_row_identity_sha256=source_row_identity_sha256,
        target_schema_release_ref="registry-schema:agent_runtime_control@v2",
        target_schema_release_sha256=_registry_schema_release_sha256(),
        target_bundle=compiled.target_bundle,
        release_dispositions=compiled.release_dispositions,
        admission_dispositions=compiled.admission_dispositions,
        active_pointer_dispositions=compiled.active_pointer_dispositions,
        planned_at_utc=planned_at_utc,
    )


def _database_url(environment_variable: str) -> str:
    database_url = os.environ.get(environment_variable)
    if not database_url:
        raise ValueError(
            f"database URL environment variable is absent: {environment_variable}"
        )
    return database_url


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Registry migration tooling requires agent-runtime-core[postgres]"
        ) from exc
    return psycopg.connect(
        database_url,
        options="-c client_encoding=UTF8 -c timezone=UTC",
    )


def plan_command(arguments: argparse.Namespace) -> RegistrySchemaMigrationPlan:
    source_schema = _validate_schema_name("source schema", arguments.source_schema)
    target_schema = _validate_schema_name("target schema", arguments.target_schema)
    candidate_set = load_candidate_set(
        Path(arguments.candidate),
        expected_sha256=arguments.candidate_sha256,
    )
    connection = _connect(_database_url(arguments.database_url_env))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            source = _read_source_snapshot(cursor, source_schema)
            plan = build_plan_from_snapshot(
                source_snapshot=source,
                candidate_set=candidate_set,
                migration_id=arguments.migration_id,
                source_schema=source_schema,
                target_schema=target_schema,
                source_structure_sha256=_schema_structure_sha256(
                    cursor,
                    source_schema,
                ),
                source_row_identity_sha256=_source_row_identity_sha256(
                    cursor,
                    source_schema,
                ),
                planned_at_utc=arguments.planned_at_utc,
            )
        finally:
            cursor.close()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    Path(arguments.output).write_text(
        json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return plan


def lifecycle_command(arguments: argparse.Namespace) -> None:
    payload = json.loads(Path(arguments.plan).read_text(encoding="utf-8"))
    plan = RegistrySchemaMigrationPlan.from_dict(payload)
    store = PostgresRuntimeReleaseStore.from_dsn(
        _database_url(arguments.database_url_env),
        schema=plan.target_schema,
    )
    if arguments.command == "migrate":
        store.migrate_schema(plan)
    elif arguments.command == "resume":
        store.resume_schema_migration(plan)
    else:
        store.abort_schema_migration(plan)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url-env",
        default="AGENT_RUNTIME_DATABASE_URL",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--candidate", required=True)
    plan.add_argument("--candidate-sha256", required=True)
    plan.add_argument("--migration-id", required=True)
    plan.add_argument("--source-schema", required=True)
    plan.add_argument("--target-schema", required=True)
    plan.add_argument("--planned-at-utc", required=True)
    plan.add_argument("--output", required=True)
    for command in ("migrate", "resume", "abort"):
        lifecycle = commands.add_parser(command)
        lifecycle.add_argument("--plan", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "plan":
        result = plan_command(arguments)
        print(result.plan_sha256)
    else:
        lifecycle_command(arguments)
        print(arguments.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
