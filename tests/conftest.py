import copy
import json
from pathlib import Path

import pytest

from mrds.cli import main

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "synthetic-v1"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def run_cli(tmp_path):
    """run_cli(baseline_doc, candidate_doc, policy=None, extra=()) -> (exit_code, report_dict)."""
    counter = [0]

    def _run(baseline, candidate, policy=None, extra=()):
        counter[0] += 1
        d = tmp_path / f"case{counter[0]}"
        d.mkdir()
        paths = {}
        for name, doc in (("baseline", baseline), ("candidate", candidate)):
            paths[name] = d / f"{name}.json"
            paths[name].write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
        pol = d / "policy.json"
        pol.write_text(json.dumps(policy if policy is not None else load("policy.json")), encoding="utf-8")
        code = main(["--baseline", str(paths["baseline"]), "--candidate", str(paths["candidate"]),
                     "--policy", str(pol), "--out-dir", str(d / "out"), *extra])
        report = json.loads((d / "out" / "report.json").read_text(encoding="utf-8"))
        return code, report

    return _run


@pytest.fixture
def base():
    return load("baseline.run.json")


@pytest.fixture
def cand():
    return copy.deepcopy(load("candidate_boundary.run.json"))


def codes(report):
    return {e["code"] for e in report["errors"]}


def row(doc, qid):
    return next(r for r in doc["results"]["bm25"]["queries"] if r["query_id"] == qid)
