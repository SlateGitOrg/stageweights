from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from stageweights.oracle import ReplayOracle

OPP = {
    "opportunity_id": "OPP-1",
    "owner_id": "rep_01",
    "created_at": "2024-01-02T10:00:00-05:00",  # 15:00 UTC
    "created_stage": "Discovery",
    "created_amount": "20000",
    "created_close_date": "2024-02-01",
    "created_rep_probability": "20",
    "stage": "Closed Won",
    "amount": "25000",
    "close_date": "2024-03-04",
    "rep_probability": "100",
}


def row(field, old, new, at):
    return {
        "opportunity_id": "OPP-1",
        "field": field,
        "old_value": old,
        "new_value": new,
        "changed_at": at,
    }


HISTORY = [
    row("stage", "Discovery", "Solution", "2024-01-08T09:00:00-05:00"),  # 14:00 UTC
    row("close_date", "2024-02-01", "2024-03-02", "2024-02-05T09:00:00-05:00"),  # a push
    row("close_date", "2024-03-02", "2024-03-09", "2024-02-26T09:00:00-05:00"),  # a second push
    row(
        "close_date", "2024-03-09", "2024-03-12", "2024-03-04T09:00:00-08:00"
    ),  # close-out, no push
    row("amount", "20000", "25000", "2024-03-04T09:00:00-08:00"),
    row("stage", "Solution", "Closed Won", "2024-03-04T09:00:00-08:00"),
    row("rep_probability", "20", "100", "2024-03-04T09:00:00-08:00"),
]


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_state_is_none_before_creation_and_initial_at_creation():
    oracle = ReplayOracle([OPP], HISTORY)
    assert oracle.state_at("OPP-1", at("2024-01-02T14:59:59")) is None
    state = oracle.state_at("OPP-1", at("2024-01-02T15:00:00"))
    assert (state.stage, state.amount, state.close_date) == (
        "Discovery",
        Decimal(20000),
        date(2024, 2, 1),
    )
    assert state.rep_probability == Decimal("0.2") and state.push_count == 0


def test_changes_apply_from_their_utc_instant_not_their_local_clock():
    oracle = ReplayOracle([OPP], HISTORY)
    # 09:00-05:00 on 8 January is 14:00 UTC.
    assert oracle.state_at("OPP-1", at("2024-01-08T13:59:59")).stage == "Discovery"
    assert oracle.state_at("OPP-1", at("2024-01-08T14:00:00")).stage == "Solution"
    # 09:00-08:00 on 4 March is 17:00 UTC.
    assert oracle.state_at("OPP-1", at("2024-03-04T16:59:59")).stage == "Solution"
    assert oracle.state_at("OPP-1", at("2024-03-04T17:00:00")).stage == "Closed Won"


def test_pushes_are_counted_and_the_final_state_matches_the_replay():
    oracle = ReplayOracle([OPP], HISTORY)
    mid = oracle.state_at("OPP-1", at("2024-02-10T00:00:00"))
    assert (mid.close_date, mid.push_count) == (date(2024, 3, 2), 1)
    end = oracle.state_at("OPP-1", at("2024-06-01T00:00:00"))
    assert end.push_count == 2  # the close-out date set on the winning day is not a push
    assert end.amount == Decimal(25000) and end.rep_probability == Decimal(1)
    assert end.close_date == date(2024, 3, 12)
    assert not end.is_open


def test_exact_duplicate_rows_are_ignored():
    doubled = HISTORY + [HISTORY[1], HISTORY[1], HISTORY[2], HISTORY[0]]
    state = ReplayOracle([OPP], doubled).state_at("OPP-1", at("2024-06-01T00:00:00"))
    assert state.push_count == 2


def test_open_pipeline_excludes_closed_and_not_yet_created_deals():
    late = dict(OPP, opportunity_id="OPP-2", created_at="2024-04-01T10:00:00-05:00")
    oracle = ReplayOracle([OPP, late], HISTORY)
    assert set(oracle.open_pipeline_at(at("2024-02-15T00:00:00"))) == {"OPP-1"}
    assert oracle.open_pipeline_at(at("2024-03-05T00:00:00")) == {}
    assert set(oracle.open_pipeline_at(at("2024-04-02T00:00:00"))) == {"OPP-2"}
