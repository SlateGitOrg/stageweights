from __future__ import annotations

import os

import pandas as pd
import pytest

from stageweights import runner
from stageweights.synth import SynthConfig, generate, write_dataset


def test_dbt_build_passes_every_model_and_test_offline(full):
    result = full.result
    assert result.success and result.failures == []
    assert result.n_models == 13
    assert result.n_tests >= 50


def test_each_dataset_gets_its_own_target_directory_and_duckdb_file(full, homog):
    assert full.result.db_path != homog.result.db_path
    assert full.result.db_path.exists() and homog.result.db_path.exists()
    assert full.result.target_dir != homog.result.target_dir
    assert full.result.db_path.parent == full.result.target_dir
    assert (full.result.target_dir / "manifest.json").exists()


def test_the_run_leaves_the_project_directory_and_environment_clean(full):
    project = runner.dbt_project_dir()
    assert not (project / "target").exists()
    assert not (project / "logs").exists()
    assert runner.DB_PATH_ENV not in os.environ


def test_staging_removes_exact_duplicates_and_casts_to_utc(full):
    staged = full.frame("select count(*) n from stg_field_history")["n"][0]
    history = full.dataset.history
    assert staged == len(history.drop_duplicates())
    assert staged == len(history) - full.dataset.truth["n_duplicate_rows"]
    # 09:00-05:00 style offsets are gone: every instant sits in UTC.
    first = full.frame(
        "select min(changed_at) t from stg_field_history where opportunity_id = 'OPP-00001'"
    )["t"][0]
    raw = pd.Timestamp(history[history["opportunity_id"] == "OPP-00001"]["changed_at"].min())
    assert first == raw.tz_convert("UTC").tz_localize(None)


def test_replayed_intervals_tile_time_and_end_at_current_values(full):
    frame = full.frame(
        """
        select opportunity_id, field, interval_seq, valid_from, valid_to
        from int_opportunity_intervals order by opportunity_id, field, interval_seq
        """
    )
    assert len(frame) / 2400 > 8  # four fields, several changes each
    assert frame.groupby(["opportunity_id", "field"]).ngroups == 2400 * 4
    nxt = frame.groupby(["opportunity_id", "field"])["valid_from"].shift(-1)
    closed = frame["valid_to"].notna()
    assert (frame.loc[closed, "valid_to"] == nxt[closed]).all()
    assert frame["valid_to"].isna().sum() == 2400 * 4


def test_building_from_deduplicated_history_gives_identical_intervals(full, dedup):
    query = "select * from int_opportunity_intervals order by opportunity_id, field, interval_seq"
    pd.testing.assert_frame_equal(full.frame(query), dedup.frame(query))
    assert (
        len(dedup.dataset.history)
        == len(full.dataset.history) - (full.dataset.truth["n_duplicate_rows"])
    )


def test_a_broken_extract_fails_the_build(tmp_path):
    dataset = generate(SynthConfig(seed=5, n_opportunities=60))
    orphan = dataset.history.iloc[[0]].copy()
    orphan["opportunity_id"] = "OPP-99999"
    dataset.history = pd.concat([dataset.history, orphan], ignore_index=True)
    write_dataset(dataset, tmp_path / "broken")
    with pytest.raises(runner.BuildError, match="relationships"):
        runner.build(tmp_path / "broken", tmp_path / "work")
