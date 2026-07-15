"""End-to-end integration tests driving the installed plugin via ``pytester``.

Magnitudes are deliberately well separated (baseline median ~1ms vs. a
``sleep(0.2)`` regressor) so real timing noise on CI runners can never flip an
assertion.  Negative/statistical edge cases are proven in ``test_stats.py``;
these tests focus on the plumbing.
"""

import json

BASELINE_REL = ".pytest_speedguard/baseline.json"


def _seed(pytester, tests, phase="call"):
    """Write a baseline file under the pytester dir and return its Path."""
    path = pytester.path / ".pytest_speedguard" / "baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "phase": phase, "tests": tests}),
        encoding="utf-8",
    )
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_run_seeds_baseline_no_regressions(pytester):
    pytester.makepyfile(
        test_first="""
        import time
        def test_slow():
            time.sleep(0.2)
        def test_fast():
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)

    baseline = pytester.path / ".pytest_speedguard" / "baseline.json"
    assert baseline.exists()
    data = _load(baseline)
    assert "test_first.py::test_slow" in data["tests"]
    assert "test_first.py::test_fast" in data["tests"]

    # Match the report banner (write_sep renders "=== speedguard ===") rather
    # than the bare word, which also appears in pytest's own "plugins:" header.
    result.stdout.fnmatch_lines(["*= speedguard =*", "*New slow tests*"])
    result.stdout.no_fnmatch_line("*Top regressions*")


def test_seeded_regression_is_reported(pytester):
    pytester.makepyfile(
        test_reg="""
        import time
        def test_target():
            time.sleep(0.2)
        """
    )
    _seed(
        pytester,
        {"test_reg.py::test_target": {"samples": [0.001, 0.0011, 0.0009, 0.001, 0.001]}},
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*Top regressions*", "*test_target*"])


def test_fail_flag_makes_session_nonzero_on_regression(pytester):
    pytester.makepyfile(
        test_fail="""
        import time
        def test_target():
            time.sleep(0.2)
        """
    )
    _seed(
        pytester,
        {"test_fail.py::test_target": {"samples": [0.001, 0.001, 0.001, 0.001, 0.001]}},
    )
    result = pytester.runpytest("--speedguard-fail", "-p", "no:cacheprovider")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*session failed*"])


def test_disabled_writes_no_file_and_no_section(pytester):
    pytester.makepyfile(
        test_off="""
        import time
        def test_slow():
            time.sleep(0.2)
        """
    )
    result = pytester.runpytest("--no-speedguard", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    baseline = pytester.path / ".pytest_speedguard" / "baseline.json"
    assert not baseline.exists()
    # Guard against the report banner ("=== speedguard ==="), not the bare word,
    # which also appears in pytest's "plugins: speedguard-0.1.0" header line.
    result.stdout.no_fnmatch_line("*= speedguard =*")


def test_sub_floor_doubling_not_flagged(pytester):
    # A tiny test that "doubles" but stays below the 50ms floor must not flag.
    pytester.makepyfile(
        test_floor="""
        def test_tiny():
            pass
        """
    )
    _seed(
        pytester,
        {"test_floor.py::test_tiny": {"samples": [0.0001, 0.0001, 0.0001, 0.0001, 0.0001]}},
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*Top regressions*")


def test_never_mode_leaves_baseline_byte_identical(pytester):
    pytester.makepyfile(
        test_never="""
        import time
        def test_target():
            time.sleep(0.2)
        """
    )
    path = _seed(
        pytester,
        {"test_never.py::test_target": {"samples": [0.001, 0.001, 0.001, 0.001, 0.001]}},
    )
    before = path.read_bytes()
    result = pytester.runpytest(
        "--speedguard-update=never", "-p", "no:cacheprovider"
    )
    result.assert_outcomes(passed=1)
    # Comparison still happens (regression reported) ...
    result.stdout.fnmatch_lines(["*Top regressions*"])
    # ... but the frozen baseline is never rewritten.
    assert path.read_bytes() == before


def test_accept_rebaselines_and_suppresses(pytester):
    pytester.makepyfile(
        test_acc="""
        import time
        def test_target():
            time.sleep(0.2)
        """
    )
    path = _seed(
        pytester,
        {"test_acc.py::test_target": {"samples": [0.001, 0.001, 0.001, 0.001, 0.001]}},
    )
    result = pytester.runpytest("--speedguard-accept", "-p", "no:cacheprovider")
    assert result.ret == 0
    result.stdout.no_fnmatch_line("*Top regressions*")

    samples = _load(path)["tests"]["test_acc.py::test_target"]["samples"]
    assert samples[-1] > 0.1  # the intended slowdown was recorded


def test_parametrized_tests_tracked_separately(pytester):
    pytester.makepyfile(
        test_param="""
        import pytest
        @pytest.mark.parametrize("n", [1, 2])
        def test_p(n):
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
    data = _load(pytester.path / ".pytest_speedguard" / "baseline.json")
    assert "test_param.py::test_p[1]" in data["tests"]
    assert "test_param.py::test_p[2]" in data["tests"]


def test_ini_configuration_is_honoured(pytester):
    # Threshold set very high via ini => the slow test is not a regression.
    pytester.makeini(
        """
        [pytest]
        speedguard_threshold = 1000.0
        """
    )
    pytester.makepyfile(
        test_ini="""
        import time
        def test_target():
            time.sleep(0.2)
        """
    )
    _seed(
        pytester,
        {"test_ini.py::test_target": {"samples": [0.001, 0.001, 0.001, 0.001, 0.001]}},
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*Top regressions*")


def test_total_phase_records_summed_duration(pytester):
    pytester.makepyfile(
        test_total="""
        import time
        import pytest

        @pytest.fixture
        def slow_setup():
            time.sleep(0.1)
            yield

        def test_target(slow_setup):
            time.sleep(0.1)
        """
    )
    result = pytester.runpytest(
        "--speedguard-phase=total", "-p", "no:cacheprovider"
    )
    result.assert_outcomes(passed=1)
    data = _load(pytester.path / ".pytest_speedguard" / "baseline.json")
    assert data["phase"] == "total"
    # setup (~0.1) + call (~0.1) recorded together should exceed either alone.
    samples = data["tests"]["test_total.py::test_target"]["samples"]
    assert samples[0] >= 0.18
