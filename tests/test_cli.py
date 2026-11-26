from __future__ import annotations

import json

from typer.testing import CliRunner

from stageweights.cli import app

runner = CliRunner(env={"COLUMNS": "160"})


def test_generate_writes_the_three_sources_and_metadata(tmp_path):
    result = runner.invoke(
        app, ["generate", "--seed", "3", "--out", str(tmp_path / "d"), "--opportunities", "80"]
    )
    assert result.exit_code == 0, result.output
    names = {p.name for p in (tmp_path / "d").iterdir()}
    assert {"opportunities.csv", "opportunity_field_history.csv", "users.csv"} <= names
    meta = json.loads((tmp_path / "d" / "meta.json").read_text())
    assert meta["fiscal_start"] == "2023-01-02" and meta["quarter_days"] == 91
    assert "Wrote 80 opportunities" in result.output


def test_backtest_prints_the_eight_quarters_and_the_weight_table(full):
    args = ["--data", str(full.data_dir), "--work", str(full.work_root)]
    result = runner.invoke(app, ["backtest", *args])
    assert result.exit_code == 0, result.output
    for label in ("FY2024 Q1", "FY2025 Q4", "MAPE", "Mean bias", "Negotiation", "3+ pushes"):
        assert label in result.output
    skipped = runner.invoke(app, ["backtest", *args, "--no-weights"])
    assert "3+ pushes" not in skipped.output


def test_calibration_lists_all_three_methods(full):
    result = runner.invoke(
        app, ["calibration", "--data", str(full.data_dir), "--work", str(full.work_root)]
    )
    assert result.exit_code == 0, result.output
    for method in ("learned", "crm_default", "rep_entered"):
        assert method in result.output


def test_hygiene_honours_top(full):
    args = ["--data", str(full.data_dir), "--work", str(full.work_root)]
    result = runner.invoke(app, ["hygiene", *args, "--top", "7"])
    assert result.exit_code == 0, result.output
    assert result.output.count("OPP-") == 7
    assert "as of 2025-09-29" in result.output


def test_reports_ask_for_a_build_first(tmp_path):
    result = runner.invoke(
        app, ["backtest", "--data", str(tmp_path / "nope"), "--work", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "stageweights build" in result.output
