"""pytest plugin wiring for :mod:`pytest_speedguard`.

Responsibilities:

* Declare every command-line option and its matching ``ini`` key.
* Capture each test's duration via :func:`pytest_runtest_logreport`.
* At session end (controller only, never xdist workers) compare each duration
  against the rolling baseline, print the report, and update+save the baseline
  according to the configured update mode.

The statistical decision-making lives in :mod:`pytest_speedguard.stats`; the
persistence in :mod:`pytest_speedguard.baseline`; the rendering in
:mod:`pytest_speedguard.report`.  This module is pure orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .baseline import Baseline
from .report import ReportData, ReportRow, render
from .stats import RegressionResult, evaluate

DEFAULT_BASELINE_PATH = ".pytest_speedguard/baseline.json"

_PHASES = ("call", "total")
_UPDATE_MODES = ("auto", "always", "never")

# Registration name for the plugin instance (handy for introspection/tests).
_PLUGIN_NAME = "speedguard_plugin"


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def pytest_addoption(parser) -> None:
    """Register CLI options and the parallel ``ini`` keys.

    Every tunable is available both on the command line and via ``ini`` so a
    project can bake its policy into ``pytest.ini`` / ``pyproject.toml`` and
    still override per-run on the command line (CLI wins over ini).
    """
    group = parser.getgroup(
        "speedguard",
        "pytest-speedguard: passive per-test duration regression tracking",
    )

    # -- Master switch ------------------------------------------------------
    group.addoption(
        "--speedguard",
        action="store_true",
        dest="speedguard",
        default=None,
        help="Enable speedguard (default: on; overrides the ini setting).",
    )
    group.addoption(
        "--no-speedguard",
        action="store_false",
        dest="speedguard",
        default=None,
        help="Disable speedguard for this run.",
    )
    parser.addini(
        "speedguard",
        type="bool",
        default=True,
        help="Master switch for pytest-speedguard (default: true).",
    )

    # -- Relative threshold -------------------------------------------------
    group.addoption(
        "--speedguard-threshold",
        action="store",
        dest="speedguard_threshold",
        type=float,
        default=None,
        metavar="RATIO",
        help="Relative regression threshold; 0.5 means +50%% over the baseline "
        "median (default: 0.5).",
    )
    parser.addini(
        "speedguard_threshold",
        default=0.5,
        help="Relative regression threshold (default: 0.5 = +50%%).",
    )

    # -- Noise floor --------------------------------------------------------
    group.addoption(
        "--speedguard-min-duration",
        action="store",
        dest="speedguard_min_duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Ignore tests faster than this many seconds; sub-50ms timings are "
        "noise-dominated (default: 0.05).",
    )
    parser.addini(
        "speedguard_min_duration",
        default=0.05,
        help="Minimum duration in seconds before a test can be flagged "
        "(default: 0.05).",
    )

    # -- Absolute statistical guard ----------------------------------------
    group.addoption(
        "--speedguard-noise-factor",
        action="store",
        dest="speedguard_noise_factor",
        type=float,
        default=None,
        metavar="FACTOR",
        help="Require current > median + FACTOR * scaled_mad, i.e. beyond "
        "normal jitter (default: 3.0).",
    )
    parser.addini(
        "speedguard_noise_factor",
        default=3.0,
        help="Multiplier on scaled MAD for the absolute guard (default: 3.0).",
    )

    # -- Rolling window size ------------------------------------------------
    group.addoption(
        "--speedguard-window",
        action="store",
        dest="speedguard_window",
        type=int,
        default=None,
        metavar="N",
        help="Rolling window size (samples kept per test) (default: 20).",
    )
    parser.addini(
        "speedguard_window",
        default=20,
        help="Rolling window size per test (default: 20).",
    )

    # -- Warm-up guard ------------------------------------------------------
    group.addoption(
        "--speedguard-min-samples",
        action="store",
        dest="speedguard_min_samples",
        type=int,
        default=None,
        metavar="N",
        help="Minimum baseline samples before a test can be flagged; fewer "
        "means it is still warming up (default: 3).",
    )
    parser.addini(
        "speedguard_min_samples",
        default=3,
        help="Minimum baseline samples before flagging (default: 3).",
    )

    # -- Measured phase -----------------------------------------------------
    group.addoption(
        "--speedguard-phase",
        action="store",
        dest="speedguard_phase",
        choices=list(_PHASES),
        default=None,
        help="Which phase to time: 'call' (the test body) or 'total' "
        "(setup+call+teardown) (default: call).",
    )
    parser.addini(
        "speedguard_phase",
        default="call",
        help="Timing phase: call | total (default: call).",
    )

    # -- Update mode --------------------------------------------------------
    group.addoption(
        "--speedguard-update",
        action="store",
        dest="speedguard_update",
        choices=list(_UPDATE_MODES),
        default=None,
        help="Baseline update mode: 'auto' appends the sample only if the test "
        "did not regress; 'always' always appends (rolling); 'never' compares "
        "only and never writes (frozen baseline) (default: auto).",
    )
    parser.addini(
        "speedguard_update",
        default="auto",
        help="Baseline update mode: auto | always | never (default: auto).",
    )

    # -- Accept / re-baseline ----------------------------------------------
    group.addoption(
        "--speedguard-accept",
        action="store_true",
        dest="speedguard_accept",
        default=False,
        help="Record ALL current durations as the new baseline, suppress "
        "regression reporting, and do not fail on regressions. The deliberate "
        "way to move the baseline after an intended slowdown.",
    )

    # -- CI gate ------------------------------------------------------------
    group.addoption(
        "--speedguard-fail",
        action="store_true",
        dest="speedguard_fail",
        default=None,
        help="Fail the session (non-zero exit) if any regression is detected. "
        "Default is informational only (default: false).",
    )
    parser.addini(
        "speedguard_fail",
        type="bool",
        default=False,
        help="Fail the session on regression (default: false).",
    )

    # -- Baseline path ------------------------------------------------------
    group.addoption(
        "--speedguard-baseline",
        action="store",
        dest="speedguard_baseline",
        default=None,
        metavar="PATH",
        help=f"Path to the baseline JSON file (default: {DEFAULT_BASELINE_PATH}).",
    )
    parser.addini(
        "speedguard_baseline",
        default=DEFAULT_BASELINE_PATH,
        help=f"Baseline JSON file path (default: {DEFAULT_BASELINE_PATH}).",
    )

    # -- Report cap ---------------------------------------------------------
    group.addoption(
        "--speedguard-top",
        action="store",
        dest="speedguard_top",
        type=int,
        default=20,
        metavar="N",
        help="Maximum rows per report section (default: 20).",
    )


# ---------------------------------------------------------------------------
# Settings resolution (CLI overrides ini)
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """Fully resolved, typed configuration for one run."""

    enabled: bool
    threshold: float
    min_duration: float
    noise_factor: float
    window: int
    min_samples: int
    phase: str
    update: str
    accept: bool
    fail: bool
    baseline_path: str
    top: int


def _opt_or_ini(config, name):
    """Return the CLI option if explicitly set, else the ini value."""
    val = config.getoption(name)
    if val is None:
        val = config.getini(name)
    return val


def _normalize_choice(value, allowed, default):
    value = str(value)
    return value if value in allowed else default


def _resolve_settings(config) -> Settings:
    """Resolve all options into a typed :class:`Settings` (CLI beats ini)."""
    return Settings(
        enabled=bool(_opt_or_ini(config, "speedguard")),
        threshold=float(_opt_or_ini(config, "speedguard_threshold")),
        min_duration=float(_opt_or_ini(config, "speedguard_min_duration")),
        noise_factor=float(_opt_or_ini(config, "speedguard_noise_factor")),
        window=int(_opt_or_ini(config, "speedguard_window")),
        min_samples=int(_opt_or_ini(config, "speedguard_min_samples")),
        phase=_normalize_choice(
            _opt_or_ini(config, "speedguard_phase"), _PHASES, "call"
        ),
        update=_normalize_choice(
            _opt_or_ini(config, "speedguard_update"), _UPDATE_MODES, "auto"
        ),
        accept=bool(config.getoption("speedguard_accept")),
        fail=bool(_opt_or_ini(config, "speedguard_fail")),
        baseline_path=str(_opt_or_ini(config, "speedguard_baseline")),
        top=int(config.getoption("speedguard_top")),
    )


# ---------------------------------------------------------------------------
# The plugin object
# ---------------------------------------------------------------------------


@dataclass
class _TotalParts:
    """Accumulator for phase == 'total' (setup + call + teardown)."""

    setup: float = 0.0
    call: float = 0.0
    teardown: float = 0.0
    ran: bool = False
    skipped: bool = False


class SpeedGuardPlugin:
    """Session-scoped collector + analyser, registered in :func:`pytest_configure`."""

    def __init__(self, config, settings: Settings) -> None:
        self.config = config
        self.settings = settings
        # nodeid -> current duration for the configured phase.
        self._current: Dict[str, float] = {}
        # nodeid -> accumulator, only used when phase == "total".
        self._parts: Dict[str, _TotalParts] = {}

        # Populated by _analyze():
        self.regressions: List[ReportRow] = []
        self.new_slow: List[ReportRow] = []
        self.should_fail: bool = False
        self.accepted: bool = False
        self.baseline_warnings: List[str] = []
        self._analyzed: bool = False

    # -- xdist awareness ----------------------------------------------------

    @property
    def is_controller(self) -> bool:
        """False on xdist workers; comparison/report/write happen only here.

        Workers just execute tests and stream their reports back to the
        controller, where :func:`pytest_runtest_logreport` is re-emitted and
        aggregated.
        """
        return not hasattr(self.config, "workerinput")

    # -- Duration capture ---------------------------------------------------

    def pytest_runtest_logreport(self, report) -> None:
        """Record durations for tests that actually ran (passed or failed)."""
        if self.settings.phase == "total":
            self._record_total(report)
            return
        # phase == "call": record only the call phase of non-skipped tests.
        if report.when == "call" and (report.passed or report.failed):
            self._current[report.nodeid] = float(report.duration)

    def _record_total(self, report) -> None:
        parts = self._parts.setdefault(report.nodeid, _TotalParts())
        when = report.when
        if when == "setup":
            parts.setup = float(report.duration)
            if report.skipped:
                parts.skipped = True
        elif when == "call":
            parts.call = float(report.duration)
            if report.skipped:
                parts.skipped = True
            elif report.passed or report.failed:
                parts.ran = True
        elif when == "teardown":
            parts.teardown = float(report.duration)
            # Teardown is last: finalise the total for this nodeid.
            self._parts.pop(report.nodeid, None)
            if parts.ran and not parts.skipped:
                self._current[report.nodeid] = (
                    parts.setup + parts.call + parts.teardown
                )

    # -- Analysis + baseline update ----------------------------------------

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        """Compare, decide fail state, and update+save the baseline.

        Runs during the terminal reporter's ``pytest_sessionfinish`` wrapper
        (before it prints), so :func:`pytest_terminal_summary` can render the
        already-computed results.  Modifying ``session.exitstatus`` here changes
        the process exit code.
        """
        if not self.is_controller:
            return
        self._analyze(session)

    def _analyze(self, session) -> None:
        if self._analyzed:
            return
        self._analyzed = True
        s = self.settings

        baseline = Baseline(s.baseline_path, phase=s.phase)
        baseline.load()
        self.baseline_warnings = baseline.warnings

        current = self._current

        # -- Accept / re-baseline: record everything, report nothing. -------
        if s.accept:
            for nodeid, dur in current.items():
                baseline.record(nodeid, dur, s.window)
            baseline.save()
            self.accepted = True
            return

        eval_results: Dict[str, RegressionResult] = {}
        for nodeid, dur in current.items():
            samples = baseline.get_samples(nodeid)
            result = evaluate(
                dur,
                samples,
                threshold=s.threshold,
                noise_factor=s.noise_factor,
                min_duration=s.min_duration,
                min_samples=s.min_samples,
            )
            eval_results[nodeid] = result
            if result.is_new:
                if dur >= s.min_duration:
                    self.new_slow.append(ReportRow(nodeid=nodeid, current=dur))
            elif result.is_regression:
                self.regressions.append(
                    ReportRow(
                        nodeid=nodeid,
                        current=dur,
                        baseline=result.baseline,
                        delta_ratio=result.delta_ratio,
                        samples=len(samples),
                    )
                )

        # Biggest regressions first; new slow tests slowest first.
        self.regressions.sort(
            key=lambda r: r.delta_ratio if r.delta_ratio is not None else float("inf"),
            reverse=True,
        )
        self.new_slow.sort(key=lambda r: r.current, reverse=True)

        # -- CI gate. -------------------------------------------------------
        self.should_fail = bool(s.fail and self.regressions)
        if self.should_fail and session.exitstatus == 0:
            session.exitstatus = 1

        # -- Baseline update per mode. --------------------------------------
        if s.update != "never":
            for nodeid, dur in current.items():
                result = eval_results.get(nodeid)
                if s.update == "always":
                    baseline.record(nodeid, dur, s.window)
                else:  # auto: absorb only non-regressions so regressions persist
                    if result is None or not result.is_regression:
                        baseline.record(nodeid, dur, s.window)
            baseline.save()
        # "never": frozen baseline — deliberately no write at all.

    # -- Reporting ----------------------------------------------------------

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config) -> None:
        """Print the speedguard section (controller only)."""
        if not self.is_controller:
            return
        for warning in self.baseline_warnings:
            terminalreporter.write_line(f"speedguard: {warning}", yellow=True)

        data = ReportData(
            regressions=self.regressions,
            new_slow=self.new_slow,
            threshold=self.settings.threshold,
            min_duration=self.settings.min_duration,
            top=self.settings.top,
            fail_enabled=self.settings.fail,
            failing=self.should_fail,
            accepted=self.accepted,
            verbose=config.getoption("verbose", 0) > 0,
        )
        render(terminalreporter, data)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def pytest_configure(config) -> None:
    """Resolve settings and register the plugin instance when enabled."""
    settings = _resolve_settings(config)
    if not settings.enabled:
        return
    plugin = SpeedGuardPlugin(config, settings)
    config.pluginmanager.register(plugin, _PLUGIN_NAME)
    # Stashed for introspection in tests.
    config._speedguard_plugin = plugin


def pytest_unconfigure(config) -> None:
    """Unregister the plugin instance if it was registered."""
    plugin = getattr(config, "_speedguard_plugin", None)
    if plugin is not None:
        config.pluginmanager.unregister(plugin)
        config._speedguard_plugin = None
