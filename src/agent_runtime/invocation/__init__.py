"""Model and tool invocation implementations.

Provider SDK modules are intentionally not imported here. The standalone core
must remain importable without optional provider dependencies; a registered
Execution Profile loads its exact provider implementation explicitly.
"""

__all__: list[str] = []
