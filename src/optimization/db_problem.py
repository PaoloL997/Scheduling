"""Build realistic scheduling problems from the local SQLite application DB.

The Django app already stores the two inputs the solver needs: operational
activities in ``attivita`` and physical inventory in the ``*_item`` tables.
This module converts those rows into the pure optimization data model, without
depending on Django settings or ORM startup.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.optimization.calendar import working_days_in_calendar_span
from src.optimization.compatibility import configure_generator_compatibility
from src.optimization.data_model import (
    PLANTS,
    Activity,
    Job,
    Resource,
    ResourceType,
    SchedulingProblem,
)

DEFAULT_DB_PATH = Path("db.sqlite3")
MINUTES_PER_WORKING_DAY = 8 * 60


@dataclass(frozen=True)
class _ActivityRow:
    id: int
    commessa: str
    item: int
    activity: str
    stabilimento: str
    modello_testa: str | None
    bore_min: float | None
    bore_mid: float | None
    bore_max: float | None
    tube_pitch: float | None
    data_inizio: date | None
    data_fine: date | None
    duration_min: int | None


@dataclass(frozen=True)
class _JobRow:
    commessa: str
    total_cost: float | None
    penalty: float | None
    delivery_date: date | None


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _plant(value: Any) -> str:
    text = _clean_text(value)
    if text in PLANTS:
        return text
    return "Valbrembo"


def _duration_days(
    duration_min: int | None, start: date | None, end: date | None
) -> int:
    if start is not None and end is not None:
        return working_days_in_calendar_span(start, end)
    if duration_min and duration_min > 0:
        return max(1, math.ceil(duration_min / MINUTES_PER_WORKING_DAY))
    return 1


def _activity_id(row: _ActivityRow, clone_index: int) -> str:
    suffix = "" if clone_index == 0 else f"-c{clone_index:02d}"
    label = row.activity.replace(" ", "_") or "activity"
    return f"{row.commessa}-{row.item}-{label}-{row.id}{suffix}"


def _job_id(commessa: str, clone_index: int) -> str:
    return commessa if clone_index == 0 else f"{commessa}-c{clone_index:02d}"


def _penalty_per_week(job: _JobRow | None) -> float:
    if job is None:
        return 0.0
    if job.total_cost and job.penalty and job.total_cost > 0 and job.penalty > 0:
        return job.total_cost * job.penalty / 100
    if job.penalty and job.penalty > 0:
        return job.penalty
    return 0.0


def _due_date(
    job: _JobRow | None,
    rows: list[_ActivityRow],
    *,
    clone_index: int,
    clone_spacing_days: int,
    due_date_tightening_days: int,
) -> date:
    if job is not None and job.delivery_date is not None:
        base = job.delivery_date
    else:
        latest_finish = max(
            (row.data_fine for row in rows if row.data_fine), default=None
        )
        base = latest_finish or date.today()
    return base + timedelta(
        days=clone_index * clone_spacing_days - due_date_tightening_days
    )


def _fetch_activity_rows(conn: sqlite3.Connection) -> list[_ActivityRow]:
    rows = conn.execute(
        """
        SELECT id, commessa, item, activity, stabilimento, modello_testa,
               bore_min, bore_mid, bore_max, tube_pitch, data_inizio, data_fine,
               duration_min
        FROM attivita
        ORDER BY commessa, item, id
        """
    ).fetchall()
    return [
        _ActivityRow(
            id=int(row["id"]),
            commessa=str(row["commessa"]),
            item=int(row["item"]),
            activity=str(row["activity"] or ""),
            stabilimento=str(row["stabilimento"] or ""),
            modello_testa=_clean_text(row["modello_testa"]),
            bore_min=_parse_float(row["bore_min"]),
            bore_mid=_parse_float(row["bore_mid"]),
            bore_max=_parse_float(row["bore_max"]),
            tube_pitch=_parse_float(row["tube_pitch"]),
            data_inizio=_parse_date(row["data_inizio"]),
            data_fine=_parse_date(row["data_fine"]),
            duration_min=int(row["duration_min"]) if row["duration_min"] else None,
        )
        for row in rows
    ]


def _fetch_job_rows(conn: sqlite3.Connection) -> dict[str, _JobRow]:
    rows = conn.execute(
        "SELECT commessa, total_cost, penalty, delivery_date FROM job"
    ).fetchall()
    return {
        str(row["commessa"]): _JobRow(
            commessa=str(row["commessa"]),
            total_cost=_parse_float(row["total_cost"]),
            penalty=_parse_float(row["penalty"]),
            delivery_date=_parse_date(row["delivery_date"]),
        )
        for row in rows
    }


def _fetch_required_quantities(
    conn: sqlite3.Connection,
) -> dict[str, dict[ResourceType, int]]:
    quantities: dict[str, dict[ResourceType, int]] = {}
    rows = conn.execute(
        """
        SELECT modello_testa, min_mandrini, min_tastatori, min_maschere,
               min_generatori
        FROM configurazione_testa
        """
    ).fetchall()
    for row in rows:
        model = _clean_text(row["modello_testa"])
        if model is None:
            continue
        quantities[model] = {
            ResourceType.MANDRINO: max(0, int(row["min_mandrini"] or 0)),
            ResourceType.TASTATORE: max(0, int(row["min_tastatori"] or 0)),
            ResourceType.MASCHERA: max(0, int(row["min_maschere"] or 0)),
            ResourceType.GENERATORE: max(0, int(row["min_generatori"] or 0)),
            ResourceType.TESTA: 1,
        }
    return quantities


def _fetch_generator_compatibility(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT ct.modello_testa, gc.modello_generatore
        FROM generatore_compatibile gc
        JOIN configurazione_testa ct ON ct.id = gc.configurazione_id
        """
    ).fetchall()
    compatibility: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        head_model = _clean_text(row["modello_testa"])
        generator_model = _clean_text(row["modello_generatore"])
        if head_model is not None and generator_model is not None:
            compatibility[head_model].append(generator_model)
    return dict(compatibility)


