from __future__ import annotations

from decimal import Decimal

import pytest

from stageweights import fiscal
from stageweights.synth import CRM_DEFAULT_PERCENT

OOS = list(range(4, 12))


def test_backtest_scores_the_eight_out_of_sample_quarters(full):
    frame = full.frame("select quarter_index, fiscal_quarter from fct_forecast_backtest order by 1")
    assert list(frame["fiscal_quarter"]) == [
        "FY2024 Q1",
        "FY2024 Q2",
        "FY2024 Q3",
        "FY2024 Q4",
        "FY2025 Q1",
        "FY2025 Q2",
        "FY2025 Q3",
        "FY2025 Q4",
    ]


def test_actual_and_default_forecast_are_recomputed_from_the_replay(full):
    rows = full.rows("select * from fct_forecast_backtest order by quarter_index")
    for row in rows:
        start = fiscal.utc_midnight(fiscal.quarter_start(row.quarter_index))
        end = fiscal.utc_midnight(fiscal.quarter_end(row.quarter_index))
        pipeline = full.oracle.open_pipeline_at(start)
        actual = Decimal(0)
        default = Decimal(0)
        rep = Decimal(0)
        for oid, state in pipeline.items():
            if full.oracle.state_at(oid, end).stage == "Closed Won":
                actual += full.oracle.state_at(oid, end).amount
            default += state.amount * Decimal(CRM_DEFAULT_PERCENT[state.stage]) / 100
            rep += state.amount * state.rep_probability
        assert row.n_deals == len(pipeline)
        assert row.actual_won == pytest.approx(float(actual))
        assert row.forecast_default == pytest.approx(float(default))
        assert row.forecast_rep == pytest.approx(float(rep))
        assert row.ape_default == pytest.approx(
            abs(row.forecast_default - row.actual_won) / row.actual_won
        )
        assert row.bias_learned == pytest.approx(
            (row.forecast_learned - row.actual_won) / row.actual_won
        )


def test_learned_forecast_applies_that_quarters_weights_to_each_deal(full):
    frame = full.frame(
        """
        select s.quarter_index, sum(s.amount * w.weight) expected
        from fct_pipeline_snapshots s
        join fct_stage_weights w on w.forecast_quarter_index = s.quarter_index
            and w.stage = s.stage and w.push_bucket = s.push_bucket
        where s.quarter_index >= 4 group by 1 order by 1
        """
    )
    backtest = full.frame(
        "select forecast_learned from fct_forecast_backtest order by quarter_index"
    )
    assert list(backtest["forecast_learned"]) == pytest.approx(list(frame["expected"]))


def test_learned_weights_have_the_lowest_error_and_the_crm_default_runs_hot(full):
    frame = full.frame("select * from fct_forecast_backtest")
    mape = {m: frame[f"ape_{m}"].mean() for m in ("default", "rep", "learned")}
    assert mape["learned"] < mape["default"] and mape["learned"] < mape["rep"]
    assert mape["learned"] < 0.6 * min(mape["default"], mape["rep"])
    assert frame["bias_default"].mean() >= 0.20
    assert (frame["bias_default"] > 0).all()


def test_calibration_and_brier_score_favour_learned_weights(full):
    cal = full.frame("select * from fct_calibration").set_index("method")
    assert cal.loc["learned", "brier"] < cal.loc["crm_default", "brier"]
    assert cal.loc["learned", "brier"] < cal.loc["rep_entered", "brier"]
    assert abs(cal.loc["learned", "mean_gap"]) <= 0.03
    assert cal.loc["crm_default", "mean_gap"] >= 0.10
    assert cal["n_deals"].nunique() == 1


def test_brier_score_is_recomputed_from_deal_level_probabilities(full):
    deals = full.frame(
        """
        select s.won_in_quarter::int won, s.crm_default_probability::double p_default,
            s.rep_probability::double p_rep, w.weight p_learned
        from fct_pipeline_snapshots s
        join fct_stage_weights w on w.forecast_quarter_index = s.quarter_index
            and w.stage = s.stage and w.push_bucket = s.push_bucket
        where s.quarter_index >= 4
        """
    )
    cal = full.frame("select * from fct_calibration").set_index("method")
    for method, column in (
        ("crm_default", "p_default"),
        ("rep_entered", "p_rep"),
        ("learned", "p_learned"),
    ):
        brier = ((deals[column] - deals["won"]) ** 2).mean()
        assert cal.loc[method, "brier"] == pytest.approx(brier)
        assert cal.loc[method, "mean_predicted"] == pytest.approx(deals[column].mean())
        assert cal.loc[method, "realised_rate"] == pytest.approx(deals["won"].mean())
