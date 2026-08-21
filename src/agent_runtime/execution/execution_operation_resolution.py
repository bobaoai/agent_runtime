"""Resolve Runtime-owned authority for one protected operation."""

from __future__ import annotations

from typing import Protocol

from ..contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextBinding,
    ProtectedOperationIntent,
)


class RuntimeProtectedOperationClient(Protocol):
    """Resolve Runtime-owned authority records by exact reference and hash."""

    def resolve_protected_operation_intent(
        self,
        intent_ref: str,
        intent_sha256: str,
    ) -> ProtectedOperationIntent:
        """Resolve one exact committed protected-operation intent."""

        ...

    def resolve_execution_authorization_binding(
        self,
        binding_ref: str,
        binding_sha256: str,
    ) -> ExecutionAuthorizationContextBinding:
        """Resolve one exact execution authorization binding."""

        ...


__all__ = ["RuntimeProtectedOperationClient"]
