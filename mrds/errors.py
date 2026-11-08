"""Shared constants and the Issue record used for every validation failure."""

from __future__ import annotations

from dataclasses import dataclass

PASS = "pass"
REGRESSION = "regression_detected"
INVALID = "comparison_invalid"

EXIT_CODES = {PASS: 0, REGRESSION: 1, INVALID: 2}


@dataclass(frozen=True)
class Issue:
    """One reason a comparison is invalid. `side` is baseline, candidate, pair, policy or cli."""

    code: str
    side: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "side": self.side, "message": self.message}
