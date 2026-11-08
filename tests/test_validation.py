"""Duplicate IDs, mismatched dataset/cutoff, null vs missing, and other reasons to reject before aggregating."""

import copy
import json

import pytest

from conftest import codes, load, row


def invalid(run_cli, base, cand, code, **kw):
    exit_code, rep = run_cli(base, cand, **kw)
    assert exit_code == 2 and rep["decision"] == "comparison_invalid", rep["reason"]
    assert rep["aggregate"] is None and rep["per_query"] is None
    assert code in codes(rep), rep["errors"]
    return rep


# ----------------------------------------------------------------------------- duplicate IDs
def _dup_doc(d):
    d["dataset"]["documents"].append(copy.deepcopy(d["dataset"]["documents"][0]))


def _dup_query(d):
    d["dataset"]["queries"].append(copy.deepcopy(d["dataset"]["queries"][0]))


def _dup_judgment(d):
    d["dataset"]["judgments"].append(list(d["dataset"]["judgments"][0]))


def _dup_result_row(d):
    d["results"]["bm25"]["queries"].append(copy.deepcopy(row(d, "q02")))


def _dup_ranking_doc(d):
    r = row(d, "q02")
    r["ranking"][2]["doc_id"] = r["ranking"][1]["doc_id"]


def _dup_ks(d):
    d["config"]["ks"] = [3, 3]


def _dup_methods(d):
    d["config"]["methods"] = ["bm25", "bm25"]


def _dup_file_name(d):
    d["dataset"]["files"].append(copy.deepcopy(d["dataset"]["files"][0]))


@pytest.mark.parametrize("mutate", [_dup_doc, _dup_query, _dup_judgment, _dup_result_row, _dup_ranking_doc,
                                    _dup_ks, _dup_methods, _dup_file_name])
@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_duplicate_ids_are_rejected_on_either_side(run_cli, base, cand, mutate, side):
    target = base if side == "baseline" else cand
    mutate(target)
    rep = invalid(run_cli, base, cand, "duplicate_id")
    assert any(e["side"] == side for e in rep["errors"] if e["code"] == "duplicate_id")


def test_duplicate_json_object_key_is_rejected(run_cli, base, cand):
    text = json.dumps(cand).replace('"schema_version": "1.1"', '"schema_version": "1.1", "schema_version": "1.1"', 1)
    rep = invalid(run_cli, base, text, "invalid_json")
    assert "duplicate JSON object key" in rep["errors"][0]["message"]


# ----------------------------------------------------------------------------- mismatched dataset
def _designation(d):
    d["dataset"]["designation"] = "real"


def _file_sha(d):
    d["dataset"]["files"][0]["sha256"] = "0" * 64


def _doc_text(d):
    d["dataset"]["documents"][0]["text"] = "edited"


def _query_text(d):
    d["dataset"]["queries"][0]["text"] = "edited"


def _judgment(d):
    # self-consistent edit (d12 also relevant to q02; q02 recall stays 1.0) so that only the cross-run check can fail
    next(j for j in d["dataset"]["judgments"] if j[:2] == ["q02", "d12"])[2] = 1
    r = row(d, "q02")
    r["n_relevant"] = 2
    r["ranking"][1]["label"] = "relevant"


def _extra_doc(d):
    d["dataset"]["documents"].append({"doc_id": "d99", "text": "extra"})


@pytest.mark.parametrize("mutate", [_designation, _file_sha, _doc_text, _query_text, _judgment, _extra_doc])
def test_mismatched_dataset_is_rejected(run_cli, base, cand, mutate):
    mutate(cand)
    invalid(run_cli, base, cand, "dataset_mismatch")


# ----------------------------------------------------------------------------- reviewer regressions
@pytest.mark.parametrize("missing", ["documents.jsonl", "queries.jsonl", "qrels.csv"])
def test_missing_required_dataset_checksum_is_rejected_even_when_both_sides_omit_it(run_cli, base, cand, missing):
    for d in (base, cand):  # identical omission on both sides: equality of the two reports is not enough
        d["dataset"]["files"] = [f for f in d["dataset"]["files"] if f["name"] != missing]
    rep = invalid(run_cli, base, cand, "missing_dataset_file")
    assert {e["side"] for e in rep["errors"] if e["code"] == "missing_dataset_file"} == {"baseline", "candidate"}
    assert missing in rep["errors"][0]["message"]


