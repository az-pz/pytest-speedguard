"""Unit tests for the terminal-report formatting helpers."""

from pytest_speedguard.report import (
    ReportData,
    ReportRow,
    format_summary_line,
    render,
)
from pytest_speedguard.report import _fmt_secs, _render_table


class FakeTR:
    """Minimal stand-in for pytest's TerminalReporter."""

    def __init__(self):
        self.lines = []

    def write_sep(self, sep, title, **markup):
        self.lines.append(f"=== {title} ===")

    def write_line(self, line, **markup):
        self.lines.append(line)

    @property
    def text(self):
        return "\n".join(self.lines)


def test_fmt_secs_milliseconds_and_seconds():
    assert _fmt_secs(0.05) == "50.0ms"
    assert _fmt_secs(1.5) == "1.500s"
    assert _fmt_secs(None) == "-"


def test_render_table_alignment():
    lines = _render_table(
        ["test", "n"], [["a", "1"], ["bbb", "22"]], ["l", "r"]
    )
    assert lines[0].split() == ["test", "n"]
    assert set(lines[1]) <= {"-", " "}  # separator row
    assert lines[2].split() == ["a", "1"]
    assert lines[3].split() == ["bbb", "22"]


def test_summary_line_pluralization():
    data = ReportData(
        regressions=[ReportRow("a", 0.2)],
        new_slow=[ReportRow("b", 0.3), ReportRow("c", 0.4)],
        threshold=0.5,
        min_duration=0.05,
    )
    assert (
        format_summary_line(data)
        == "1 regression, 2 new slow tests (threshold 50%, floor 50ms)"
    )


def test_render_quiet_when_nothing_to_report():
    tr = FakeTR()
    render(tr, ReportData(verbose=False))
    assert tr.lines == []


def test_render_verbose_clean_note():
    tr = FakeTR()
    render(tr, ReportData(verbose=True))
    assert any("no duration regressions" in ln for ln in tr.lines)


def test_render_regressions_and_summary():
    tr = FakeTR()
    data = ReportData(
        regressions=[ReportRow("test_x.py::test_a", 0.2, baseline=0.1, delta_ratio=1.0, samples=5)],
        threshold=0.5,
        min_duration=0.05,
    )
    render(tr, data)
    assert any("Top regressions" in ln for ln in tr.lines)
    assert any("test_x.py::test_a" in ln for ln in tr.lines)
    assert any("1 regression," in ln for ln in tr.lines)


def test_render_fail_note():
    tr = FakeTR()
    data = ReportData(
        regressions=[ReportRow("test_x.py::test_a", 0.2, baseline=0.1, delta_ratio=1.0, samples=5)],
        fail_enabled=True,
        failing=True,
    )
    render(tr, data)
    assert any("session failed" in ln for ln in tr.lines)
