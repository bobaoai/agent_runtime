# Slice 1 Real Downstream Gate

Status: failing release evidence carried into Slice 2.

This record corrects the earlier Slice 1 evidence boundary. Static consumer
inventory and Runtime-local conformance are necessary, but they do not prove
that an actual consuming product can import and assemble the candidate Runtime.

## Permanent release gate

Every Runtime public-surface or Registry-boundary change must pass all three
consumer checks against at least one real registered host product:

1. **Static closure**: rebuild the symbol-level consumer manifest and reject
   unreviewed drift.
2. **Import closure**: import the host's declared Runtime integration roots
   using the candidate Runtime installed in the host's real Python
   environment.
3. **Composition closure**: run the host-owned registration/composition smoke
   tests that assemble real Runtime releases. The host owns the test logic and
   repository layout; Runtime release evidence records only the exact host,
   candidate Runtime, command, and result.

Runtime Conformance must not learn a host repository layout, execute commands
stored in an untrusted manifest, or replace the host-owned composition test
with another Runtime-local synthetic fixture.

## 2026-08-17 trading_platform result

Runtime candidate:

- repository: `/Users/bokanbao/Documents/GitHub/the_agent_runtime`
- commit: `1152cb4`
- imported source: `/Users/bokanbao/Documents/GitHub/the_agent_runtime/src/agent_runtime/__init__.py`

Real host:

- consumer: `trading_platform`
- commit: `bdaf1391453a7ab3ad9ec0e8c8a45e026cee2f50`
- environment: `/Users/bokanbao/Documents/GitHub/trading_platform/.venv`

Command:

```text
./.venv/bin/python -m pytest -q \
  tests/test_runtime_host_release_registration.py \
  tests/test_split_workflow_driver_smoke.py
```

Observed result:

```text
7 failed, 5 passed
ModuleNotFoundError:
No module named 'agent_runtime.registry.registry_module_exporting'
```

The failure occurs while importing the real host Module-release plugins, before
PostgreSQL access or provider invocation. It proves that the host still consumes
a retired Registry authoring interface and that static Slice 1 evidence did not
establish executable downstream closure.

## Disposition

- Slice 1 Runtime-local architecture conformance remains valid.
- Slice 1 does not yet have passing real downstream release evidence.
- The failed gate is not repaired by a compatibility shim.
- Slice 2 owns the root correction: Runtime accepts repository-independent
  structured release candidates, while `trading_platform` owns repository and
  Skill authoring discovery plus conversion into those candidates.
- The same command must turn green before Slice 2 can be accepted.
