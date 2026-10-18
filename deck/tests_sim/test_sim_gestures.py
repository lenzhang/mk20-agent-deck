"""pytest front-end for the declarative cases in mk20sim/cases/ (single source of truth;
the web UI test panel and `python -m mk20sim run-cases` load the very same files)."""
import pytest

from mk20sim.cases import load_cases, run_case

CASES = load_cases()


def test_cases_found():
    assert len(CASES) >= 10


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case(case):
    r = run_case(case)
    assert r.ok, str(r.failure or r.error)


def test_failure_reports_step_expected_actual_and_screen():
    from mk20sim.cases import Case

    r = run_case(Case("bad", "meta", "", [{"press": "A1"}, {"expect": {"page": "L4"}}]))
    assert not r.ok and r.failure.step == 2 and r.failure.expected == "L4" and r.failure.actual == "L1"
    assert r.screen_png_b64
