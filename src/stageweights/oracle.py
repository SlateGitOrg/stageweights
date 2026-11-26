"""Independent replay of the CRM change log in plain Python.

This module never touches DuckDB or dbt. It reads the raw CSVs, drops exact duplicate rows, and
answers "what did this opportunity look like at this instant" by applying changes in order. The test
suite compares its answers with the SQL point-in-time reconstruction.
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

OPEN_STAGES = frozenset(
    {"Prospecting", "Discovery", "Solution", "Proposal", "Negotiation", "Contract Sent"}
)


@dataclass(frozen=True)
class OppState:
    """An opportunity's tracked fields at one instant."""

    stage: str
    amount: Decimal
    close_date: date
    rep_probability: Decimal
    push_count: int

    @property
    def is_open(self) -> bool:
        return self.stage in OPEN_STAGES


def parse_utc(text: str) -> datetime:
    """An ISO 8601 timestamp with a UTC offset, as an aware UTC datetime."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without a UTC offset: {text!r}")
    return parsed.astimezone(UTC)


def _percent(text: str) -> Decimal:
    return Decimal(int(text)) / Decimal(100)


def _flag_pushes(
    initial: dict[str, str], times: list[datetime], changes: list[tuple[str, str]]
) -> list[bool]:
    """Mark each change that moves a close date later while the deal is still open.

    The stage that counts is the one in force after every change stamped at the same instant, so a
    close date reset on the day a deal is won is not a push.
    """
    running = dict(initial)
    stage_after: list[str] = []
    for field, new_value in changes:
        running[field] = new_value
        stage_after.append(running["stage"])
    for i in range(len(changes) - 2, -1, -1):
        if times[i] == times[i + 1]:
            stage_after[i] = stage_after[i + 1]

    close_date = date.fromisoformat(initial["close_date"])
    flags = []
    for (field, new_value), stage in zip(changes, stage_after, strict=True):
        is_push = False
        if field == "close_date":
            moved = date.fromisoformat(new_value)
            is_push = moved > close_date and stage in OPEN_STAGES
            close_date = moved
        flags.append(is_push)
    return flags


@dataclass
class _Track:
    created_at: datetime
    initial: dict[str, str]
    times: list[datetime]
    changes: list[tuple[str, str]]  # (field, new_value), same order as ``times``
    pushes: list[bool]  # whether each change is a push, same order


class ReplayOracle:
    """Event-sourced view of every opportunity in an export."""

    def __init__(self, opportunities: list[dict[str, str]], history: list[dict[str, str]]):
        raw_changes: dict[str, list[tuple[datetime, str, str]]] = {}
        seen: set[tuple[str, ...]] = set()
        for row in history:
            key = (
                row["opportunity_id"],
                row["field"],
                row["old_value"],
                row["new_value"],
                row["changed_at"],
            )
            if key in seen:
                continue
            seen.add(key)
            raw_changes.setdefault(row["opportunity_id"], []).append(
                (parse_utc(row["changed_at"]), row["field"], row["new_value"])
            )

        self._tracks: dict[str, _Track] = {}
        for opp in opportunities:
            oid = opp["opportunity_id"]
            ordered = sorted(raw_changes.get(oid, []), key=lambda c: (c[0], c[2]))
            initial = {
                "stage": opp["created_stage"],
                "amount": opp["created_amount"],
                "close_date": opp["created_close_date"],
                "rep_probability": opp["created_rep_probability"],
            }
            times = [c[0] for c in ordered]
            changes = [(c[1], c[2]) for c in ordered]
            self._tracks[oid] = _Track(
                created_at=parse_utc(opp["created_at"]),
                initial=initial,
                times=times,
                changes=changes,
                pushes=_flag_pushes(initial, times, changes),
            )

    @classmethod
    def from_csv(cls, data_dir: Path | str) -> ReplayOracle:
        base = Path(data_dir)
        with open(base / "opportunities.csv", newline="") as handle:
            opportunities = list(csv.DictReader(handle))
        with open(base / "opportunity_field_history.csv", newline="") as handle:
            history = list(csv.DictReader(handle))
        return cls(opportunities, history)

    @property
    def opportunity_ids(self) -> list[str]:
        return list(self._tracks)

    def state_at(self, opportunity_id: str, at: datetime) -> OppState | None:
        """The opportunity as it stood at ``at`` (changes stamped exactly ``at`` are applied).

        Returns ``None`` if it had not been created yet.
        """
        track = self._tracks[opportunity_id]
        if track.created_at > at:
            return None
        current = dict(track.initial)
        applied = bisect_right(track.times, at)
        for field, new_value in track.changes[:applied]:
            current[field] = new_value
        pushes = sum(track.pushes[:applied])
        return OppState(
            stage=current["stage"],
            amount=Decimal(current["amount"]),
            close_date=date.fromisoformat(current["close_date"]),
            rep_probability=_percent(current["rep_probability"]),
            push_count=pushes,
        )

    def open_pipeline_at(self, at: datetime) -> dict[str, OppState]:
        """Every opportunity that existed at ``at`` and was in an open stage."""
        pipeline = {}
        for oid in self._tracks:
            state = self.state_at(oid, at)
            if state is not None and state.is_open:
                pipeline[oid] = state
        return pipeline
