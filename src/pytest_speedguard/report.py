"""Terminal-summary rendering for :mod:`pytest_speedguard`.

Kept separate from the plugin wiring so the table/label formatting can be unit
tested without a live pytest session.  The public :func:`render` entry point
writes through pytest's :class:`TerminalReporter` (so colours/markup honour the
user's ``--color`` setting); the helpers below are pure string builders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

__all__ = ["ReportRow", "ReportData", "render", "format_summary_line"]

_SECTION_TITLE = "speedguard"
#: Longest node id we print before truncating (keeping the informative tail).
_MAX_NODEID = 70


@dataclass
class ReportRow:
    """One row in a report table."""

    nodeid: str
    current: float
    baseline: Optional[float] = None
    delta_ratio: Optional[float] = None
    samples: int = 0


@dataclass
class ReportData:
    """Everything :func:`render` needs to draw the speedguard section."""

    regressions: List[ReportRow] = field(default_factory=list)
    new_slow: List[ReportRow] = field(default_factory=list)
    threshold: float = 0.5
    min_duration: float = 0.05
    top: int = 20
    fail_enabled: bool = False
    failing: bool = False
    accepted: bool = False
    verbose: bool = False


# -- Formatting helpers -----------------------------------------------------


def _fmt_secs(value: Optional[float]) -> str:
    """Render a duration compactly: milliseconds under 1s, else seconds."""
    if value is None:
        return "-"
    if value >= 1.0:
        return f"{value:.3f}s"
    return f"{value * 1000:.1f}ms"


def _fmt_pct(delta_ratio: Optional[float]) -> str:
    if delta_ratio is None:
        return "-"
    return f"{delta_ratio * 100:+.1f}%"


def _truncate(nodeid: str) -> str:
    if len(nodeid) <= _MAX_NODEID:
        return nodeid
    return "..." + nodeid[-(_MAX_NODEID - 3) :]


def _render_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str]
) -> List[str]:
    """Return aligned monospace table lines for *headers* + *rows*.

    *aligns* is a per-column ``"l"``/``"r"`` sequence.  Pure and deterministic
    so it can be asserted on directly in tests.
    """
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt_row(cells: Sequence[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            if aligns[i] == "r":
                parts.append(cell.rjust(widths[i]))
            else:
                parts.append(cell.ljust(widths[i]))
        return "  ".join(parts).rstrip()

    lines = [_fmt_row(headers)]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(_fmt_row(row))
    return lines


def format_summary_line(data: ReportData) -> str:
    """Build the one-line count summary, e.g.::

        2 regressions, 1 new slow test (threshold 50%, floor 50ms)
    """
    n_reg = len(data.regressions)
    n_new = len(data.new_slow)
    reg_word = "regression" if n_reg == 1 else "regressions"
    new_word = "new slow test" if n_new == 1 else "new slow tests"
    floor_ms = data.min_duration * 1000
    floor = (
        f"{floor_ms:.0f}ms" if floor_ms == int(floor_ms) else f"{floor_ms:.1f}ms"
    )
    return (
        f"{n_reg} {reg_word}, {n_new} {new_word} "
        f"(threshold {data.threshold * 100:.0f}%, floor {floor})"
    )


# -- Rendering --------------------------------------------------------------


def render(tr, data: ReportData) -> None:
    """Write the speedguard section through TerminalReporter *tr*.

    Stays silent when there is nothing to report, except in verbose mode where a
    single clean-bill-of-health line is emitted.
    """
    if not data.regressions and not data.new_slow:
        if data.verbose:
            tr.write_sep("=", _SECTION_TITLE, cyan=True)
            if data.accepted:
                tr.write_line(
                    "baseline accepted; regression reporting suppressed"
                )
            else:
                tr.write_line("no duration regressions detected")
        return

    tr.write_sep("=", _SECTION_TITLE, cyan=True)

    if data.regressions:
        shown = data.regressions[: data.top]
        hidden = len(data.regressions) - len(shown)
        tr.write_line("Top regressions:", bold=True)
        rows = [
            [
                _truncate(r.nodeid),
                _fmt_secs(r.baseline),
                _fmt_secs(r.current),
                _fmt_pct(r.delta_ratio),
                str(r.samples),
            ]
            for r in shown
        ]
        for line in _render_table(
            ["test", "baseline", "current", "delta", "n"],
            rows,
            ["l", "r", "r", "r", "r"],
        ):
            tr.write_line("  " + line, red=True)
        if hidden > 0:
            tr.write_line(f"  ... and {hidden} more (raise --speedguard-top)")

    if data.new_slow:
        shown = data.new_slow[: data.top]
        hidden = len(data.new_slow) - len(shown)
        tr.write_line("New slow tests:", bold=True)
        rows = [[_truncate(r.nodeid), _fmt_secs(r.current)] for r in shown]
        for line in _render_table(
            ["test", "current"], rows, ["l", "r"]
        ):
            tr.write_line("  " + line, yellow=True)
        if hidden > 0:
            tr.write_line(f"  ... and {hidden} more (raise --speedguard-top)")

    tr.write_line(format_summary_line(data), bold=True)

    if data.fail_enabled and data.failing:
        tr.write_line(
            "session failed: duration regressions detected "
            "(--speedguard-fail)",
            red=True,
            bold=True,
        )
