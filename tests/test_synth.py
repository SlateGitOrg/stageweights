from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stageweights import fiscal
from stageweights.oracle import parse_utc
from stageweights.synth import (
    CRM_DEFAULT_PERCENT,
    DROP_TICK,
    OPEN_STAGES,
    OPTIMISTIC_REPS,
    SEGMENTS,
    SynthConfig,
    generate,
    transition_matrix,
    true_win_in_quarter,
)


def test_same_seed_gives_identical_tables_and_other_seeds_differ():
    first = generate(SynthConfig(seed=3, n_opportunities=150))
    again = generate(SynthConfig(seed=3, n_opportunities=150))
    other = generate(SynthConfig(seed=4, n_opportunities=150))
    pd.testing.assert_frame_equal(first.history, again.history)
    pd.testing.assert_frame_equal(first.opportunities, again.opportunities)
    assert not first.opportunities.equals(other.opportunities)


def test_fiscal_quarters_are_13_weeks_from_monday_2_january_2023():
    assert fiscal.FISCAL_START == date(2023, 1, 2)
    assert fiscal.FISCAL_START.weekday() == 0
    assert fiscal.quarter_start(1) - fiscal.quarter_start(0) == timedelta(weeks=13)
    assert fiscal.quarter_start(11) == date(2025, 9, 29)
    assert all(fiscal.quarter_start(q).weekday() == 0 for q in range(12))
    assert [fiscal.quarter_label(q) for q in (0, 4, 11)] == ["FY2023 Q1", "FY2024 Q1", "FY2025 Q4"]


def test_default_dataset_has_the_documented_shape(full):
    users, opps = full.dataset.users, full.dataset.opportunities
    assert len(opps) == 2400
    assert 15_000 < len(full.dataset.history) < 30_000
    assert users.groupby("segment").size().to_dict() == {
        "enterprise": 6,
        "mid_market": 6,
        "smb": 6,
    }
    assert len(users) == 18
    assert set(full.dataset.history["field"]) == {
        "stage",
        "amount",
        "close_date",
        "rep_probability",
    }
    assert set(opps["created_stage"]) <= set(OPEN_STAGES)


def test_two_percent_of_history_rows_are_exact_duplicates(full):
    history = full.dataset.history
    n_dup = int(history.duplicated().sum())
    assert n_dup == full.dataset.truth["n_duplicate_rows"]
    assert abs(n_dup / (len(history) - n_dup) - 0.02) < 0.006


def test_optimistic_reps_enter_probabilities_25_points_above_default(full):
    opps = full.dataset.opportunities
    default = opps["created_stage"].map(CRM_DEFAULT_PERCENT)
    gap = opps["created_rep_probability"] - default
    optimistic = opps["owner_id"].isin(OPTIMISTIC_REPS)
    assert abs(gap[optimistic].mean() - 25) < 2.5
    assert abs(gap[~optimistic].mean()) < 1.5


def test_a_quarter_of_deals_are_slippers_and_close_dates_move_in_30_day_pushes(full):
    share = len(full.dataset.truth["slipper_opportunities"]) / 2400
    assert 0.22 < share < 0.28

    history = full.dataset.history.drop_duplicates()
    closings = history[history["new_value"].isin(["Closed Won", "Closed Lost"])]
    closing_keys = set(zip(closings["opportunity_id"], closings["changed_at"], strict=True))
    changes = history[history["field"] == "close_date"]
    keys = zip(changes["opportunity_id"], changes["changed_at"], strict=True)
    changes = changes[[key not in closing_keys for key in keys]]
    old = pd.to_datetime(changes["old_value"])
    new = pd.to_datetime(changes["new_value"])
    pushes = (new - old)[new > old]
    assert len(pushes) > 1_000
    assert set(pushes) == {pd.Timedelta(days=30)}


def test_amounts_follow_segment_medians(full):
    opps = full.dataset.opportunities.merge(
        full.dataset.users, left_on="owner_id", right_on="user_id"
    )
    medians = opps.groupby("segment")["created_amount"].median()
    assert 15_000 < medians["smb"] < 22_000
    assert 40_000 < medians["mid_market"] < 58_000
    assert 110_000 < medians["enterprise"] < 175_000


def test_true_win_in_quarter_is_the_13th_power_of_the_transition_matrix():
    # Independent route: push a probability vector through 13 weekly steps.
    for segment in SEGMENTS:
        matrix = transition_matrix(segment)
        assert np.allclose(matrix.sum(axis=1), 1.0)
        for idx, stage in enumerate(OPEN_STAGES):
            vector = np.zeros(8)
            vector[idx] = 1.0
            for _ in range(13):
                vector = vector @ matrix
            assert true_win_in_quarter(stage, segment) == pytest.approx(vector[6], abs=1e-12)


def test_true_rates_match_the_documented_values():
    assert abs(true_win_in_quarter("Proposal", "mid_market") - 0.31) < 0.02
    assert abs(true_win_in_quarter("Negotiation", "enterprise") - 0.55) < 0.02
    assert abs(true_win_in_quarter("Negotiation", "enterprise", dropped=True) - 0.35) < 0.02
    ordered = [true_win_in_quarter(stage, "smb") for stage in OPEN_STAGES]
    assert ordered == sorted(ordered)


def test_switching_off_the_late_change_leaves_earlier_data_identical(full, nodrop):
    cut = datetime.combine(
        fiscal.FISCAL_START + timedelta(days=7 * DROP_TICK), datetime.min.time(), UTC
    )

    def before(frame):
        stamp = frame["changed_at"].map(parse_utc)
        return frame[stamp < cut].reset_index(drop=True)

    early_on, early_off = before(full.dataset.history), before(nodrop.dataset.history)
    assert len(early_on) > 10_000
    pd.testing.assert_frame_equal(early_on, early_off)
    pd.testing.assert_frame_equal(
        full.dataset.opportunities[["opportunity_id", "created_at", "created_amount"]],
        nodrop.dataset.opportunities[["opportunity_id", "created_at", "created_amount"]],
    )
    assert not full.dataset.history.equals(nodrop.dataset.history)


def test_homogeneous_mode_has_one_segment_and_no_planted_effects(homog):
    truth, opps = homog.dataset.truth, homog.dataset.opportunities
    owners = homog.dataset.users.set_index("user_id").loc[opps["owner_id"], "segment"]
    assert len(opps) == 6000
    assert set(owners) == {"mid_market"}
    assert truth["slipper_opportunities"] == [] and truth["planted_jumps"] == []
    assert truth["optimistic_reps"] == [] and truth["jumper_rep"] is None
    gap = opps["created_rep_probability"] - opps["created_stage"].map(CRM_DEFAULT_PERCENT)
    assert abs(gap.mean()) < 1.5
