"""The gate decision. Exact rational arithmetic only; input must already be validated."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .errors import PASS, REGRESSION
from .policy import Policy
from .validate import Run


@dataclass(frozen=True)
class QueryDelta:
    query_id: str
    baseline: Fraction
    candidate: Fraction
    delta: Fraction


@dataclass(frozen=True)
class GateResult:
    decision: str
    reason: str
    baseline_mean: Fraction
    candidate_mean: Fraction
    mean_delta: Fraction
    threshold: Fraction  # block when mean_delta < threshold (= -tolerance)
    deltas: tuple  # all scored queries, sorted by query_id
    largest_drops: tuple  # negative deltas only, most negative first, ties by query_id
    n_total: int
    unscored: tuple  # (query_id, status) in query_id order


def decide(mean_delta: Fraction, tolerance: Fraction) -> str:
    """Block only when the mean delta is strictly below -tolerance; the exact boundary passes."""
    return REGRESSION if mean_delta < -tolerance else PASS


def evaluate(base: Run, cand: Run, policy: Policy, top_n: int = 5) -> GateResult:
    k = policy.k
    if set(base.results) != set(cand.results):
        raise ValueError("evaluate() requires validated runs with identical query sets")
    deltas, unscored = [], []
    for qid in sorted(base.results):
        b, c = base.results[qid], cand.results[qid]
        if (b.recalls[k] is None) != (c.recalls[k] is None):
            raise ValueError(f"{qid}: scored on one side only; validation should have rejected this")
        if b.recalls[k] is None:
            unscored.append((qid, b.status))
            continue
        deltas.append(QueryDelta(qid, b.recalls[k], c.recalls[k], c.recalls[k] - b.recalls[k]))
    if not deltas:
        raise ValueError("empty scored set; validation should have rejected this")
    n = len(deltas)
    mean_delta = sum((d.delta for d in deltas), Fraction(0)) / n
    base_mean = sum((d.baseline for d in deltas), Fraction(0)) / n
    cand_mean = sum((d.candidate for d in deltas), Fraction(0)) / n
    decision = decide(mean_delta, policy.tolerance)
    drops = sorted((d for d in deltas if d.delta < 0), key=lambda d: (d.delta, d.query_id))[:top_n]
    return GateResult(
        decision=decision,
        reason=_reason(decision, mean_delta, policy),
        baseline_mean=base_mean,
        candidate_mean=cand_mean,
        mean_delta=mean_delta,
        threshold=-policy.tolerance,
        deltas=tuple(deltas),
        largest_drops=tuple(drops),
        n_total=len(base.results),
        unscored=tuple(unscored),
    )


def _reason(decision, mean_delta, policy):
    tol = policy.tolerance
    head = f"mean paired {policy.metric} delta at k={policy.k} is {mean_delta}"
    if decision == REGRESSION:
        return f"{head}, strictly below -{policy.tolerance_text} (policy {policy.policy_version})."
    if mean_delta == -tol:
        return f"{head}, exactly -{policy.tolerance_text}; the boundary passes (policy {policy.policy_version})."
    return f"{head}, not below -{policy.tolerance_text} (policy {policy.policy_version})."
