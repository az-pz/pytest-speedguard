"""Deterministic unit tests for the pure statistical core.

Every case uses injected numbers so the statistical robustness is proven
without any real timing noise.
"""

import math

import pytest

from pytest_speedguard.stats import (
    MAD_TO_STD,
    RegressionResult,
    evaluate,
    mad,
    median,
    scaled_mad,
)

# Common evaluate() knobs; individual tests override as needed.
KW = dict(threshold=0.5, noise_factor=3.0, min_duration=0.05, min_samples=3)


# -- median -----------------------------------------------------------------


def test_median_odd():
    assert median([3, 1, 2]) == 2.0


def test_median_even_averages_middle_pair():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_single():
    assert median([42.0]) == 42.0


def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])


# -- mad / scaled_mad -------------------------------------------------------


def test_mad_identical_is_zero():
    assert mad([1.0, 1.0, 1.0]) == 0.0


def test_mad_known_value():
    # median 3; deviations [2,1,0,1,2]; median of deviations = 1
    assert mad([1, 2, 3, 4, 5]) == 1.0


def test_mad_empty_is_zero():
    assert mad([]) == 0.0


def test_scaled_mad_applies_constant():
    assert scaled_mad([1, 2, 3, 4, 5]) == pytest.approx(MAD_TO_STD * 1.0)


def test_scaled_mad_zero_dispersion():
    assert scaled_mad([7.0, 7.0, 7.0]) == 0.0


# -- evaluate: new / empty --------------------------------------------------


def test_empty_samples_is_new_not_regression():
    r = evaluate(0.2, [], **KW)
    assert isinstance(r, RegressionResult)
    assert r.is_new is True
    assert r.is_regression is False
    assert r.baseline is None
    assert r.delta_ratio is None


def test_new_test_below_floor_still_new():
    # evaluate() does not apply the floor to brand-new tests; the report layer
    # decides whether to *list* it.
    r = evaluate(0.0001, [], **KW)
    assert r.is_new is True
    assert r.is_regression is False


# -- evaluate: guards -------------------------------------------------------


def test_below_min_duration_not_flagged():
    r = evaluate(0.02, [0.001, 0.001, 0.001, 0.001, 0.001], **KW)
    assert r.is_regression is False
    assert "floor" in r.reason


def test_warming_up_not_flagged():
    # Only 2 samples but min_samples is 3.
    r = evaluate(0.5, [0.1, 0.1], **KW)
    assert r.is_regression is False
    assert r.is_new is False
    assert "warming up" in r.reason


def test_exactly_at_threshold_not_flagged():
    # median 0.10, threshold 0.5 => limit 0.15; current == 0.15 is NOT > limit.
    r = evaluate(0.15, [0.10, 0.10, 0.10, 0.10, 0.10], **KW)
    assert r.is_regression is False
    assert "within threshold" in r.reason


def test_just_over_threshold_zero_dispersion_flagged():
    # Identical samples => scaled_mad 0 => guard 4 skipped, relative guard fires.
    r = evaluate(0.20, [0.10, 0.10, 0.10, 0.10, 0.10], **KW)
    assert r.is_regression is True
    assert "zero dispersion" in r.reason
    assert r.delta_ratio == pytest.approx(1.0)


def test_within_jitter_not_flagged():
    # Dispersed baseline: median 0.30, scaled_mad = 1.4826*0.10 = 0.14826.
    # absolute limit = 0.30 + 3*0.14826 = 0.74478.
    # current 0.50 clears the relative guard (>0.45) but is within jitter.
    samples = [0.10, 0.20, 0.30, 0.40, 0.50]
    r = evaluate(0.50, samples, **KW)
    assert r.is_regression is False
    assert "within jitter" in r.reason


def test_clear_spike_beyond_jitter_flagged():
    samples = [0.10, 0.20, 0.30, 0.40, 0.50]  # median 0.30
    r = evaluate(0.80, samples, **KW)
    assert r.is_regression is True
    assert r.baseline == pytest.approx(0.30)
    assert r.delta_ratio == pytest.approx((0.80 - 0.30) / 0.30)
    assert "scaled_mad" in r.reason


def test_min_samples_boundary_allows_flag():
    # Exactly min_samples (3) identical samples => eligible; clear spike flagged.
    r = evaluate(0.30, [0.10, 0.10, 0.10], **KW)
    assert r.is_regression is True


def test_delta_ratio_math():
    r = evaluate(0.30, [0.10, 0.10, 0.10, 0.10], **KW)
    assert r.delta_ratio == pytest.approx(2.0)  # +200%


def test_noise_factor_can_be_tightened():
    # Same dispersed baseline; a smaller noise_factor makes the absolute guard
    # easier to clear, so 0.50 now flags.
    samples = [0.10, 0.20, 0.30, 0.40, 0.50]
    kw = dict(KW)
    kw["noise_factor"] = 1.0
    r = evaluate(0.50, samples, **kw)
    assert r.is_regression is True


def test_reason_is_human_readable_string():
    r = evaluate(0.80, [0.10, 0.20, 0.30, 0.40, 0.50], **KW)
    assert isinstance(r.reason, str) and r.reason
    assert not math.isnan(r.current)