def test_query_without_any_judgment_row_is_rejected_even_when_both_sides_agree(run_cli, base, cand):
    for d in (base, cand):  # q08: judgment rows removed, ranking labels rewritten to 'unjudged' on both sides
        d["dataset"]["judgments"] = [j for j in d["dataset"]["judgments"] if j[0] != "q08"]
        for e in row(d, "q08")["ranking"]:
            e["label"] = "unjudged"
    rep = invalid(run_cli, base, cand, "missing_judgments")
    assert "q08" in [e for e in rep["errors"] if e["code"] == "missing_judgments"][0]["message"]


def test_explicit_zero_judgments_are_still_accepted_as_null_recall(run_cli, base, cand):
    assert {j[2] for j in base["dataset"]["judgments"] if j[0] == "q08"} == {0}
    code, rep = run_cli(base, cand)
    assert code == 0 and rep["unscored_queries"][0]["query_id"] == "q08"


# ----------------------------------------------------------------------------- mismatched cutoff
def test_mismatched_ks_is_rejected(run_cli, base, cand):
    cand["config"]["ks"] = [3, 5]
    cand["config"]["max_k"] = 5
    cand_row = row(cand, "q02")
    for r in cand["results"]["bm25"]["queries"]:
        r["metrics"]["5"] = copy.deepcopy(r["metrics"]["3"])
    cand["results"]["bm25"]["aggregate"]["5"] = copy.deepcopy(cand["results"]["bm25"]["aggregate"]["3"])
    rep = invalid(run_cli, base, cand, "cutoff_mismatch")
    assert any("config.ks" in e["message"] for e in rep["errors"])
    assert cand_row  # q02 stays fully recomputable; the failure is the cutoff, nothing else


def test_mismatched_retrieval_depth_is_rejected(run_cli, base, cand):
    cand["config"]["max_k"] = 5
    rep = invalid(run_cli, base, cand, "cutoff_mismatch")
    assert any("max_k" in e["message"] for e in rep["errors"])


def test_policy_cutoff_absent_from_run_is_rejected(run_cli, base, cand):
    policy = load("policy.json")
    policy["k"] = 5
    invalid(run_cli, base, cand, "cutoff_mismatch", policy=policy)


# ----------------------------------------------------------------------------- null versus missing
def test_null_recall_with_no_judged_relevant_is_legitimate_and_excluded(run_cli, base, cand):
    assert row(base, "q08")["metrics"]["3"]["recall"] is None and row(cand, "q08")["metrics"]["3"]["recall"] is None
    code, rep = run_cli(base, cand)
    assert code == 0
    assert rep["counts"] == {"total": 3, "scored": 2, "unscored": 1}
    assert "q08" not in {d["query_id"] for d in rep["per_query"]}
    assert rep["unscored_queries"][0]["query_id"] == "q08"


def test_q08_ranking_change_cannot_move_the_gate(run_cli, base, cand):
    for r in row(cand, "q08")["ranking"]:
        r["label"] = "nonrelevant"
    row(cand, "q08")["ranking"] = row(cand, "q08")["ranking"][:1]
    row(cand, "q08")["n_returned"] = 1
    code, rep = run_cli(base, cand)
    assert code == 0 and rep["aggregate"]["mean_delta"]["exact"] == "-1/20"


def test_missing_result_row_for_unscored_query_is_not_silently_skipped(run_cli, base, cand):
    cand["results"]["bm25"]["queries"] = [r for r in cand["results"]["bm25"]["queries"] if r["query_id"] != "q08"]
    rep = invalid(run_cli, base, cand, "missing_result")
    assert "q08" in [e for e in rep["errors"] if e["code"] == "missing_result"][0]["message"]


def test_extra_result_row_is_rejected(run_cli, base, cand):
    extra = copy.deepcopy(row(cand, "q02"))
    extra["query_id"] = "q99"
    cand["results"]["bm25"]["queries"].append(extra)
    invalid(run_cli, base, cand, "extra_result")


