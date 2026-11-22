"""Validators that stand between the agent and the approval queue."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Errors block a proposal; warnings are shown to the owner."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: ValidationResult) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
