"""Tests for the deterministic SQLite mockup database."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from mockup_db import create_mockup_db
from src.optimization.db_problem import load_scheduling_problem_from_sqlite
from src.optimization.feasibility import check_feasibility


def test_create_mockup_db_loads_as_feasible_problem(tmp_path):
    db_path = create_mockup_db(tmp_path / "mockup.sqlite3")

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM attivita").fetchone()[0] == 14
        assert conn.execute("SELECT COUNT(*) FROM risorse_attivita").fetchone()[0] > 0

    problem = load_scheduling_problem_from_sqlite(db_path)
    report = check_feasibility(problem)

    assert len(problem.jobs) == 6
    assert sum(len(job.activities) for job in problem.jobs) == 14
    assert len(problem.resources) == 36
    assert report.feasible