def test_null_recall_on_scored_query_is_rejected(run_cli, base, cand):
    row(cand, "q01")["metrics"]["3"]["recall"] = None
    invalid(run_cli, base, cand, "recall_mismatch")


def test_non_null_recall_on_unscored_query_is_rejected(run_cli, base, cand):
    row(cand, "q08")["metrics"]["3"]["recall"] = 0.0
    invalid(run_cli, base, cand, "recall_mismatch")


@pytest.mark.parametrize("breakage", ["drop_recall_key", "drop_metrics_k", "drop_metrics"])
def test_missing_recall_is_not_the_same_as_null(run_cli, base, cand, breakage):
    r = row(cand, "q08")
    if breakage == "drop_recall_key":
        del r["metrics"]["3"]["recall"]
    elif breakage == "drop_metrics_k":
        del r["metrics"]["3"]
    else:
        del r["metrics"]
    invalid(run_cli, base, cand, "malformed")


def test_wrong_status_for_unjudged_query_is_rejected(run_cli, base, cand):
    row(cand, "q08")["status"] = "scored"
    invalid(run_cli, base, cand, "status_mismatch")


def test_empty_ranking_scores_zero_and_stays_in_denominator(run_cli, base, cand):
    r = row(cand, "q02")
    r["ranking"], r["n_returned"] = [], 0
    r["metrics"]["3"]["recall"] = 0.0
    cand["results"]["bm25"]["aggregate"]["3"]["macro_recall"] = 0.1  # (0.2 + 0) / 2
    code, rep = run_cli(base, cand)
    assert code == 1
    assert rep["counts"]["scored"] == 2
    q02 = next(d for d in rep["per_query"] if d["query_id"] == "q02")
    assert q02["candidate"]["exact"] == "0" and q02["delta"]["exact"] == "-1"
    assert rep["largest_drops"][0]["query_id"] == "q02"


def test_empty_scored_set_is_rejected(run_cli, base, cand):
    for d in (base, cand):
        for j in d["dataset"]["judgments"]:
            j[2] = 0
        for r in d["results"]["bm25"]["queries"]:
            r.update(status="no_judged_relevant", n_relevant=0)
            r["metrics"]["3"]["recall"] = None
            for e in r["ranking"]:
                e["label"] = "nonrelevant"
        d["results"]["bm25"]["counts"] = {"total": 3, "scored": 0, "no_judged_relevant": 3}
    invalid(run_cli, base, cand, "empty_scored_set")


# ----------------------------------------------------------------------------- derived, not trusted
def test_saved_recall_must_match_recomputed(run_cli, base, cand):
    row(cand, "q01")["metrics"]["3"]["recall"] = 0.3  # ranking says 0.2
    invalid(run_cli, base, cand, "recall_mismatch")


def test_saved_aggregate_must_match_recomputed(run_cli, base, cand):
    cand["results"]["bm25"]["aggregate"]["3"]["macro_recall"] = 0.99
    invalid(run_cli, base, cand, "aggregate_mismatch")


def test_saved_counts_must_match_recomputed(run_cli, base, cand):
    cand["results"]["bm25"]["counts"]["scored"] = 3
    invalid(run_cli, base, cand, "count_mismatch")


def test_decision_ignores_saved_values_when_they_agree_with_rankings(run_cli, base, cand):
    # recall at the cutoff is derived from the first three ranks only
    assert [e["doc_id"] for e in row(cand, "q01")["ranking"]] == ["d01", "d02", "d12"]
    code, rep = run_cli(base, cand)
    assert rep["aggregate"]["candidate_mean"]["exact"] == "3/5"


# ----------------------------------------------------------------------------- ranking / score validity
@pytest.mark.parametrize("name,mutate,code", [
    ("rank_gap", lambda r: r["ranking"][1].update(rank=3), "invalid_ranking"),
    ("unknown_doc", lambda r: r["ranking"][2].update(doc_id="d77", label="unjudged"), "invalid_ranking"),
    ("wrong_label", lambda r: r["ranking"][0].update(label="nonrelevant"), "invalid_ranking"),
    ("unsorted_scores", lambda r: r["ranking"][2].update(score=9.0), "invalid_ranking"),
    ("too_deep", lambda r: r["ranking"].append({"rank": 4, "doc_id": "d04", "score": 0.5, "label": "relevant"}),
     "invalid_ranking"),
    ("string_score", lambda r: r["ranking"][0].update(score="3.0"), "malformed_score"),
    ("bool_score", lambda r: r["ranking"][0].update(score=True), "malformed_score"),
    ("null_score", lambda r: r["ranking"][0].update(score=None), "malformed_score"),
])
def test_invalid_rankings_and_scores_are_rejected(run_cli, base, cand, name, mutate, code):
    mutate(row(cand, "q01"))
    invalid(run_cli, base, cand, code)


