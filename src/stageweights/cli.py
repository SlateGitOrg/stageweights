"""Command line for the stageweights pipeline: generate, build, backtest, calibration, hygiene."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from stageweights import reports, runner
from stageweights.synth import SynthConfig, generate, write_dataset

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Pipeline forecast weights learned from CRM field history, replayed per quarter.",
)
console = Console()

DataOpt = Annotated[Path, typer.Option("--data", help="Directory holding the generated CSVs.")]
WorkOpt = Annotated[
    Path, typer.Option("--work", help="Directory for dbt targets and DuckDB files.")
]


def _open(data: Path, work: Path):
    _, _, db_path = runner.dataset_paths(data, work)
    try:
        return runner.connect(db_path)
    except FileNotFoundError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _pct(value: float, signed: bool = False) -> str:
    return f"{value:+.1%}" if signed else f"{value:.1%}"


@app.command("generate")
def generate_command(
    seed: Annotated[int, typer.Option(help="Random seed.")] = 7,
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("data"),
    homogeneous: Annotated[
        bool, typer.Option(help="One segment, no slippers or rep effects (exact-recovery mode).")
    ] = False,
    enterprise_drop: Annotated[
        bool, typer.Option(help="Plant the FY2025 Q3 enterprise Negotiation change.")
    ] = True,
    opportunities: Annotated[
        int | None, typer.Option(help="Override the opportunity count.")
    ] = None,
) -> None:
    """Write a seeded synthetic CRM export (opportunities, field history, users)."""
    config = SynthConfig(
        seed=seed,
        homogeneous=homogeneous,
        enterprise_negotiation_drop=enterprise_drop,
        n_opportunities=opportunities,
    )
    dataset = generate(config)
    write_dataset(dataset, out)
    truth = dataset.truth
    console.print(
        f"Wrote {truth['n_opportunities']:,} opportunities and {truth['n_history_rows']:,} "
        f"history rows ({truth['n_duplicate_rows']:,} exact duplicates) to {out}"
    )


@app.command()
def build(data: DataOpt = Path("data"), work: WorkOpt = Path("work")) -> None:
    """Run `dbt build` (models and tests) over a generated dataset."""
    try:
        result = runner.build(data, work)
    except runner.BuildError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    console.print(
        f"dbt build passed: {result.n_models} models, {result.n_tests} tests. "
        f"DuckDB file: {os.path.relpath(result.db_path)}"
    )


@app.command()
def backtest(
    data: DataOpt = Path("data"),
    work: WorkOpt = Path("work"),
    weights: Annotated[bool, typer.Option(help="Also print the learned stage weights.")] = True,
    quarter: Annotated[
        int | None, typer.Option(help="Quarter index for the weights table (default: latest).")
    ] = None,
) -> None:
    """Out-of-sample forecast backtest: CRM default vs rep-entered vs learned weights."""
    con = _open(data, work)
    frame = reports.backtest_frame(con)
    table = Table(
        title="Closed-won forecast made at each quarter start vs what those deals closed for"
    )
    for column in ("Quarter", "Actual", "CRM default", "Rep entered", "Learned"):
        table.add_column(column, justify="right" if column != "Quarter" else "left")
    for column in ("APE default", "APE rep", "APE learned"):
        table.add_column(column, justify="right")
    for row in frame.itertuples():
        table.add_row(
            row.fiscal_quarter,
            _money(row.actual_won),
            _money(row.forecast_default),
            _money(row.forecast_rep),
            _money(row.forecast_learned),
            _pct(row.ape_default),
            _pct(row.ape_rep),
            _pct(row.ape_learned),
        )
    table.add_section()
    table.add_row(
        "MAPE",
        "",
        "",
        "",
        "",
        _pct(frame.ape_default.mean()),
        _pct(frame.ape_rep.mean()),
        _pct(frame.ape_learned.mean()),
    )
    table.add_row(
        "Mean bias",
        "",
        "",
        "",
        "",
        _pct(frame.bias_default.mean(), True),
        _pct(frame.bias_rep.mean(), True),
        _pct(frame.bias_learned.mean(), True),
    )
    console.print(table)
    if weights:
        _print_weights(reports.weights_frame(con, quarter))


def _print_weights(frame) -> None:
    label = frame.forecast_quarter.iloc[0]
    table = Table(title=f"Learned stage weights for {label} (win within the quarter)")
    for column in ("Stage", "CRM default", "0 pushes", "1-2 pushes", "3+ pushes", "Stage only"):
        table.add_column(column, justify="left" if column == "Stage" else "right")
    marks = {"cell": "", "stage": "s", "crm_default": "d"}
    for stage, group in frame.groupby("stage", sort=False):
        by_bucket = {r.push_bucket: r for r in group.itertuples()}
        cells = [
            f"{by_bucket[b].weight:.3f}{marks[by_bucket[b].weight_source]} "
            f"[dim]n={by_bucket[b].cell_n_rows}[/dim]"
            for b in ("0", "1-2", "3+")
        ]
        first = group.iloc[0]
        stage_only = "-" if first.stage_rate != first.stage_rate else f"{first.stage_rate:.3f}"
        table.add_row(
            stage,
            f"{first.crm_default_probability:.2f}",
            *cells,
            f"{stage_only} n={int(first.stage_n_rows)}",
        )
    console.print(table)
    console.print("s = fell back to the stage-only rate (cell under 30 rows); d = CRM default kept")


@app.command()
def calibration(data: DataOpt = Path("data"), work: WorkOpt = Path("work")) -> None:
    """Mean predicted vs realised win rate and Brier score for each method."""
    con = _open(data, work)
    frame = reports.calibration_frame(con)
    table = Table(title="Calibration over the scored quarters (deal level)")
    for column in (
        "Method",
        "Deals",
        "Mean predicted",
        "Realised",
        "Predicted - realised",
        "Brier",
    ):
        table.add_column(column, justify="left" if column == "Method" else "right")
    for row in frame.itertuples():
        table.add_row(
            row.method,
            f"{row.n_deals:,}",
            f"{row.mean_predicted:.3f}",
            f"{row.realised_rate:.3f}",
            f"{row.mean_gap:+.3f}",
            f"{row.brier:.4f}",
        )
    console.print(table)


@app.command()
def hygiene(
    data: DataOpt = Path("data"),
    work: WorkOpt = Path("work"),
    top: Annotated[int, typer.Option(help="How many flagged opportunities to list.")] = 20,
) -> None:
    """Opportunities with the most data-hygiene flags at the latest snapshot."""
    con = _open(data, work)
    frame = reports.hygiene_frame(con, top)
    as_of = con.execute("select max(snapshot_date) from dim_snapshot_dates").fetchone()[0]
    table = Table(title=f"Top {len(frame)} hygiene flags as of {as_of}")
    for column in (
        "Opportunity",
        "Segment",
        "Stage",
        "Open",
        "Value",
        "Pushes",
        "Idle days",
        "Flags",
    ):
        table.add_column(
            column, justify="right" if column in {"Value", "Pushes", "Idle days"} else "left"
        )
    for _, row in frame.iterrows():
        table.add_row(
            row.opportunity_id,
            row.segment,
            row.stage,
            "yes" if row.open_at_latest_snapshot else "no",
            _money(float(row.pipeline_value)),
            str(int(row.push_count)),
            str(int(row.days_since_change)),
            reports.flag_names(row),
        )
    console.print(table)


if __name__ == "__main__":
    app()
