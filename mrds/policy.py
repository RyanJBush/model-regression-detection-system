"""Gate policy: loaded from a JSON file, tolerance parsed as an exact rational."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .errors import Issue

REQUIRED = ("policy_version", "method", "metric", "k", "absolute_tolerance")
OPTIONAL = ("note",)
_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")


@dataclass(frozen=True)
class Policy:
    policy_version: str
    method: str
    metric: str
    k: int
    tolerance: Fraction
    tolerance_text: str
    note: str | None
    sha256: str

    def as_dict(self) -> dict:
        d = {
            "policy_version": self.policy_version,
            "method": self.method,
            "metric": self.metric,
            "k": self.k,
            "absolute_tolerance": self.tolerance_text,
        }
        if self.note is not None:
            d["note"] = self.note
        d["sha256"] = self.sha256
        return d


def load_policy(path) -> tuple[Policy | None, list[Issue]]:
    def bad(msg):
        return None, [Issue("invalid_policy", "policy", msg)]

    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return bad(f"cannot read policy file {path}: {exc.strerror or exc}")
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        return bad(f"policy file is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        return bad("policy must be a JSON object")
    missing = [k for k in REQUIRED if k not in doc]
    extra = sorted(set(doc) - set(REQUIRED) - set(OPTIONAL))
    if missing or extra:
        return bad(f"policy keys: missing {missing}, unexpected {extra}")
    if not isinstance(doc["policy_version"], str) or not doc["policy_version"]:
        return bad("policy_version must be a non-empty string")
    if doc["method"] != "bm25":
        return bad(f"unsupported method {doc['method']!r}; only 'bm25' is implemented")
    if doc["metric"] != "macro_recall":
        return bad(f"unsupported metric {doc['metric']!r}; only 'macro_recall' is implemented")
    if type(doc["k"]) is not int or doc["k"] < 1:
        return bad("k must be a positive integer")
    tol = doc["absolute_tolerance"]
    if not isinstance(tol, str) or not _DECIMAL.match(tol):
        return bad("absolute_tolerance must be a non-negative decimal string such as \"0.05\" (not a JSON number)")
    note = doc.get("note")
    if note is not None and not isinstance(note, str):
        return bad("note must be a string")
    return (
        Policy(
            policy_version=doc["policy_version"],
            method=doc["method"],
            metric=doc["metric"],
            k=doc["k"],
            tolerance=Fraction(tol),
            tolerance_text=tol,
            note=note,
            sha256=hashlib.sha256(raw).hexdigest(),
        ),
        [],
    )


def _no_constant(name):
    raise ValueError(f"non-standard JSON constant {name!r}")
