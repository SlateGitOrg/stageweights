from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from stageweights import fiscal

N_QUARTERS = 12


def quarter_ts(index: int) -> datetime:
    return fiscal.utc_midnight(fiscal.quarter_start(index))


def test_snapshot_dates_are_the_twelve_complete_fiscal_quarters(full):
    dates = full.rows(
        "select quarter_index, fiscal_quarter, snapshot_date, quarter_end_date "
        "from dim_snapshot_dates order by 1"
    )
    assert [d.quarter_index for d in dates] == list(range(N_QUARTERS))
    assert dates[0].fiscal_quarter == "FY2023 Q1" and dates[-1].fiscal_quarter == "FY2025 Q4"
    assert dates[0].snapshot_date == date(2023, 1, 2)
    assert all((d.quarter_end_date - d.snapshot_date).days == 91 for d in dates)


def test_every_snapshot_row_matches_the_python_replay_oracle_exactly(full):
    rows = full.rows(
        """
        select quarter_index, opportunity_id, stage, amount, close_date, rep_probability, push_count
        from fct_pipeline_snapshots
        """
    )
    by_quarter: dict[int, list] = {}
    for row in rows:
        by_quarter.setdefault(row.quarter_index, []).append(row)
    total_checked = 0
    for quarter in range(N_QUARTERS):
        expected = full.oracle.open_pipeline_at(quarter_ts(quarter))
        got = by_quarter.get(quarter, [])
        assert {r.opportunity_id for r in got} == set(expected), f"quarter {quarter}"
        assert len(got) == len(expected)
        for row in got:
            state = expected[row.opportunity_id]
            assert row.stage == state.stage
            assert Decimal(row.amount) == state.amount
            assert row.close_date == state.close_date
            assert Decimal(row.rep_probability) == state.rep_probability
            assert row.push_count == state.push_count
            total_checked += 1
    assert total_checked == len(rows) > 2_500


def test_the_first_quarter_has_no_pipeline_because_no_deal_exists_yet(full):
    assert full.oracle.open_pipeline_at(quarter_ts(0)) == {}
    assert (
        full.frame("select count(*) n from fct_pipeline_snapshots where quarter_index = 0")["n"][0]
        == 0
    )


def test_deals_created_during_a_quarter_are_reported_separately(full):
    creations = full.frame("select * from fct_quarter_creations order by quarter_index")
    snapshot = full.frame("select quarter_index, opportunity_id from fct_pipeline_snapshots")
    for row in creations.itertuples():
        start, end = quarter_ts(row.quarter_index), quarter_ts(row.quarter_index + 1)
        created = [
            oid
            for oid in full.oracle.opportunity_ids
            if start < full.oracle._tracks[oid].created_at <= end
        ]
        assert row.n_created == len(created)
        in_snapshot = set(
            snapshot[snapshot["quarter_index"] == row.quarter_index]["opportunity_id"]
        )
        assert not in_snapshot & set(created)
        assert row.n_won_in_quarter + row.n_lost_in_quarter + row.n_open_at_quarter_end == len(
            created
        )
    assert creations["n_created"].sum() > 2_300


def test_outcome_is_read_from_the_quarter_end_state(full):
    rows = full.rows(
        """
        select quarter_index, opportunity_id, close_date, outcome, won_in_quarter, won_amount
        from fct_pipeline_snapshots
        """
    )
    end_cache: dict[int, datetime] = {}
    for row in rows:
        end = end_cache.setdefault(row.quarter_index, quarter_ts(row.quarter_index + 1))
        state = full.oracle.state_at(row.opportunity_id, end)
        if state.stage == "Closed Won":
            assert (row.outcome, bool(row.won_in_quarter)) == ("won", True)
            assert Decimal(row.won_amount) == state.amount
        elif state.stage == "Closed Lost":
            assert (row.outcome, bool(row.won_in_quarter), Decimal(row.won_amount)) == (
                "lost",
                False,
                Decimal(0),
            )
        else:
            expected = (
                "slipped" if row.close_date < fiscal.quarter_end(row.quarter_index) else "open"
            )
            assert row.outcome == expected and not row.won_in_quarter
    assert {r.outcome for r in rows} == {"won", "lost", "slipped", "open"}


def test_age_and_time_in_stage_are_measured_at_the_snapshot_date(full):
    rows = full.rows(
        "select quarter_index, opportunity_id, snapshot_date, deal_age_days, days_in_stage "
        "from fct_pipeline_snapshots where quarter_index in (5, 11)"
    )
    assert len(rows) > 400
    for row in rows:
        created = full.oracle._tracks[row.opportunity_id].created_at
        assert row.deal_age_days == (row.snapshot_date - created.date()).days
        assert 0 <= row.days_in_stage <= row.deal_age_days


def test_no_snapshot_row_uses_a_change_dated_after_its_snapshot(full):
    late = full.frame(
        "select count(*) n from fct_pipeline_snapshots where last_change_at > snapshot_ts"
    )
    assert late["n"][0] == 0
    assert (
        full.frame("select max(last_change_at <= snapshot_ts)::int ok from fct_pipeline_snapshots")[
            "ok"
        ][0]
        == 1
    )


def test_utc_instants_do_not_depend_on_the_session_time_zone(full):
    full.con.execute("set TimeZone = 'Pacific/Kiritimati'")
    try:
        sample = full.frame(
            "select min(changed_at) t from stg_field_history where opportunity_id = 'OPP-00002'"
        )["t"][0]
    finally:
        full.con.execute("set TimeZone = 'UTC'")
    first = min(full.oracle._tracks["OPP-00002"].times or [datetime.max.replace(tzinfo=UTC)])
    assert sample == first.replace(tzinfo=None)
