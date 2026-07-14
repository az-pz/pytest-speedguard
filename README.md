# pytest-duration-guard

[![CI](https://github.com/az-pz/pytest-duration-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/az-pz/pytest-duration-guard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pytest-duration-guard.svg)](https://pypi.org/project/pytest-duration-guard/)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-duration-guard.svg)](https://pypi.org/project/pytest-duration-guard/)

A [pytest](https://docs.pytest.org/) plugin that **passively** tracks every
test's wall-clock duration across runs, keeps a noise-tolerant rolling baseline,
and flags tests that **regress** (get meaningfully slower) or newly appear as
slow. Cross-platform, with **zero runtime dependencies beyond pytest** — the
statistics are implemented in pure Python.

> Badges reflect live state: CI shows the real GitHub Actions result and the
> PyPI badge shows the real published version (unpublished = "not found").

## The problem: test suites rot silently

A suite creeps from 2 minutes to 12 minutes over a year and nobody notices the
single test that quietly got 5× slower. Each individual run looks "basically the
same as last time," so the slowdown hides in the noise until CI is painfully
slow and the culprit commit is long gone.

### Why `pytest-benchmark` doesn't cover this

[`pytest-benchmark`](https://pypi.org/project/pytest-benchmark/) is excellent,
but it solves a *different* problem: **deliberate microbenchmarks** you write by
hand for a handful of hot functions (`benchmark(func, ...)`). It won't tell you
that `test_import_users` in your integration suite doubled last Tuesday, because
you never wrote a benchmark for it.

`pytest-duration-guard` is **passive and whole-suite**: it watches *every* test
you already have, needs **no code changes**, and answers a different question —
"did anything in my existing suite get slower?"

## Install

```bash
pip install pytest-duration-guard
```

The plugin auto-registers via pytest's `pytest11` entry point — nothing else to
configure.

## Quickstart

```bash
# 1. Run once to seed a baseline (every test is "new" — nothing to compare yet).
pytest

# 2. ... time passes, code changes ...

# 3. Run again. Tests that got meaningfully slower are flagged.
pytest
```

Example output when something regressed:

```
=============================== duration guard ================================
Top regressions:
  test                          baseline  current    delta  n
  ----------------------------  --------  -------  -------  -
  tests/test_api.py::test_sync   208.0ms  504.5ms  +142.5%  8
1 regression, 0 new slow tests (threshold 50%, floor 50ms)
```

By default this is **informational** (it never fails your build). Add
`--duration-guard-fail` to turn it into a CI gate.

## How the statistical model works (and why)

For each test, the plugin keeps a **rolling window** of recent durations. A test
is flagged as a regression **only if all four guards agree**:

1. **Noise floor** — `current >= min-duration` (default **50 ms**). Sub-50ms
   timings are dominated by scheduler jitter; a test going from 1ms to 3ms is
   noise, not a regression. *Kills tiny-fast-test false positives.*
2. **Warm-up guard** — there must be at least `min-samples` (default **3**)
   baseline samples. With fewer, the test is still "warming up" and can't be
   judged. *Stops a brand-new or barely-seen test from firing on its second
   run.*
3. **Relative guard** — `current > median × (1 + threshold)` (default
   **+50%**). The robust **median** of the window is the reference, not the
   mean, so one slow outlier in the history doesn't move the bar.
4. **Absolute statistical guard** — `current > median + noise_factor ×
   scaled_mad` (default `noise_factor` **3.0**). `scaled_mad = 1.4826 × MAD`
   (median absolute deviation) is a robust, outlier-resistant estimate of the
   standard deviation. This demands the jump be larger than the test's *own*
   normal run-to-run jitter. *Kills CI-runner-noise false positives on tests
   that are naturally variable.* When dispersion is zero (few or identical
   samples) this guard is skipped and the decision rests on the relative guard,
   so we never divide by — or flag on — zero dispersion.

Using **median + MAD** instead of **mean + standard deviation** is deliberate:
test timings on shared CI runners are heavy-tailed, and robust statistics ignore
the occasional 10× outlier from a noisy neighbour instead of letting it poison
the baseline.

**New slow tests** (no baseline history yet) whose duration is at or above the
floor are listed separately so you notice expensive newcomers, but they are
never counted as regressions.

## Options

Every option is available on the command line **and** as an `ini` key (in
`pytest.ini`, `tox.ini`, or `pyproject.toml`'s `[tool.pytest.ini_options]`).
The command line wins over `ini`.

| CLI flag | ini key | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `--duration-guard` / `--no-duration-guard` | `duration_guard` | bool | `true` | Master switch. |
| `--duration-guard-threshold` | `duration_guard_threshold` | float | `0.5` | Relative regression threshold (`0.5` = +50%). |
| `--duration-guard-min-duration` | `duration_guard_min_duration` | float (s) | `0.05` | Ignore tests faster than this (noise floor). |
| `--duration-guard-noise-factor` | `duration_guard_noise_factor` | float | `3.0` | Require `current > median + factor × scaled_mad`. |
| `--duration-guard-window` | `duration_guard_window` | int | `20` | Rolling window size (samples kept per test). |
| `--duration-guard-min-samples` | `duration_guard_min_samples` | int | `3` | Minimum baseline samples before flagging (warm-up). |
| `--duration-guard-phase` | `duration_guard_phase` | `call` \| `total` | `call` | Time the test body (`call`) or `setup+call+teardown` (`total`). |
| `--duration-guard-update` | `duration_guard_update` | `auto` \| `always` \| `never` | `auto` | Baseline update policy (see below). |
| `--duration-guard-accept` | — | flag | off | Re-baseline: record all current durations, suppress reporting, don't fail. |
| `--duration-guard-fail` | `duration_guard_fail` | bool | `false` | Fail the session (non-zero exit) on any regression — the CI gate. |
| `--duration-guard-baseline` | `duration_guard_baseline` | path | `.pytest_duration_guard/baseline.json` | Baseline file location. |
| `--duration-guard-top` | — | int | `20` | Max rows per report section. |

## Update modes and `--accept`

Controls how the baseline window is updated after a run:

- **`auto`** (default) — append the new sample to the rolling window **only if
  the test did *not* regress**. A genuine regression therefore keeps firing on
  every run until it's fixed or explicitly accepted, instead of being silently
  absorbed into the baseline.
- **`always`** — always append (a pure rolling window). Simpler, but it only
  ever catches *sudden* spikes, because gradual slowdowns are continuously
  absorbed.
- **`never`** — compare only, never write. The baseline is **frozen**.
  Recommended for CI against a committed baseline file (see below).

**`--duration-guard-accept`** is the deliberate way to move the baseline after an
*intended* slowdown: it records **all** current durations as the new baseline,
suppresses regression reporting for that run, and won't fail on regressions.

```bash
# You intentionally made things slower and that's fine — accept the new normal:
pytest --duration-guard-accept
```

## Known limitation: slow-creep under the per-run threshold

A **rolling** baseline (`auto`/`always`) compares each run against *recent*
history. If a test creeps up by, say, 3% every week — always staying under the
per-run threshold — each run looks fine while the baseline slowly drifts up with
it. Over a year that's a large regression that was never flagged, because no
*single* run ever crossed the line.

**Mitigation — freeze the reference in CI:**

1. Seed and commit a baseline file to your repo:
   ```bash
   pytest --duration-guard-update=always   # build up samples over a few runs
   git add .pytest_duration_guard/baseline.json
   git commit -m "Freeze test duration baseline"
   ```
2. In CI, compare against that fixed reference and never rewrite it:
   ```bash
   pytest --duration-guard-update=never --duration-guard-fail
   ```

Now drift is measured against a **fixed** point in time, so slow-creep
accumulates against a stable reference and is caught. Re-baseline deliberately
(`--duration-guard-accept`, then commit the file) whenever you accept a new
normal.

## Parallel runs (pytest-xdist)

`pytest-duration-guard` is **xdist-aware** in v1 via **controller-side
aggregation**. Workers just run tests and stream their reports back to the
controller; all comparison, reporting, and baseline writing happen **only on the
controller** (guarded by `hasattr(config, "workerinput")`), so there are no
racing writers and the baseline stays consistent. Just run pytest as usual:

```bash
pytest -n auto
```

## Baseline JSON schema

The baseline is a small, human-readable JSON file written **atomically** (temp
file + `os.replace`) so an interrupted run can never corrupt it. A missing or
corrupt file is treated as an empty baseline (it never crashes your run).

```json
{
  "schema": 1,
  "phase": "call",
  "tests": {
    "tests/test_api.py::test_sync": { "samples": [0.201, 0.199, 0.204] },
    "tests/test_api.py::test_list[page-2]": { "samples": [0.512] }
  }
}
```

- `schema` — on-disk format version.
- `phase` — which timing phase the samples represent (`call` or `total`).
  Loading a baseline recorded under a different phase resets it, since the
  numbers aren't comparable.
- `tests` — a map of pytest node id → rolling window of recent durations
  (seconds). Parametrized tests have distinct node ids (e.g. `test_x[a-1]`) and
  are tracked separately.

## Development

```bash
pip install -e .[dev]
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
