"""Review regressions for model attribution boundaries and wire overflow."""
import pytest

from throughput_under_test import sampler as S, series as SE
from throughput_under_test.store import Store
from test_models import M, make_db, local_sampler, step, metric_rows


def test_wire_other_survives_sampling_and_top8_overflow(store):
    models = {f"m{i}": 100.0 for i in range(10)} | {"other": 1000.0}
    store.observe_token_counter("worker", "tokens_io", M, 2000, 1, models)
    models = {m: v + 10 for m, v in models.items()}
    store.observe_token_counter("worker", "tokens_io", M + 60, 2110, 2, models)
    p = SE.build(store, {}, ["worker"], "hub", "1h", now=M + 60, by="model")
    assert p["models"] == [f"m{i}" for i in range(8)] + ["other", "unreported"]
    assert p["series"]["tokens_io"]["other"][-1] == 30  # wire overflow + two ranked-out names
    assert sum(a[-1] for a in p["series"]["tokens_io"].values()) == 110


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "series.sqlite")


@pytest.mark.parametrize("left,right", [(" model", "model "), ("x" * 128 + "a", "x" * 128 + "b"), (" ", "unknown")])
def test_registry_normalized_collisions_sum_before_differencing(store, left, right):
    store.observe_token_counter("worker", "tokens_io", M, 300, 1, {left: 100, right: 200})
    store.observe_token_counter("worker", "tokens_io", M + 60, 360, 2, {left: 140, right: 220})
    rows = metric_rows(store, "worker")
    assert rows["unreported"][M] == 0
    assert sum(r[M] for k, r in rows.items() if k not in ("", "unreported")) == 60


def test_first_model_deploy_keeps_scalar_gap_without_model_backfill(store):
    store.observe_counter("worker", "tokens_io", M - 120, 100, 1)
    store.observe_token_counter("worker", "tokens_io", M, 160, 2, {"a": 160})
    rows = metric_rows(store, "worker")
    assert rows == {"": {M - 120: 30, M - 60: 30}}
    store.observe_token_counter("worker", "tokens_io", M + 60, 180, 3, {"a": 180})
    rows = metric_rows(store, "worker")
    assert rows["a"] == {M: 20}
    assert rows["unreported"] == {M: 0}


def test_model_read_failure_recovery_does_not_steal_another_models_interval(tmp_path, monkeypatch):
    root, smp, clock = local_sampler(tmp_path)
    path = root / "state.db"
    make_db(path, [("s", 100, 0)], [("s", "old", 100, 0, "p", "")])
    smp.tick()
    real = S.model_snapshot
    monkeypatch.setattr(S, "model_snapshot", lambda dbs: ({}, set()))
    make_db(path, [("s", 200, 0)], [("s", "old", 200, 0, "p", "")])
    step(smp, clock, 1)
    monkeypatch.setattr(S, "model_snapshot", real)
    make_db(path, [("s", 210, 0)], [("s", "new", 10, 0, "p", "")])
    step(smp, clock, 2)
    rows = metric_rows(smp.store, "hub")
    assert rows["unreported"] == {M: 100, M + 60: 10}
    assert "old" not in rows and "new" not in rows  # recovery is a fresh model baseline
    make_db(path, [("s", 215, 0)], [("s", "new", 15, 0, "p", "")])
    step(smp, clock, 3)
    rows = metric_rows(smp.store, "hub")
    assert rows["new"] == {M + 120: 5}
    assert rows["unreported"][M + 120] == 0
