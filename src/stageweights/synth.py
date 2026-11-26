"""Seeded synthetic CRM export: opportunities, field history and users.

Every deal is simulated on its own random stream (``SeedSequence([seed, index])``) and all draws
for a deal are made before any pattern switch is consulted, so toggling a switch can only change
what a deal *does*, never the numbers it was dealt. Deals advance on global weekly ticks
(Monday, 09:00-17:00 local time), so a deal open at a quarter start experiences exactly 13 ticks
before the quarter ends and the generator's absorbing Markov chain gives the true win-in-quarter
probability as the 13th matrix power (see :func:`true_win_in_quarter`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from stageweights.fiscal import (
    FISCAL_START,
    N_QUARTERS,
    QUARTER_DAYS,
    TICKS_PER_QUARTER,
    quarter_start,
    utc_midnight,
)

OPEN_STAGES = ("Prospecting", "Discovery", "Solution", "Proposal", "Negotiation", "Contract Sent")
WON = "Closed Won"
LOST = "Closed Lost"
STAGES = (*OPEN_STAGES, WON, LOST)
CRM_DEFAULT_PERCENT = dict(zip(OPEN_STAGES, (10, 20, 40, 60, 80, 90), strict=True))

SEGMENTS = ("smb", "mid_market", "enterprise")
SEGMENT_SHARE = (0.45, 0.35, 0.20)
REPS_PER_SEGMENT = 6
OPTIMISTIC_REPS = ("rep_03", "rep_10")
OPTIMISTIC_POINTS = 25
JUMPER_REP = "rep_09"
UTC_OFFSET_HOURS = {"east": -5, "central": -6, "west": -8}

N_TICKS = N_QUARTERS * TICKS_PER_QUARTER
DROP_TICK = 10 * TICKS_PER_QUARTER  # first tick of FY2025 Q3
SLIPPER_SHARE = 0.25
SLIPPER_SPEED = 0.4
STALL_PUSHES = 3  # a deal whose close date has been pushed this often has lost momentum
STALL_FACTOR = 0.45
DEFAULT_OPPORTUNITIES = 2400
HOMOGENEOUS_OPPORTUNITIES = 6000
PUSH_DAYS = 30
CLOSE_DATE_OFFSET_DAYS = {"smb": (75, 150), "mid_market": (105, 210), "enterprise": (150, 300)}
AMOUNT_MEDIAN = {"smb": 18_000, "mid_market": 48_000, "enterprise": 140_000}
AMOUNT_SIGMA = {"smb": 0.5, "mid_market": 0.5, "enterprise": 0.6}
AMOUNT_CHANGE_RATE = 0.03
REP_UPDATE_RATE = 0.6  # share of stage changes after which the rep re-enters a probability
START_STAGE_CUTS = (0.5, 0.8)  # Prospecting below the first cut, Discovery below the second
MAX_EVENTS_PER_DEAL = 512

# Weekly transitions out of each open stage as (lose, regress, advance); from Contract Sent
# "advance" means Closed Won. Segment multipliers scale advance and lose.
_BASE_RATES = (
    (0.03, 0.00, 0.30),
    (0.04, 0.02, 0.24),
    (0.04, 0.02, 0.20),
    (0.045, 0.03, 0.165),
    (0.04, 0.02, 0.15),
    (0.03, 0.02, 0.30),
)
_SEGMENT_SPEED = {"smb": (1.15, 1.15), "mid_market": (1.0, 1.0), "enterprise": (0.92, 0.85)}
_NEGOTIATION = OPEN_STAGES.index("Negotiation")
NEGOTIATION_DROP_FACTOR = 0.48  # enterprise Negotiation advance after the planted change


def _rates(
    stage_idx: int,
    segment: str,
    *,
    slipper: bool = False,
    dropped: bool = False,
    stalled: bool = False,
) -> tuple[float, float, float]:
    lose, regress, advance = _BASE_RATES[stage_idx]
    advance_scale, lose_scale = _SEGMENT_SPEED[segment]
    advance *= advance_scale
    lose *= lose_scale
    if slipper:
        advance *= SLIPPER_SPEED
    if stalled:
        advance *= STALL_FACTOR
    if dropped and segment == "enterprise" and stage_idx == _NEGOTIATION:
        advance *= NEGOTIATION_DROP_FACTOR
    return lose, regress, advance


def transition_matrix(segment: str, *, dropped: bool = False) -> np.ndarray:
    """8x8 weekly transition matrix over the open stages plus Closed Won and Closed Lost."""
    matrix = np.zeros((8, 8))
    for idx in range(len(OPEN_STAGES)):
        lose, regress, advance = _rates(idx, segment, dropped=dropped)
        matrix[idx, 7] = lose
        if idx > 0:
            matrix[idx, idx - 1] = regress
        matrix[idx, idx + 1] = advance
        matrix[idx, idx] = 1.0 - lose - regress - advance
    matrix[6, 6] = matrix[7, 7] = 1.0
    return matrix


def true_win_in_quarter(stage: str, segment: str, *, dropped: bool = False) -> float:
    """Exact probability that a deal in ``stage`` at a quarter start is won within 13 weekly steps.

    This is the (stage, Closed Won) entry of the 13th power of the segment's transition matrix. It
    describes the base chain; the slipper class and rep effects are layered on top of it in full
    mode, so pooled rates there differ from this number by design. ``dropped`` applies the
    enterprise Negotiation change for the whole quarter.
    """
    power = np.linalg.matrix_power(transition_matrix(segment, dropped=dropped), TICKS_PER_QUARTER)
    return float(power[OPEN_STAGES.index(stage), 6])


@dataclass(frozen=True)
class SynthConfig:
    """Generator settings. ``homogeneous`` is the exact-recovery mode."""

    seed: int = 7
    n_opportunities: int | None = None
    homogeneous: bool = False
    enterprise_negotiation_drop: bool = True
    duplicate_rate: float = 0.02

    @property
    def size(self) -> int:
        if self.n_opportunities is not None:
            return self.n_opportunities
        return HOMOGENEOUS_OPPORTUNITIES if self.homogeneous else DEFAULT_OPPORTUNITIES


@dataclass
class Dataset:
    """The three raw CRM tables plus the ground truth the generator planted."""

    users: pd.DataFrame
    opportunities: pd.DataFrame
    history: pd.DataFrame
    truth: dict
    meta: dict
    config: SynthConfig = field(default_factory=SynthConfig)


def _users() -> pd.DataFrame:
    rows = []
    regions = list(UTC_OFFSET_HOURS)
    for n in range(1, len(SEGMENTS) * REPS_PER_SEGMENT + 1):
        rows.append(
            {
                "user_id": f"rep_{n:02d}",
                "segment": SEGMENTS[(n - 1) // REPS_PER_SEGMENT],
                "region": regions[(n - 1) % len(regions)],
            }
        )
    return pd.DataFrame(rows)


def _rep_percent(stage: str, optimistic: bool, z: float) -> int:
    """Probability a rep types in: the stage default, plus bias, plus noise, in steps of 5."""
    value = CRM_DEFAULT_PERCENT[stage] + (OPTIMISTIC_POINTS if optimistic else 0) + 5.0 * z
    return int(min(100, max(5, round(value / 5.0) * 5)))


def _recorded_stage(latent: int, jumper: bool) -> str:
    """Stage the CRM shows. The jumper rep parks deals in Discovery until they are nearly closed."""
    if jumper and 1 <= latent <= 4:
        return "Discovery"
    return STAGES[latent]


@dataclass
class _Event:
    at: datetime  # UTC
    local: datetime
    field: str
    old: str
    new: str


@dataclass
class _Deal:
    opportunity_id: str
    owner_id: str
    segment: str
    created_utc: datetime
    created_local: datetime
    initial: dict
    final: dict
    events: list[_Event]
    duplicates: list[bool]
    closed_utc: datetime | None
    jump_at: list[datetime]
    slipper: bool


def _simulate_deal(index: int, cfg: SynthConfig, users: pd.DataFrame) -> _Deal:
    rng = np.random.default_rng(np.random.SeedSequence([cfg.seed, index]))
    # Every draw is made up front, in a fixed order, whatever the switches say.
    seg_u, owner_u, day_u, time_u = rng.random(4)
    start_u, close_u, slip_u, amount_z, rp_z0 = *rng.random(3), *rng.standard_normal(2)
    tick_u = rng.random((N_TICKS, 4))
    tick_z = rng.standard_normal((N_TICKS, 2))
    dup_u = rng.random(MAX_EVENTS_PER_DEAL)

    if cfg.homogeneous:
        segment = "mid_market"
    else:
        segment = SEGMENTS[int(np.searchsorted(np.cumsum(SEGMENT_SHARE), seg_u, side="right"))]
    reps = users[users["segment"] == segment].reset_index(drop=True)
    owner = reps.iloc[int(owner_u * len(reps))]
    owner_id = str(owner["user_id"])
    offset = timezone(timedelta(hours=UTC_OFFSET_HOURS[str(owner["region"])]))
    optimistic = (not cfg.homogeneous) and owner_id in OPTIMISTIC_REPS
    jumper = (not cfg.homogeneous) and owner_id == JUMPER_REP
    slipper = (not cfg.homogeneous) and slip_u < SLIPPER_SHARE

    created_day = int(day_u * (N_TICKS * 7 - 7))
    created_local = datetime.combine(
        FISCAL_START + timedelta(days=created_day), datetime.min.time(), tzinfo=offset
    ) + timedelta(seconds=int(8 * 3600 + time_u * 10 * 3600))
    created_utc = created_local.astimezone(UTC)

    latent = 0 if start_u < START_STAGE_CUTS[0] else (1 if start_u < START_STAGE_CUTS[1] else 2)
    stage = _recorded_stage(latent, jumper)
    amount = max(
        1_000,
        int(round(AMOUNT_MEDIAN[segment] * np.exp(AMOUNT_SIGMA[segment] * amount_z) / 100) * 100),
    )
    lo, hi = CLOSE_DATE_OFFSET_DAYS[segment]
    close_date = created_local.date() + timedelta(days=int(lo + close_u * (hi - lo)))
    rep_percent = _rep_percent(stage, optimistic, rp_z0)
    initial = {
        "stage": stage,
        "amount": amount,
        "close_date": close_date,
        "rep_probability": rep_percent,
    }

    events: list[_Event] = []
    jump_at: list[datetime] = []
    closed_utc: datetime | None = None
    pushes = 0

    def emit(at: datetime, local: datetime, name: str, old, new) -> None:
        events.append(_Event(at, local, name, str(old), str(new)))

    first_tick = created_day // 7
    for tick in range(first_tick, N_TICKS):
        local = datetime.combine(
            FISCAL_START + timedelta(days=7 * tick), datetime.min.time(), tzinfo=offset
        ) + timedelta(seconds=int(9 * 3600 + tick_u[tick, 2] * 8 * 3600))
        at = local.astimezone(UTC)
        if at <= created_utc:
            continue

        if tick_u[tick, 1] < AMOUNT_CHANGE_RATE:
            new_amount = max(
                1_000, int(round(amount * np.exp(0.15 * tick_z[tick, 0] - 0.01) / 100) * 100)
            )
            if new_amount != amount:
                emit(at, local, "amount", amount, new_amount)
                amount = new_amount

        lose, regress, advance = _rates(
            latent,
            segment,
            slipper=slipper,
            dropped=cfg.enterprise_negotiation_drop and tick >= DROP_TICK,
            stalled=(not cfg.homogeneous) and pushes >= STALL_PUSHES,
        )
        u = tick_u[tick, 0]
        if u < lose:
            new_latent = 7
        elif u < lose + regress:
            new_latent = latent - 1
        elif u < lose + regress + advance:
            new_latent = latent + 1
        else:
            new_latent = latent
        latent = new_latent

        new_stage = STAGES[latent] if latent >= 6 else _recorded_stage(latent, jumper)
        if new_stage != stage:
            emit(at, local, "stage", stage, new_stage)
            if stage == "Discovery" and new_stage == "Contract Sent":
                jump_at.append(at)
            if latent == 6:
                new_percent = 100
            elif latent == 7:
                new_percent = 0
            elif tick_u[tick, 3] < REP_UPDATE_RATE:
                new_percent = _rep_percent(new_stage, optimistic, float(tick_z[tick, 1]))
            else:
                new_percent = rep_percent  # the rep did not revisit the probability
            if new_percent != rep_percent:
                emit(at, local, "rep_probability", rep_percent, new_percent)
                rep_percent = new_percent
            stage = new_stage

        if latent >= 6:
            if local.date() != close_date:
                emit(at, local, "close_date", close_date, local.date())
                close_date = local.date()
            closed_utc = at
            break
        if close_date < local.date():
            pushed = close_date + timedelta(days=PUSH_DAYS)
            emit(at, local, "close_date", close_date, pushed)
            close_date = pushed
            pushes += 1

    final = {
        "stage": stage,
        "amount": amount,
        "close_date": close_date,
        "rep_probability": rep_percent,
    }
    return _Deal(
        opportunity_id=f"OPP-{index + 1:05d}",
        owner_id=owner_id,
        segment=segment,
        created_utc=created_utc,
        created_local=created_local,
        initial=initial,
        final=final,
        events=events,
        duplicates=[bool(x < cfg.duplicate_rate) for x in dup_u[: len(events)]],
        closed_utc=closed_utc,
        jump_at=jump_at,
        slipper=slipper,
    )


def generate(config: SynthConfig | None = None) -> Dataset:
    """Simulate the CRM and return the raw tables with the planted ground truth."""
    cfg = config or SynthConfig()
    users = _users()
    deals = [_simulate_deal(i, cfg, users) for i in range(cfg.size)]

    opp_rows, hist_rows = [], []
    n_duplicates = 0
    for deal in deals:
        opp_rows.append(
            {
                "opportunity_id": deal.opportunity_id,
                "owner_id": deal.owner_id,
                "created_at": deal.created_local.isoformat(),
                "created_stage": deal.initial["stage"],
                "created_amount": deal.initial["amount"],
                "created_close_date": deal.initial["close_date"].isoformat(),
                "created_rep_probability": deal.initial["rep_probability"],
                "stage": deal.final["stage"],
                "amount": deal.final["amount"],
                "close_date": deal.final["close_date"].isoformat(),
                "rep_probability": deal.final["rep_probability"],
            }
        )
        for ev, dup in zip(deal.events, deal.duplicates, strict=True):
            row = {
                "opportunity_id": deal.opportunity_id,
                "field": ev.field,
                "old_value": ev.old,
                "new_value": ev.new,
                "changed_at": ev.local.isoformat(),
            }
            hist_rows.append((ev.at, row))
            if dup:
                hist_rows.append((ev.at, dict(row)))
                n_duplicates += 1
    hist_rows.sort(key=lambda pair: (pair[0], pair[1]["opportunity_id"], pair[1]["field"]))
    history = pd.DataFrame([row for _, row in hist_rows])
    opportunities = pd.DataFrame(opp_rows)

    latest_snapshot = quarter_start(N_QUARTERS - 1)
    snap_ts = utc_midnight(latest_snapshot)
    open_3plus = 0
    for deal in deals:
        if deal.created_utc > snap_ts or (
            deal.closed_utc is not None and deal.closed_utc <= snap_ts
        ):
            continue
        pushes = sum(
            1
            for ev in deal.events
            if ev.field == "close_date" and ev.at <= snap_ts and ev.new > ev.old
        )
        open_3plus += pushes >= 3

    truth = {
        "seed": cfg.seed,
        "homogeneous": cfg.homogeneous,
        "enterprise_negotiation_drop": cfg.enterprise_negotiation_drop,
        "n_opportunities": len(deals),
        "n_history_rows": len(history),
        "n_duplicate_rows": n_duplicates,
        "latest_snapshot_date": latest_snapshot.isoformat(),
        "open_3plus_pushes_at_latest_snapshot": int(open_3plus),
        "planted_jumps": [
            {"opportunity_id": d.opportunity_id, "changed_at": at.isoformat()}
            for d in deals
            for at in d.jump_at
        ],
        "optimistic_reps": [] if cfg.homogeneous else list(OPTIMISTIC_REPS),
        "jumper_rep": None if cfg.homogeneous else JUMPER_REP,
        "slipper_opportunities": [d.opportunity_id for d in deals if d.slipper],
    }
    data_through = FISCAL_START + timedelta(days=QUARTER_DAYS * N_QUARTERS)
    meta = {
        "fiscal_start": FISCAL_START.isoformat(),
        "quarter_days": QUARTER_DAYS,
        "data_through": data_through.isoformat(),
    }
    return Dataset(users, opportunities, history, truth, meta, cfg)


def write_dataset(dataset: Dataset, out_dir: Path | str) -> Path:
    """Write the CSVs the dbt sources read, plus ``meta.json`` and ``truth.json``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dataset.users.to_csv(out / "users.csv", index=False)
    dataset.opportunities.to_csv(out / "opportunities.csv", index=False)
    dataset.history.to_csv(out / "opportunity_field_history.csv", index=False)
    (out / "meta.json").write_text(json.dumps(dataset.meta, indent=2) + "\n")
    (out / "truth.json").write_text(json.dumps(dataset.truth, indent=2) + "\n")
    return out


def drop_exact_duplicates(history: pd.DataFrame) -> pd.DataFrame:
    """The history with exact duplicate rows removed, in the original order."""
    return history.drop_duplicates().reset_index(drop=True)


def data_through_date(meta: dict) -> date:
    return date.fromisoformat(meta["data_through"])
