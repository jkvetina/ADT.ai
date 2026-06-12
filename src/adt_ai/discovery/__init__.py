"""Read-only SELECT discovery interface for the target database."""

from __future__ import annotations

from adt_ai.discovery.validator import (
    DiscoveryValidationError,
    validate_select_only,
)

__all__ = ["DiscoveryValidationError", "validate_select_only"]
