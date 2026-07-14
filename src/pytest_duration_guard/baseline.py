"""Persistent rolling-baseline storage for :mod:`pytest_duration_guard`.

The baseline is a small JSON document holding, per test node id, a rolling
window of that test's most recent durations::

    {
      "schema": 1,
      "phase": "call",
      "tests": {
        "tests/test_x.py::test_a": {"samples": [0.101, 0.099, 0.102]},
        "tests/test_x.py::test_b[1]": {"samples": [0.5]}
      }
    }

Design guarantees:

* **Never crash on load.** A missing *or* corrupt/garbage file yields an empty
  baseline rather than an exception, so a bad file can't wedge a test run.
* **Atomic save.** We write to a temp file in the same directory and
  :func:`os.replace` it into place, so an interrupted run can't leave a
  half-written (corrupt) baseline.  ``os.replace`` is atomic on both POSIX and
  Windows.
* **Phase isolation.** The measured phase (``call`` vs ``total``) is stored;
  loading a baseline recorded under a different phase resets it, because the
  numbers are not comparable.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Optional

__all__ = ["Baseline", "SCHEMA_VERSION"]

#: On-disk schema version.  Bump if the JSON layout changes incompatibly.
SCHEMA_VERSION: int = 1


class Baseline:
    """A load/save wrapper around the per-test rolling-duration JSON file.

    :param path: Filesystem path to the baseline JSON file.
    :param phase: The timing phase these samples represent (``"call"`` or
        ``"total"``).  Persisted so a phase change invalidates cleanly.
    """

    def __init__(self, path: str, phase: str = "call") -> None:
        self.path = str(path)
        self.phase = phase
        self._tests: Dict[str, List[float]] = {}
        self._warnings: List[str] = []

    # -- Introspection ------------------------------------------------------

    @property
    def warnings(self) -> List[str]:
        """Non-fatal messages accumulated during :meth:`load` (e.g. reset)."""
        return list(self._warnings)

    def has(self, nodeid: str) -> bool:
        """Return whether *nodeid* already has baseline history."""
        return nodeid in self._tests

    def nodeids(self) -> List[str]:
        """Return all node ids currently tracked in the baseline."""
        return list(self._tests.keys())

    def get_samples(self, nodeid: str) -> List[float]:
        """Return a copy of the recorded samples for *nodeid* (``[]`` if none)."""
        return list(self._tests.get(nodeid, []))

    # -- Mutation -----------------------------------------------------------

    def record(self, nodeid: str, duration: float, window: int) -> List[float]:
        """Append *duration* to *nodeid*'s window, trimmed to the last *window*.

        :param window: Maximum samples to retain.  Values ``<= 0`` disable
            trimming (keep everything).
        :returns: The (trimmed) sample list for *nodeid*.
        """
        samples = self._tests.setdefault(nodeid, [])
        samples.append(float(duration))
        if window > 0 and len(samples) > window:
            # Keep only the most recent `window` samples.
            del samples[: len(samples) - window]
        return samples

    # -- Persistence --------------------------------------------------------

    def load(self) -> "Baseline":
        """Populate this baseline from disk; tolerate missing/corrupt files.

        A missing file, unreadable file, or malformed JSON all result in an
        empty (but usable) baseline.  A stored phase different from the
        configured one triggers a reset and records a warning.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # Missing, unreadable, or corrupt JSON -> start fresh, never crash.
            self._tests = {}
            return self

        if not isinstance(data, dict):
            self._tests = {}
            return self

        stored_phase = data.get("phase", self.phase)
        if stored_phase != self.phase:
            self._warnings.append(
                f"baseline phase changed ({stored_phase!r} -> {self.phase!r}); "
                "resetting samples"
            )
            self._tests = {}
            return self

        parsed: Dict[str, List[float]] = {}
        tests = data.get("tests", {})
        if isinstance(tests, dict):
            for nodeid, entry in tests.items():
                if not isinstance(entry, dict):
                    continue
                raw = entry.get("samples", [])
                if not isinstance(raw, list):
                    continue
                clean = [
                    float(s)
                    for s in raw
                    if isinstance(s, (int, float)) and not isinstance(s, bool)
                ]
                parsed[nodeid] = clean
        self._tests = parsed
        return self

    def to_dict(self) -> Dict[str, object]:
        """Return the JSON-serialisable representation of this baseline."""
        return {
            "schema": SCHEMA_VERSION,
            "phase": self.phase,
            "tests": {
                nodeid: {"samples": samples}
                for nodeid, samples in sorted(self._tests.items())
            },
        }

    def save(self) -> "Baseline":
        """Atomically persist this baseline to :attr:`path`.

        Writes to a uniquely named temp file in the destination directory then
        :func:`os.replace`\\ s it over the target, so readers never observe a
        partially written file and an interrupted run cannot corrupt it.
        """
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".baseline-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            # Don't leave a stray temp file behind on any failure.
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        return self

    def raw_bytes(self) -> Optional[bytes]:
        """Return the current on-disk bytes, or ``None`` if the file is absent.

        Useful for callers that want to detect whether a save actually changed
        anything.
        """
        try:
            with open(self.path, "rb") as fh:
                return fh.read()
        except OSError:
            return None
