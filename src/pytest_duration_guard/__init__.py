"""pytest-duration-guard: passively catch tests that get slower over time.

``pytest-duration-guard`` records every test's wall-clock duration across runs,
keeps a noise-tolerant rolling baseline, and flags tests that regress (get
meaningfully slower) or newly appear as slow.  It has no runtime dependency
beyond pytest — the statistics are implemented in pure Python.

Public API (the plugin wiring lives in :mod:`pytest_duration_guard.plugin`):

* :func:`~pytest_duration_guard.stats.evaluate` and the robust-stats helpers
  :func:`~pytest_duration_guard.stats.median`,
  :func:`~pytest_duration_guard.stats.mad`,
  :func:`~pytest_duration_guard.stats.scaled_mad`.
* :class:`~pytest_duration_guard.stats.RegressionResult`.
* :class:`~pytest_duration_guard.baseline.Baseline`.
"""

from __future__ import annotations

from .baseline import Baseline
from .stats import RegressionResult, evaluate, mad, median, scaled_mad

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Baseline",
    "RegressionResult",
    "evaluate",
    "mad",
    "median",
    "scaled_mad",
]
