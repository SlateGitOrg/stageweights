"""Session fixtures: each dataset is generated and built with dbt once, then shared."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from stageweights import runner
from stageweights.oracle import ReplayOracle
from stageweights.synth import (
    Dataset,
    SynthConfig,
    drop_exact_duplicates,
    generate,
    write_dataset,
)

SEED = 7


@dataclass
class Built:
    """A generated dataset, its dbt build, and a read-only connection to the result."""

    name: str
    data_dir: Path
    work_root: Path
    dataset: Dataset
    result: runner.BuildResult
    con: duckdb.DuckDBPyConnection
    _oracle: ReplayOracle | None = None

    @property
    def oracle(self) -> ReplayOracle:
        if self._oracle is None:
            self._oracle = ReplayOracle.from_csv(self.data_dir)
        return self._oracle

    def frame(self, sql: str, params: list | None = None):
        return self.con.execute(sql, params or []).df()

    def rows(self, sql: str, params: list | None = None) -> list[SimpleNamespace]:
        """Rows with native Python values (date, Decimal), unlike the pandas conversion."""
        cursor = self.con.execute(sql, params or [])
        names = [column[0] for column in cursor.description]
        return [SimpleNamespace(**dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


@pytest.fixture(scope="session")
def work_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("work")


@pytest.fixture(scope="session")
def build_dataset(tmp_path_factory, work_root):
    """Factory: generate (or accept) a dataset, build it, and keep the connection for cleanup."""
    opened: list[duckdb.DuckDBPyConnection] = []

    def make(name: str, config: SynthConfig | None = None, dataset: Dataset | None = None) -> Built:
        data = dataset if dataset is not None else generate(config or SynthConfig(seed=SEED))
        data_dir = tmp_path_factory.mktemp("data") / name
        write_dataset(data, data_dir)
        result = runner.build(data_dir, work_root)
        con = runner.connect(result.db_path)
        opened.append(con)
        return Built(name, data_dir, work_root, data, result, con)

    yield make
    for con in opened:
        con.close()


@pytest.fixture(scope="session")
def full(build_dataset) -> Built:
    return build_dataset("full", SynthConfig(seed=SEED))


@pytest.fixture(scope="session")
def nodrop(build_dataset) -> Built:
    return build_dataset("nodrop", SynthConfig(seed=SEED, enterprise_negotiation_drop=False))


@pytest.fixture(scope="session")
def homog(build_dataset) -> Built:
    return build_dataset("homog", SynthConfig(seed=11, homogeneous=True))


@pytest.fixture(scope="session")
def dedup(build_dataset, full) -> Built:
    """The full dataset with its exact duplicate history rows already removed."""
    source = full.dataset
    clean = Dataset(
        users=source.users,
        opportunities=source.opportunities,
        history=drop_exact_duplicates(source.history),
        truth=source.truth,
        meta=source.meta,
        config=source.config,
    )
    return build_dataset("dedup", dataset=clean)
