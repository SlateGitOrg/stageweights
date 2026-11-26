"""Read the built marts back as data frames (shared by the CLI and the tests)."""

from __future__ import annotations

import duckdb
import pandas as pd

BACKTEST_SQL = "select * from fct_forecast_backtest order by quarter_index"

HYGIENE_FLAGS = (
    ("flag_pushes_3plus", "3+ pushes"),
    ("flag_stage_regression", "regression"),
    ("flag_stale_60d", "stale 60d"),
    ("flag_close_date_past", "close date past"),
    ("flag_stage_jump", "stage jump"),
)


def backtest_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.sql(BACKTEST_SQL).df()


def calibration_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.sql(
        """
        select method, n_deals, mean_predicted, realised_rate, mean_gap, brier
        from fct_calibration
        order by brier
        """
    ).df()


def latest_quarter_index(con: duckdb.DuckDBPyConnection) -> int:
    return int(con.execute("select max(quarter_index) from dim_snapshot_dates").fetchone()[0])


def weights_frame(con: duckdb.DuckDBPyConnection, quarter_index: int | None = None) -> pd.DataFrame:
    """Learned weights for one forecast quarter (default: the latest) in long form."""
    quarter = latest_quarter_index(con) if quarter_index is None else quarter_index
    return con.execute(
        """
        select forecast_quarter, stage, stage_order, push_bucket, weight, weight_lo, weight_hi,
            weight_source, cell_n_rows, stage_n_rows, stage_rate, crm_default_probability
        from fct_stage_weights
        where forecast_quarter_index = ?
        order by stage_order, push_bucket
        """,
        [quarter],
    ).df()


def hygiene_frame(con: duckdb.DuckDBPyConnection, top: int = 20) -> pd.DataFrame:
    """Flagged opportunities: live pipeline first, then most flags, then largest pipeline value.

    Deals already won or lost at the snapshot carry no pipeline value, so they rank after every
    open deal rather than ahead of them on idle days or flag count alone.
    """
    return con.execute(
        """
        select opportunity_id, segment, stage, open_at_latest_snapshot, pipeline_value,
            push_count, days_since_change, flag_count,
            flag_pushes_3plus, flag_stage_regression, flag_stale_60d,
            flag_close_date_past, flag_stage_jump
        from fct_hygiene_flags
        where flag_count > 0
        order by open_at_latest_snapshot desc, flag_count desc, pipeline_value desc, opportunity_id
        limit ?
        """,
        [top],
    ).df()


def flag_names(row: pd.Series) -> str:
    return ", ".join(label for column, label in HYGIENE_FLAGS if bool(row[column]))
