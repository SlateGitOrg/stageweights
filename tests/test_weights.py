from __future__ import annotations

import math

import pandas as pd
import pytest

from stageweights.synth import CRM_DEFAULT_PERCENT, OPEN_STAGES, true_win_in_quarter

Z90 = 1.6448536269514722
LAST = 11


def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    scale = 1 + z * z / n
    return (centre - spread) / scale, (centre + spread) / scale


def test_wilson_reference_value_and_sql_bounds_agree(full):
    low, high = wilson(50, 100)
    assert (round(low, 4), round(high, 4)) == (0.4188, 0.5812)
    cells = full.frame(
        """
        select n_rows, n_won, wilson_lo, wilson_hi from fct_stage_cell_rates
        where n_rows > 0 and forecast_quarter_index = 11
        """
    )
    assert len(cells) >= 15
    for row in cells.itertuples():
        expected = wilson(int(row.n_won), int(row.n_rows))
        assert row.wilson_lo == pytest.approx(expected[0], abs=1e-12)
        assert row.wilson_hi == pytest.approx(expected[1], abs=1e-12)


def test_cell_counts_are_rebuilt_from_closed_quarters_only(full):
    snaps = full.frame(
        "select quarter_index, stage, push_bucket, won_in_quarter::int w "
        "from fct_pipeline_snapshots"
    )
    cells = full.frame(
        "select forecast_quarter_index f, stage, push_bucket, n_rows, n_won "
        "from fct_stage_cell_rates"
    )
    assert cells["f"].nunique() == 12 and len(cells) == 12 * 18
    for row in cells.itertuples():
        trained = snaps[
            (snaps["quarter_index"] < row.f)
            & (snaps["stage"] == row.stage)
            & (snaps["push_bucket"] == row.push_bucket)
        ]
        assert (row.n_rows, row.n_won) == (len(trained), trained["w"].sum())


def test_thin_cells_fall_back_to_the_stage_rate_then_to_the_crm_default(full):
    weights = full.frame("select * from fct_stage_weights")
    cell = weights[weights["weight_source"] == "cell"]
    stage = weights[weights["weight_source"] == "stage"]
    default = weights[weights["weight_source"] == "crm_default"]
    assert (cell["cell_n_rows"] >= 30).all()
    assert ((stage["cell_n_rows"] < 30) & (stage["stage_n_rows"] >= 30)).all()
    assert (stage["weight"] == stage["stage_rate"]).all()
    assert (default["stage_n_rows"] < 30).all()
    assert (default["weight"] == default["crm_default_probability"]).all()
    # The first forecast quarter has nothing to learn from: every weight is the CRM default.
    first = weights[weights["forecast_quarter_index"] == 0]
    assert (first["weight_source"] == "crm_default").all() and (first["stage_n_rows"] == 0).all()
    for stage_name, percent in CRM_DEFAULT_PERCENT.items():
        got = first[first["stage"] == stage_name]["weight"]
        assert got.eq(percent / 100).all()
    assert {"cell", "stage", "crm_default"} <= set(weights["weight_source"])


def test_homogeneous_mode_recovers_the_exact_win_in_quarter_probability(homog):
    stage_rates = homog.frame(
        f"""
        select stage, max(stage_rate) rate, max(stage_n_rows) n from fct_stage_weights
        where forecast_quarter_index = {LAST} group by stage
        """
    ).set_index("stage")
    assert len(stage_rates) == 6
    for stage in OPEN_STAGES:
        truth = true_win_in_quarter(stage, "mid_market")
        assert stage_rates.loc[stage, "n"] > 300
        assert abs(stage_rates.loc[stage, "rate"] - truth) <= 0.04, stage


def test_homogeneous_cell_weights_and_their_wilson_bounds_cover_the_truth(homog):
    cells = homog.frame(
        f"""
        select stage, weight, weight_lo, weight_hi from fct_stage_weights
        where forecast_quarter_index = {LAST} and weight_source = 'cell'
        """
    )
    assert len(cells) >= 12
    truth = cells["stage"].map(lambda s: true_win_in_quarter(s, "mid_market"))
    covered = (cells["weight_lo"] <= truth) & (truth <= cells["weight_hi"])
    assert covered.mean() >= 0.8
    # In this mode nothing depends on push count, so cells with plenty of rows sit near the truth.
    assert ((cells["weight"] - truth).abs() <= 0.07).all()


def test_full_mode_learns_a_proposal_weight_far_below_the_crm_default(full):
    proposal = full.frame(
        f"""
        select push_bucket, weight, stage_rate from fct_stage_weights
        where forecast_quarter_index = {LAST} and stage = 'Proposal'
        """
    )
    assert (proposal["weight"] <= 0.60 - 0.15).all()
    assert proposal["stage_rate"].iloc[0] <= 0.45


def test_deals_pushed_three_times_win_at_under_half_the_rate_of_unpushed_deals(full):
    rates = full.frame(
        """
        select stage, push_bucket, count(*) n, avg(won_in_quarter::int) rate
        from fct_pipeline_snapshots where stage in ('Proposal', 'Negotiation')
        group by stage, push_bucket
        """
    ).set_index(["stage", "push_bucket"])
    for stage in ("Proposal", "Negotiation"):
        assert rates.loc[(stage, "3+"), "n"] >= 50
        assert rates.loc[(stage, "3+"), "rate"] < 0.5 * rates.loc[(stage, "0"), "rate"]


def test_a_change_that_exists_only_in_later_data_cannot_move_earlier_weights(full, nodrop):
    query = """
        select * from fct_stage_weights
        where forecast_quarter_index <= 10
        order by forecast_quarter_index, stage_order, push_bucket
    """
    pd.testing.assert_frame_equal(full.frame(query), nodrop.frame(query))
    cells = "select * from fct_stage_cell_rates where forecast_quarter_index <= 10 order by 1, 4, 5"
    pd.testing.assert_frame_equal(full.frame(cells), nodrop.frame(cells))


def test_the_planted_change_shows_up_in_the_following_quarters(full, nodrop):
    """The weight table pools segments, so a change confined to enterprise Negotiation is diluted.

    The pooled Negotiation weight for FY2025 Q4 therefore moves only slightly (about 0.453 with the
    change against 0.456 without); the segment-level effect is visible in the snapshot win rates.
    """
    q = "forecast_quarter_index = 11 and stage = 'Negotiation'"
    on = full.frame(f"select weight, stage_rate from fct_stage_weights where {q}")
    off = nodrop.frame(f"select weight, stage_rate from fct_stage_weights where {q}")
    assert on["stage_rate"].iloc[0] < off["stage_rate"].iloc[0]
    assert not on.equals(off)

    def enterprise_negotiation(built):
        return built.frame(
            """
            select avg(won_in_quarter::int) r, count(*) n from fct_pipeline_snapshots
            where quarter_index in (10, 11) and stage = 'Negotiation' and segment = 'enterprise'
            """
        ).iloc[0]

    with_change, without = enterprise_negotiation(full), enterprise_negotiation(nodrop)
    assert with_change["n"] >= 15 and without["n"] >= 15
    assert with_change["r"] < without["r"] - 0.1