def _fetch_inventory(conn: sqlite3.Connection) -> list[Resource]:
    resources: list[Resource] = []

    for row in conn.execute("SELECT uid, diameter, plant FROM mandrino_item"):
        bore = _parse_float(row["diameter"])
        if bore is not None and bore > 0:
            resources.append(
                Resource(
                    resource_id=f"mandrino:{row['uid']}",
                    resource_type=ResourceType.MANDRINO,
                    plant=_plant(row["plant"]),
                    bore=bore,
                )
            )

    for row in conn.execute("SELECT uid, diameter, plant FROM tastatore_item"):
        bore = _parse_float(row["diameter"])
        if bore is not None and bore > 0:
            resources.append(
                Resource(
                    resource_id=f"tastatore:{row['uid']}",
                    resource_type=ResourceType.TASTATORE,
                    plant=_plant(row["plant"]),
                    bore=bore,
                )
            )

    for row in conn.execute("SELECT uid, pitch, plant FROM maschera_item"):
        pitch = _parse_float(row["pitch"])
        if pitch is not None and pitch > 0:
            resources.append(
                Resource(
                    resource_id=f"maschera:{row['uid']}",
                    resource_type=ResourceType.MASCHERA,
                    plant=_plant(row["plant"]),
                    tube_pitch=pitch,
                )
            )

    for row in conn.execute("SELECT uid, model, plant FROM testa_item"):
        model = _clean_text(row["model"])
        if model is not None:
            resources.append(
                Resource(
                    resource_id=f"testa:{row['uid']}",
                    resource_type=ResourceType.TESTA,
                    plant=_plant(row["plant"]),
                    model=model,
                )
            )

    for row in conn.execute("SELECT uid, model, plant FROM generatore_item"):
        model = _clean_text(row["model"])
        if model is not None:
            resources.append(
                Resource(
                    resource_id=f"generatore:{row['uid']}",
                    resource_type=ResourceType.GENERATORE,
                    plant=_plant(row["plant"]),
                    model=model,
                )
            )

    return resources


def _build_jobs(
    activity_rows: list[_ActivityRow],
    job_rows: dict[str, _JobRow],
    quantity_by_head_model: dict[str, dict[ResourceType, int]],
    *,
    amplify_factor: int,
    clone_spacing_days: int,
    due_date_tightening_days: int,
) -> list[Job]:
    grouped: dict[str, list[_ActivityRow]] = defaultdict(list)
    for row in activity_rows:
        grouped[row.commessa].append(row)

    jobs: list[Job] = []
    for clone_index in range(amplify_factor):
        for commessa, rows in sorted(grouped.items()):
            job_id = _job_id(commessa, clone_index)
            shift = timedelta(days=clone_index * clone_spacing_days)
            activities: list[Activity] = []
            for order, row in enumerate(sorted(rows, key=lambda r: (r.item, r.id)), 1):
                quantities = quantity_by_head_model.get(
                    row.modello_testa or "", {}
                ).copy()
                activities.append(
                    Activity(
                        activity_id=_activity_id(row, clone_index),
                        job_id=job_id,
                        order=order,
                        plant=_plant(row.stabilimento),
                        duration_days=_duration_days(
                            row.duration_min,
                            row.data_inizio,
                            row.data_fine,
                        ),
                        earliest_start=(
                            row.data_inizio + shift
                            if row.data_inizio is not None
                            else None
                        ),
                        bore_min=row.bore_min,
                        bore_mid=row.bore_mid,
                        bore_max=row.bore_max,
                        tube_pitch=row.tube_pitch,
                        head_model=row.modello_testa,
                        required_quantities=quantities,
                    )
                )

            jobs.append(
                Job(
                    job_id=job_id,
                    due_date=_due_date(
                        job_rows.get(commessa),
                        rows,
                        clone_index=clone_index,
                        clone_spacing_days=clone_spacing_days,
                        due_date_tightening_days=due_date_tightening_days,
                    ),
                    penalty_per_week=_penalty_per_week(job_rows.get(commessa)),
                    activities=activities,
                )
            )

    return jobs


