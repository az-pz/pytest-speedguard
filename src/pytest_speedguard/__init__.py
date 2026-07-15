"""pytest-speedguard: passively catch tests that get slower over time.

``pytest-speedguard`` records every test's wall-clock duration across runs,
keeps a noise-tolerant rolling baseline, and flags tests that regress (get
meaningfully slower) or newly appear as slow.  It has no runtime dependency
beyond pytest — the statistics are implemented in pure Python.

Public API (the plugin wiring lives in :mod:`pytest_speedguard.plugin`):

* :func:`~pytest_speedguard.stats.evaluate` and the robust-stats helpers
  :func:`~pytest_speedguard.stats.median`,
  :func:`~pytest_speedguard.stats.mad`,
  :func:`~pytest_speedguard.stats.scaled_mad`.
* :class:`~pytest_speedguard.stats.RegressionResult`.
* :class:`~pytest_speedguard.baseline.Baseline`.
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