def test_nan_score_in_json_is_rejected(run_cli, base, cand):
    text = json.dumps(cand).replace('"score": 3.0', '"score": NaN', 1)
    invalid(run_cli, base, text, "invalid_json")


# ----------------------------------------------------------------------------- run-level and settings
def test_incomplete_run_and_wrong_schema_are_rejected(run_cli, base, cand):
    cand["run"]["status"] = "failed"
    invalid(run_cli, base, cand, "run_not_completed")
    cand["run"]["status"] = "completed"
    cand["schema_version"] = "1.0"
    invalid(run_cli, base, cand, "unsupported_schema")


def test_same_run_id_is_rejected(run_cli, base, cand):
    cand["run"]["run_id"] = base["run"]["run_id"]
    invalid(run_cli, base, cand, "same_run")


def test_undeclared_bm25_setting_change_is_rejected(run_cli, base, cand):
    cand["adapters"]["bm25"]["k1"] = 1.2
    rep = invalid(run_cli, base, cand, "undeclared_setting_change")
    assert "k1" in rep["errors"][0]["message"]


def test_declared_setting_change_is_recorded_not_rejected(run_cli, base, cand):
    cand["adapters"]["bm25"]["k1"] = 1.2
    code, rep = run_cli(base, cand, extra=["--declare-change", "k1=tuning experiment"])
    assert code == 0
    assert rep["declared_changes"] == [{"field": "adapters.<method>.k1", "baseline": 1.5, "candidate": 1.2,
                                        "reason": "tuning experiment"}]
    assert "--declare-change 'k1=tuning experiment'" in rep["reproduction_command"]


def test_declaration_without_an_actual_change_is_rejected(run_cli, base, cand):
    invalid(run_cli, base, cand, "declared_change_not_observed", extra=["--declare-change", "k1=nothing changed"])


def test_quality_policy_text_mismatch_is_rejected(run_cli, base, cand):
    cand["policies"]["no_judged_relevant"] = "treated as zero"
    invalid(run_cli, base, cand, "policy_mismatch")


def test_latency_differences_never_affect_the_decision(run_cli, base, cand):
    cand["latency"]["summary"]["bm25"]["overall"]["median_ns"] = 9e12
    code, rep = run_cli(base, cand)
    assert code == 0 and rep["informational"]["bm25_overall_latency"]["candidate"]["median_ns"] == 9e12


# ----------------------------------------------------------------------------- gate policy file
@pytest.mark.parametrize("patch", [
    {"absolute_tolerance": 0.05},          # JSON number, not an exact decimal string
    {"absolute_tolerance": "-0.05"},
    {"method": "embedding"},
    {"metric": "mrr"},
    {"k": 3.0},
    {"unexpected": 1},
])
def test_bad_policy_is_comparison_invalid(run_cli, base, cand, patch):
    policy = {**load("policy.json"), **patch}
    invalid(run_cli, base, cand, "invalid_policy", policy=policy)


def test_unreadable_input_is_comparison_invalid(run_cli, base, cand, tmp_path):
    from mrds.cli import main
    code = main(["--baseline", str(tmp_path / "nope.json"), "--candidate", str(tmp_path / "nope2.json"),
                 "--policy", str(tmp_path / "nope3.json"), "--out-dir", str(tmp_path / "o")])
    rep = json.loads((tmp_path / "o" / "report.json").read_text())
    assert code == 2 and {"unreadable_input", "invalid_policy"} <= codes(rep)


def test_usage_error_exits_2(capsys):
    from mrds.cli import main
    with pytest.raises(SystemExit) as e:
        main(["--baseline", "x"])
    assert e.value.code == 2
