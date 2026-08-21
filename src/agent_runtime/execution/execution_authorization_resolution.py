"""Resolve Product Authorization status and decisions for Runtime execution."""

from __future__ import annotations

from typing import Protocol

from ..contracts.execution_authorization_definition import (
    OperationAuthorizationQuery,
    ProductAuthorizationContextStatus,
    ProductOperationDecision,
)


class ProductAuthorizationContextClient(Protocol):
    """Validate one opaque execution context without exposing Product policy."""

    def validate_execution_context(
        self,
        context_id: str,
        tenant_id: str,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        """Return the Product-owned current status for one execution context."""

        ...


class ProductOperationAuthorizationClient(
    ProductAuthorizationContextClient,
    Protocol,
):
    """Authorize one concrete operation through an opaque host projection."""

    def authorize_operation(
        self,
        query: OperationAuthorizationQuery,
    ) -> ProductOperationDecision:
        """Authorize one concrete protected operation query."""

        ...


__all__ = [
    "ProductAuthorizationContextClient",
    "ProductOperationAuthorizationClient",
]
