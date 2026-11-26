from __future__ import annotations

from datetime import date, datetime

from stageweights import fiscal
from stageweights.oracle import parse_utc
from stageweights.synth import OPEN_STAGES

AS_OF = fiscal.utc_midnight(fiscal.quarter_start(11))
ORDER = {stage: i for i, stage in enumerate(OPEN_STAGES)}


def hygiene(full):
    return {r.opportunity_id: r for r in full.rows("select * from fct_hygiene_flags")}


def test_one_row_per_opportunity_that_existed_at_the_latest_snapshot(full):
    rows = hygiene(full)
    existing = {
        oid for oid in full.oracle.opportunity_ids if full.oracle._tracks[oid].created_at <= AS_OF
    }
    assert set(rows) == existing
    assert {r.as_of_date for r in rows.values()} == {date(2025, 9, 29)}


def test_every_planted_discovery_to_contract_sent_jump_is_flagged(full):
    planted = {
        j["opportunity_id"]
        for j in full.dataset.truth["planted_jumps"]
        if parse_utc(j["changed_at"]) <= AS_OF
    }
    flagged = {oid for oid, r in hygiene(full).items() if r.flag_stage_jump}
    assert len(planted) >= 25
    assert planted <= flagged
    assert flagged == planted  # nothing else in this data moves forward two stages at once


def test_open_deals_with_three_pushes_match_the_generators_count(full):
    rows = hygiene(full)
    open_3plus = [r for r in rows.values() if r.open_at_latest_snapshot and r.push_count >= 3]
    assert len(open_3plus) == full.dataset.truth["open_3plus_pushes_at_latest_snapshot"] > 10
    assert all(r.flag_pushes_3plus for r in open_3plus)
    # And the same deals are the ones in the snapshot with push_count >= 3.
    snap = full.frame(
        "select count(*) n from fct_pipeline_snapshots where quarter_index = 11 and push_count >= 3"
    )["n"][0]
    assert snap == len(open_3plus)


def test_regression_stale_and_past_close_date_flags_follow_the_raw_history(full):
    history = full.dataset.history.drop_duplicates()
    stage_moves: dict[str, list[tuple[datetime, str, str]]] = {}
    last_change: dict[str, datetime] = {}
    for row in history.itertuples():
        at = parse_utc(row.changed_at)
        if at > AS_OF:
            continue
        last_change[row.opportunity_id] = max(last_change.get(row.opportunity_id, at), at)
        if row.field == "stage":
            stage_moves.setdefault(row.opportunity_id, []).append(
                (at, row.old_value, row.new_value)
            )

    for oid, got in hygiene(full).items():
        moves = stage_moves.get(oid, [])
        regress = any(
            old in ORDER and new in ORDER and ORDER[new] < ORDER[old] for _, old, new in moves
        )
        state = full.oracle.state_at(oid, AS_OF)
        last = max(
            last_change.get(oid, full.oracle._tracks[oid].created_at),
            full.oracle._tracks[oid].created_at,
        )
        idle_days = (AS_OF.date() - last.date()).days
        assert got.flag_stage_regression == regress, oid
        assert got.open_at_latest_snapshot == state.is_open, oid
        assert got.flag_stale_60d == (state.is_open and idle_days >= 60), oid
        assert got.flag_close_date_past == (state.is_open and state.close_date < AS_OF.date()), oid
        assert got.push_count == state.push_count and got.flag_pushes_3plus == (
            state.push_count >= 3
        )
    assert sum(r.flag_stale_60d for r in hygiene(full).values()) > 0
    assert sum(r.flag_close_date_past for r in hygiene(full).values()) > 0


def test_pipeline_value_is_the_open_amount_and_flag_count_adds_the_flags(full):
    for oid, row in hygiene(full).items():
        state = full.oracle.state_at(oid, AS_OF)
        assert row.pipeline_value == (state.amount if state.is_open else 0)
        assert row.flag_count == sum(
            [
                row.flag_pushes_3plus,
                row.flag_stage_regression,
                row.flag_stale_60d,
                row.flag_close_date_past,
                row.flag_stage_jump,
            ]
        )


def test_top_flags_rank_live_deals_before_closed_ones(full):
    from stageweights import reports

    top = reports.hygiene_frame(full.con, top=1000)
    open_flags = list(top["open_at_latest_snapshot"])
    assert any(open_flags) and not all(open_flags)
    # Every open deal precedes every closed deal, and the first rows are live pipeline.
    assert open_flags == sorted(open_flags, reverse=True)
    head = reports.hygiene_frame(full.con, top=10)
    assert head["open_at_latest_snapshot"].all() and (head["pipeline_value"] > 0).all()
    open_rows = top[top["open_at_latest_snapshot"]]
    assert list(open_rows["flag_count"]) == sorted(open_rows["flag_count"], reverse=True)
