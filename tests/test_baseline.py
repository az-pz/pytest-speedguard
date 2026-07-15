"""Unit tests for the JSON baseline store."""

import json

from pytest_speedguard.baseline import SCHEMA_VERSION, Baseline


def test_missing_file_loads_empty(tmp_path):
    b = Baseline(str(tmp_path / "nope.json")).load()
    assert b.nodeids() == []
    assert b.get_samples("x") == []


def test_corrupt_file_loads_empty(tmp_path):
    p = tmp_path / "baseline.json"
    p.write_text("this is not json {{{{", encoding="utf-8")
    b = Baseline(str(p)).load()
    assert b.nodeids() == []


def test_non_dict_json_loads_empty(tmp_path):
    p = tmp_path / "baseline.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    b = Baseline(str(p)).load()
    assert b.nodeids() == []


def test_save_load_round_trip(tmp_path):
    p = tmp_path / "baseline.json"
    b = Baseline(str(p))
    b.record("test_a.py::test_1", 0.10, window=20)
    b.record("test_a.py::test_1", 0.11, window=20)
    b.record("test_a.py::test_2", 0.50, window=20)
    b.save()

    loaded = Baseline(str(p)).load()
    assert loaded.get_samples("test_a.py::test_1") == [0.10, 0.11]
    assert loaded.get_samples("test_a.py::test_2") == [0.50]


def test_saved_schema_and_phase(tmp_path):
    p = tmp_path / "baseline.json"
    Baseline(str(p), phase="total").save()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["schema"] == SCHEMA_VERSION
    assert data["phase"] == "total"


def test_window_trims_to_last_n(tmp_path):
    b = Baseline(str(tmp_path / "b.json"))
    for i in range(25):
        b.record("nid", float(i), window=20)
    samples = b.get_samples("nid")
    assert len(samples) == 20
    assert samples == [float(i) for i in range(5, 25)]


def test_window_zero_keeps_all(tmp_path):
    b = Baseline(str(tmp_path / "b.json"))
    for i in range(5):
        b.record("nid", float(i), window=0)
    assert b.get_samples("nid") == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_atomic_write_leaves_no_temp_file(tmp_path):
    p = tmp_path / "baseline.json"
    b = Baseline(str(p))
    b.record("nid", 0.1, window=20)
    b.save()
    assert p.exists()
    leftovers = list(tmp_path.glob(".baseline-*.tmp"))
    assert leftovers == []


def test_save_creates_parent_directories(tmp_path):
    p = tmp_path / "nested" / "dir" / "baseline.json"
    Baseline(str(p)).save()
    assert p.exists()


def test_phase_mismatch_resets_and_warns(tmp_path):
    p = tmp_path / "baseline.json"
    seed = Baseline(str(p), phase="call")
    seed.record("nid", 0.1, window=20)
    seed.save()

    reloaded = Baseline(str(p), phase="total").load()
    assert reloaded.nodeids() == []
    assert any("phase changed" in w for w in reloaded.warnings)


def test_phase_match_keeps_samples(tmp_path):
    p = tmp_path / "baseline.json"
    seed = Baseline(str(p), phase="call")
    seed.record("nid", 0.1, window=20)
    seed.save()

    reloaded = Baseline(str(p), phase="call").load()
    assert reloaded.get_samples("nid") == [0.1]
    assert reloaded.warnings == []


def test_record_trims_and_returns_samples(tmp_path):
    b = Baseline(str(tmp_path / "b.json"))
    returned = b.record("nid", 1.0, window=2)
    assert returned == [1.0]
    b.record("nid", 2.0, window=2)
    returned = b.record("nid", 3.0, window=2)
    assert returned == [2.0, 3.0]
