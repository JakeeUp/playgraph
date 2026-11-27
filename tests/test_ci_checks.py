"""The performance gate must fail closed on bad or incomplete measurements."""
import csv

import pytest

from loadtest.ci_check import ROUTES, check_stats


@pytest.fixture
def stats(tmp_path):
    def write(change=None):
        rows = [{"Type": "GET", "Name": route, "Request Count": "10", "Failure Count": "0", "95%": "40"}
                for route in sorted(ROUTES)]
        if change:
            change(rows)
        path = tmp_path / "stats.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["Type", "Name", "Request Count", "Failure Count", "95%"])
            writer.writeheader()
            writer.writerows(rows)
        return path
    return write


def test_perf_gate_allows_the_limit(stats):
    assert check_stats(stats(), 40)["worst_p95_ms"] == 40


@pytest.mark.parametrize("column,value", [("95%", "41"), ("95%", "nan"), ("95%", "inf"),
                                          ("95%", "-1"), ("Request Count", "4"), ("Failure Count", "1")])
def test_perf_gate_rejects_bad_route_stats(stats, column, value):
    with pytest.raises(ValueError):
        check_stats(stats(lambda rows: rows[0].update({column: value})), 40)


def test_perf_gate_requires_every_route(stats):
    with pytest.raises(ValueError, match="Missing"):
        check_stats(stats(lambda rows: rows.pop()), 40)


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_perf_gate_requires_a_finite_positive_limit(stats, threshold):
    with pytest.raises(ValueError):
        check_stats(stats(), threshold)
