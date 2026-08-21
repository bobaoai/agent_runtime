from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.registry.registry_authoring_inventory_loading import (
    load_runtime_authoring_inventory,
)
from agent_runtime.registry.registry_authoring_release_building import (
    RuntimeReleaseBuildResult,
    build_runtime_release_set,
)
from agent_runtime.registry.registry_authoring_release_submission import (
    RuntimeReleaseRegistrationReport,
    register_runtime_release_set,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = (
    "examples/runtime_authoring_reference/runtime_authoring_inventory.json"
)
TIME = "2026-08-19T23:00:00Z"


def _build() -> RuntimeReleaseBuildResult:
    source_set = load_runtime_authoring_inventory(REPO_ROOT, INVENTORY_PATH)
    return build_runtime_release_set(source_set)


def test_register_release_set_and_identical_replay() -> None:
    build_result = _build()
    registry = RuntimeReleaseRegistry(recording_clock=lambda: TIME)

    first = register_runtime_release_set(registry, build_result)
    second = register_runtime_release_set(registry, build_result)

    assert type(first) is RuntimeReleaseRegistrationReport
    assert second == first
    assert first.registered_release_identities == build_result.release_identities()
    assert len(first.admission_intent_sha256s) == len(
        build_result.plugin.release_bundle.admission_intents
    )
    assert first.root_workflow_identities == (
        (
            build_result.root_workflows[0].release_ref,
            build_result.root_workflows[0].release_sha256,
        ),
    )


def test_in_memory_registry_satisfies_positional_store_protocol() -> None:
    registry = RuntimeReleaseRegistry(recording_clock=lambda: TIME)
    bundle = RuntimeReleaseBundle(
        schema_assets=_build().plugin.release_bundle.schema_assets[:1]
    )

    returned = registry.register_bundle(bundle)

    assert returned is registry


def test_registration_report_is_content_path_time_and_credential_free() -> None:
    report = register_runtime_release_set(
        RuntimeReleaseRegistry(recording_clock=lambda: TIME),
        _build(),
    )
    payload = report.as_dict()
    text = repr(payload).lower()

    def keys(value):
        if isinstance(value, dict):
            return set(value) | {
                item_key
                for item in value.values()
                for item_key in keys(item)
            }
        if isinstance(value, list):
            return {
                item_key for item in value for item_key in keys(item)
            }
        return set()

    assert set(payload) == {
        "inventory_ref",
        "inventory_sha256",
        "plugin_id",
        "plugin_version",
        "registered_release_identities",
        "root_workflow_identities",
        "admission_intent_sha256s",
    }
    report_keys = keys(payload)
    for forbidden in (
        "prompt_body",
        "schema_document",
        "path",
        "credential",
        "entitlement",
        "recorded_at_utc",
        "database_url",
    ):
        assert forbidden not in report_keys
    assert str(REPO_ROOT).lower() not in text
    assert TIME.lower() not in text


def test_store_failure_propagates_without_report() -> None:
    class FailedStore:
        def register_bundle(self, _candidate_bundle, /):
            raise RuntimeError("store unavailable")

    with pytest.raises(RuntimeError, match="store unavailable"):
        register_runtime_release_set(FailedStore(), _build())


def test_store_must_return_registry() -> None:
    class InvalidStore:
        def register_bundle(self, _candidate_bundle, /):
            return None

    with pytest.raises(TypeError, match="must return RuntimeReleaseRegistry"):
        register_runtime_release_set(InvalidStore(), _build())


def test_post_registration_exact_release_closure_is_verified() -> None:
    class WrongRegistryStore:
        def register_bundle(self, _candidate_bundle, /):
            return RuntimeReleaseRegistry()

    with pytest.raises(RuntimeError, match="closure differs"):
        register_runtime_release_set(WrongRegistryStore(), _build())


def test_submission_has_no_filesystem_or_schema_provisioning_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_result = _build()

    class Store:
        def __init__(self) -> None:
            self.registry = RuntimeReleaseRegistry(recording_clock=lambda: TIME)
            self.register_calls = 0
            self.create_calls = 0

        def register_bundle(self, candidate_bundle, /):
            self.register_calls += 1
            return self.registry.register_bundle(candidate_bundle)

        def create_schema(self):
            self.create_calls += 1
            raise AssertionError("submission attempted schema provisioning")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("submission attempted filesystem access")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    store = Store()

    register_runtime_release_set(store, build_result)

    assert store.register_calls == 1
    assert store.create_calls == 0
