from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CommandOutcome(Generic[T]):
    """A persisted resource plus the result of scheduling its follow-up work."""

    resource: T
    result_code: str = "SUCCEEDED"

    @property
    def is_partial(self) -> bool:
        return self.result_code not in {"SUCCEEDED", "ALREADY_COMPLETED"}
