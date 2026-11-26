"""Run the dbt project in-process, one target directory and DuckDB file per dataset."""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from dbt.adapters.duckdb.connections import DuckDBConnectionManager
from dbt.adapters.factory import cleanup_connections, reset_adapters
from dbt.cli.main import dbtRunner
from dbt_common.events.event_manager_client import cleanup_event_logger, get_event_manager

DBT_DIR_ENV = "STAGEWEIGHTS_DBT_DIR"
DB_PATH_ENV = "STAGEWEIGHTS_DUCKDB_PATH"
_OK_STATUSES = {"success", "pass"}


class BuildError(RuntimeError):
    """``dbt build`` failed; the message lists the nodes that did not pass."""


@dataclass(frozen=True)
class BuildResult:
    dataset: str
    workdir: Path
    target_dir: Path
    db_path: Path
    n_models: int
    n_tests: int
    failures: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.failures


def dbt_project_dir() -> Path:
    """The dbt project: ``dbt/`` next to ``src/`` in a source checkout, or $STAGEWEIGHTS_DBT_DIR."""
    override = os.environ.get(DBT_DIR_ENV)
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2] / "dbt"


def dataset_paths(data_dir: Path | str, work_root: Path | str = "work") -> tuple[Path, Path, Path]:
    """Work directory, dbt target directory and DuckDB file for one dataset directory."""
    workdir = Path(work_root).resolve() / Path(data_dir).resolve().name
    target_dir = workdir / "target"
    return workdir, target_dir, target_dir / "stageweights.duckdb"


def _release_duckdb() -> None:
    """dbt-duckdb keeps its connection and dbt its log file open after an in-process run."""
    for logger in get_event_manager().loggers:
        python_logger = getattr(logger, "_python_logger", None)
        for handler in list(getattr(python_logger, "handlers", [])):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                python_logger.removeHandler(handler)
    cleanup_event_logger()
    cleanup_connections()
    DuckDBConnectionManager.close_all_connections()
    reset_adapters()


def build(
    data_dir: Path | str,
    work_root: Path | str = "work",
    *,
    select: list[str] | None = None,
) -> BuildResult:
    """Run ``dbt build`` (models and tests) over the CSVs in ``data_dir``.

    The DuckDB file and target directory are recreated for each call, so a result never mixes two
    builds. Raises :class:`BuildError` if any model or test fails.
    """
    data = Path(data_dir).resolve()
    meta = json.loads((data / "meta.json").read_text())
    workdir, target_dir, db_path = dataset_paths(data, work_root)
    if workdir.exists():
        shutil.rmtree(workdir)
    target_dir.mkdir(parents=True)

    project = dbt_project_dir()
    variables = {
        "raw_dir": str(data),
        "fiscal_start": meta["fiscal_start"],
        "quarter_days": meta["quarter_days"],
        "data_through": meta["data_through"],
    }
    args = [
        "--quiet",
        "--no-use-colors",
        "--log-path",
        str(workdir / "logs"),
        "build",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--target-path",
        str(target_dir),
        "--vars",
        json.dumps(variables),
    ]
    if select:
        args += ["--select", *select]

    previous = os.environ.get(DB_PATH_ENV)
    os.environ[DB_PATH_ENV] = str(db_path)
    try:
        outcome = dbtRunner().invoke(args)
    finally:
        _release_duckdb()
        if previous is None:
            os.environ.pop(DB_PATH_ENV, None)
        else:
            os.environ[DB_PATH_ENV] = previous

    if outcome.exception is not None:
        raise BuildError(f"dbt could not run: {outcome.exception}")
    nodes = list(getattr(outcome.result, "results", []))
    failures = [
        f"{r.node.name}: {r.status}" for r in nodes if str(r.status).lower() not in _OK_STATUSES
    ]
    n_tests = sum(1 for r in nodes if r.node.resource_type == "test")
    n_models = sum(1 for r in nodes if r.node.resource_type == "model")
    result = BuildResult(data.name, workdir, target_dir, db_path, n_models, n_tests, failures)
    if failures:
        raise BuildError("dbt build failed:\n  " + "\n  ".join(failures))
    return result


def connect(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open a built dataset read-only."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run `stageweights build` first")
    return duckdb.connect(str(path), read_only=True)