def load_scheduling_problem_from_sqlite(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    horizon_start: date | None = None,
    horizon_days: int | None = None,
    amplify_factor: int = 1,
    clone_spacing_days: int = 3,
    due_date_tightening_days: int = 0,
) -> SchedulingProblem:
    """Load a ``SchedulingProblem`` from the application SQLite database.

    ``amplify_factor`` deterministically clones the real commesse to create a
    larger benchmark while preserving durations, requirements and due-date
    pressure. Use ``1`` to get only the exact DB activities.
    """
    if amplify_factor < 1:
        raise ValueError("amplify_factor must be >= 1.")

    with closing(_connect(db_path)) as conn:
        activity_rows = _fetch_activity_rows(conn)
        if not activity_rows:
            raise ValueError(f"No activities found in {db_path}.")
        job_rows = _fetch_job_rows(conn)
        quantity_by_head_model = _fetch_required_quantities(conn)
        generator_compatibility = _fetch_generator_compatibility(conn)
        resources = _fetch_inventory(conn)

    if generator_compatibility:
        configure_generator_compatibility(generator_compatibility)

    if horizon_start is None:
        starts = [
            row.data_inizio for row in activity_rows if row.data_inizio is not None
        ]
        horizon_start = min(starts) if starts else date.today()

    jobs = _build_jobs(
        activity_rows,
        job_rows,
        quantity_by_head_model,
        amplify_factor=amplify_factor,
        clone_spacing_days=clone_spacing_days,
        due_date_tightening_days=due_date_tightening_days,
    )

    return SchedulingProblem(
        jobs=jobs,
        resources=resources,
        horizon_start=horizon_start,
        horizon_days=horizon_days,
    )


def _summary(problem: SchedulingProblem) -> str:
    activities = sum(len(job.activities) for job in problem.jobs)
    by_type: dict[ResourceType, int] = defaultdict(int)
    for resource in problem.resources:
        by_type[resource.resource_type] += 1
    resource_summary = ", ".join(
        f"{resource_type.value}={by_type[resource_type]}"
        for resource_type in sorted(by_type, key=lambda value: value.value)
    )
    return (
        f"jobs={len(problem.jobs)}, activities={activities}, "
        f"resources={len(problem.resources)} ({resource_summary}), "
        f"horizon_start={problem.horizon_start}, "
        f"horizon_days={problem.horizon_days}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a SchedulingProblem from db.sqlite3."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--amplify-factor", type=int, default=1)
    parser.add_argument("--clone-spacing-days", type=int, default=3)
    parser.add_argument("--due-date-tightening-days", type=int, default=0)
    parser.add_argument("--horizon-days", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--solve", action="store_true")
    parser.add_argument("--time-limit", type=float, default=10.0)
    parser.add_argument(
        "--plot-output",
        help="Optional PNG/PDF/SVG path where the solved Gantt chart is saved.",
    )
    args = parser.parse_args()

    problem = load_scheduling_problem_from_sqlite(
        args.db,
        horizon_days=args.horizon_days,
        amplify_factor=args.amplify_factor,
        clone_spacing_days=args.clone_spacing_days,
        due_date_tightening_days=args.due_date_tightening_days,
    )

    if args.json:
        print(problem.model_dump_json(indent=2))
    else:
        print(_summary(problem))

    if args.solve:
        from src.optimization.solver import schedule

        result, diagnoses = schedule(problem, time_limit=args.time_limit)
        if result is None:
            print("solve_status=infeasible")
            return
        print(
            "solve_status=feasible, "
            f"total_penalty={result.total_penalty}, "
            f"late_jobs={result.num_late_jobs}, "
            f"diagnoses={len(diagnoses)}"
        )
        if args.plot_output:
            from src.optimization.visualization import plot_schedule_result

            plot_schedule_result(problem, result, output_path=args.plot_output)
            print(f"plot_output={args.plot_output}")


if __name__ == "__main__":
    main()
