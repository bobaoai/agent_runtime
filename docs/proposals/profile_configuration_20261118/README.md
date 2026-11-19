# Existing preset evidence readback

`evidence_readback.json` records a read-only inspection on 2026-11-18 of thirteen existing Windows example runs. It retains each run's original location, file SHA-256 values, registered definition identity and requirements, selected model and effort, actual result, and individual checks. No example was re-executed to produce this annex.

The supported configuration is defined in `designDoc/agent_runtime_08_agent_execution_adapter_contract.md` sections 3.4.5 and 3.4.6. Section 6.1 reports the bounded sample matrix. All thirteen records use Runtime 0.2.0.dev12; current dev14 preset values were inspected from source. The evidence does not claim a dev14 full-matrix rerun.

Successful runs and earlier failed runs remain separate. File review uses the saved provider_managed/max_attempts=1 preset definition. It is not inferred from the configuration of another registered Reviewer. MCP evidence uses synthetic stdio data and does not establish connectivity or permissions for a real external data source.
