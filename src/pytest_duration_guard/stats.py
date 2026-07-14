"""Pure statistical core for :mod:`pytest_duration_guard`.

This module deliberately does **not** import :mod:`pytest`.  Everything here is
plain arithmetic on lists of floats so that the regression-detection logic can
be exercised with fully deterministic, injected numbers in the unit tests.

The model is built on *robust* statistics (median + median-absolute-deviation)
rather than mean + standard deviation, because test timings on shared CI
runners are heavy-tailed: an occasional 10x outlier from a noisy neighbour must
not move the baseline or trip a false alarm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

__all__ = [
    "median",
    "mad",
    "scaled_mad",
    "RegressionResult",
    "evaluate",
    "MAD_TO_STD",
]

#: Scaling constant that turns the MAD into a consistent estimator of the
#: standard deviation for normally distributed data (``1 / \u03a6\u207b\u00b9(3/4)``).
MAD_TO_STD: float = 1.4826


def median(values: Sequence[float]) -> float:
    """Return the median of *values*.

    :raises ValueError: if *values* is empty.  Callers are expected to guard
        against empty input (an empty baseline means "new test", handled in
        :func:`evaluate`).
    """
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median() arg is an empty sequence")
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: Sequence[float]) -> float:
    """Median absolute deviation about the median.

    A robust dispersion measure: the median of the absolute deviations from the
    median.  Unlike the standard deviation it is barely affected by outliers.
    Returns ``0.0`` for empty input.
    """
    if not values:
        return 0.0
    med = median(values)
    return median([abs(v - med) for v in values])


def scaled_mad(values: Sequence[float]) -> float:
    """The MAD scaled by :data:`MAD_TO_STD`.

    This is a robust, outlier-resistant estimate of the standard deviation and
    is what the absolute "beyond normal jitter" guard is measured in.
    """
    return MAD_TO_STD * mad(values)


@dataclass
class RegressionResult:
    """Outcome of comparing one current duration against its baseline window.

    :param is_regression: ``True`` only when *every* guard in :func:`evaluate`
        agrees the test got meaningfully slower.
    :param is_new: ``True`` when there was no baseline history for the test.
    :param baseline: The baseline median in seconds, or ``None`` for a new test.
    :param current: The current duration in seconds.
    :param delta_ratio: Fractional change vs. the baseline median
        (``0.5`` == +50%), or ``None`` when it is undefined (new test, or a
        degenerate zero baseline median).
    :param reason: Human-readable explanation of which guard tripped (or why the
        test was spared) — surfaced in ``-v`` output and useful in tests.
    """

    is_regression: bool
    is_new: bool
    baseline: Optional[float]
    current: float
    delta_ratio: Optional[float]
    reason: str


def evaluate(
    current: float,
    samples: List[float],
    *,
    threshold: float,
    noise_factor: float,
    min_duration: float,
    min_samples: int,
) -> RegressionResult:
    """Decide whether *current* is a regression against the *samples* baseline.

    A test is flagged as a regression **iff all** of the following hold:

    1. ``current >= min_duration`` — it is above the noise floor.  Sub-floor
       timings (default 50 ms) are dominated by scheduler jitter and are never
       flagged.
    2. There are at least ``min_samples`` baseline samples.  With fewer, the
       test is still "warming up" and cannot be judged.
    3. ``current > median * (1 + threshold)`` — the *relative* guard: it grew by
       more than the allowed percentage.
    4. ``current > median + noise_factor * scaled_mad`` — the *absolute*
       statistical guard: the jump is bigger than normal run-to-run jitter.

    When ``scaled_mad == 0`` (too few or identical samples) guard 4 is skipped
    and the decision rests on the relative guard alone, so we neither divide by
    nor flag on zero dispersion.

    An empty *samples* list means the test is new: ``is_new=True`` and it is
    never a regression.
    """
    # --- New test: no history to compare against. ---------------------------
    if not samples:
        return RegressionResult(
            is_regression=False,
            is_new=True,
            baseline=None,
            current=current,
            delta_ratio=None,
            reason="new test (no baseline samples yet)",
        )

    baseline_median = median(samples)
    if baseline_median > 0:
        delta_ratio: Optional[float] = (current - baseline_median) / baseline_median
    else:
        # Degenerate baseline (all-zero samples): ratio is undefined.
        delta_ratio = None

    # --- Guard 1: noise floor. ---------------------------------------------
    if current < min_duration:
        return RegressionResult(
            is_regression=False,
            is_new=False,
            baseline=baseline_median,
            current=current,
            delta_ratio=delta_ratio,
            reason=(
                "below min-duration floor "
                f"({current:.4f}s < {min_duration:.4f}s)"
            ),
        )

    # --- Guard 2: warm-up. --------------------------------------------------
    if len(samples) < min_samples:
        return RegressionResult(
            is_regression=False,
            is_new=False,
            baseline=baseline_median,
            current=current,
            delta_ratio=delta_ratio,
            reason=(
                f"warming up ({len(samples)} of {min_samples} samples required)"
            ),
        )

    # --- Guard 3: relative threshold. --------------------------------------
    relative_limit = baseline_median * (1.0 + threshold)
    if not current > relative_limit:
        return RegressionResult(
            is_regression=False,
            is_new=False,
            baseline=baseline_median,
            current=current,
            delta_ratio=delta_ratio,
            reason=(
                f"within threshold (current {current:.4f}s <= "
                f"{relative_limit:.4f}s = median*(1+{threshold:.2f}))"
            ),
        )

    # --- Guard 4: absolute statistical guard (skipped on zero dispersion). --
    smad = scaled_mad(samples)
    if smad > 0:
        absolute_limit = baseline_median + noise_factor * smad
        if not current > absolute_limit:
            return RegressionResult(
                is_regression=False,
                is_new=False,
                baseline=baseline_median,
                current=current,
                delta_ratio=delta_ratio,
                reason=(
                    f"within jitter (current {current:.4f}s <= "
                    f"{absolute_limit:.4f}s = median+"
                    f"{noise_factor:.1f}*scaled_mad)"
                ),
            )
        stat_note = (
            f"beyond median+{noise_factor:.1f}*scaled_mad ({absolute_limit:.4f}s)"
        )
    else:
        stat_note = "zero dispersion, relative guard only"

    # --- All guards passed: this is a regression. --------------------------
    pct = "unbounded" if delta_ratio is None else f"+{delta_ratio * 100:.1f}%"
    return RegressionResult(
        is_regression=True,
        is_new=False,
        baseline=baseline_median,
        current=current,
        delta_ratio=delta_ratio,
        reason=(
            f"regression: {pct} over baseline median "
            f"{baseline_median:.4f}s; {stat_note}"
        ),
    )
