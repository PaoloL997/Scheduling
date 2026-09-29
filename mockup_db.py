"""Create a deterministic SQLite database for scheduling solver tests.

The generated database mirrors the application tables used by
``src.optimization.db_problem`` and includes enough reference tables to inspect
the data from the Django UI during local experiments.
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

DEFAULT_MOCKUP_DB_PATH = Path("mockup.sqlite3")


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE stabilimento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome varchar(200) NOT NULL UNIQUE,
            colore varchar(7) NOT NULL DEFAULT '#6366f1'
        );

        CREATE TABLE responsabile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome varchar(200) NOT NULL UNIQUE
        );

        CREATE TABLE app_settings (
            id INTEGER PRIMARY KEY,
            penale_max decimal NOT NULL DEFAULT 5
        );

        CREATE TABLE job (
            commessa varchar(100) NOT NULL PRIMARY KEY,
            total_cost REAL NULL,
            penalty REAL NULL,
            delivery_date varchar(10) NULL
        );

        CREATE TABLE attivita (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            commessa varchar(100) NOT NULL,
            item INTEGER NOT NULL,
            activity varchar(200) NOT NULL DEFAULT '',
            stabilimento varchar(200) NOT NULL DEFAULT '',
            responsabile varchar(200) NOT NULL DEFAULT '',
            modello_testa varchar(100) NOT NULL DEFAULT '',
            bore_min decimal NULL,
            bore_max decimal NULL,
            bore_mid decimal NULL,
            tube_pitch decimal NULL,
            data_inizio date NULL,
            data_fine date NULL,
            created_at datetime NOT NULL,
            actual_finish date NULL,
            actual_start date NULL,
            baseline_finish date NULL,
            baseline_start date NULL,
            duration_min INTEGER NULL,
            predecessors TEXT NULL,
            activity_id varchar(255) NOT NULL DEFAULT '',
            job_id varchar(255) NOT NULL DEFAULT '',
            UNIQUE (commessa, item, activity)
        );

        CREATE TABLE risorse_attivita (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            tipo varchar(20) NOT NULL,
            risorsa_id varchar(50) NOT NULL,
            attivita_id bigint NOT NULL REFERENCES attivita(id)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE testa_item (
            uid varchar(100) NOT NULL PRIMARY KEY,
            model varchar(200) NULL,
            plant varchar(200) NULL,
            notes TEXT NULL
        );

        CREATE TABLE mandrino_item (
            uid varchar(100) NOT NULL PRIMARY KEY,
            diameter varchar(50) NULL,
            plant varchar(200) NULL,
            notes TEXT NULL
        );

        CREATE TABLE tastatore_item (
            uid varchar(100) NOT NULL PRIMARY KEY,
            diameter varchar(50) NULL,
            plant varchar(200) NULL,
            notes TEXT NULL
        );

        CREATE TABLE maschera_item (
            uid varchar(100) NOT NULL PRIMARY KEY,
            pitch varchar(50) NULL,
            plant varchar(200) NULL,
            center_distance TEXT NULL
        );

        CREATE TABLE generatore_item (
            uid varchar(100) NOT NULL PRIMARY KEY,
            model varchar(200) NULL,
            plant varchar(200) NULL,
            notes TEXT NULL
        );

        CREATE TABLE configurazione_testa (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            modello_testa varchar(100) NOT NULL UNIQUE,
            min_mandrini integer unsigned NOT NULL DEFAULT 1,
            min_tastatori integer unsigned NOT NULL DEFAULT 1,
            min_maschere integer unsigned NOT NULL DEFAULT 1,
            min_generatori integer unsigned NOT NULL DEFAULT 1
        );

        CREATE TABLE generatore_compatibile (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            modello_generatore varchar(100) NOT NULL,
            configurazione_id bigint NOT NULL REFERENCES configurazione_testa(id)
                DEFERRABLE INITIALLY DEFERRED,
            UNIQUE (configurazione_id, modello_generatore)
        );

        CREATE TABLE problema_attivita (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            tipo varchar(20) NOT NULL,
            risorsa_tipo varchar(20) NOT NULL,
            risorsa_uid varchar(100) NOT NULL DEFAULT '',
            stabilimento_origine varchar(200) NOT NULL DEFAULT '',
            descrizione TEXT NOT NULL,
            risolto bool NOT NULL DEFAULT 0,
            note_risoluzione TEXT NOT NULL DEFAULT '',
            data_risoluzione datetime NULL,
            created_at datetime NOT NULL,
            attivita_id bigint NOT NULL REFERENCES attivita(id)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE risoluzione_problema (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            commessa varchar(100) NOT NULL,
            item INTEGER NOT NULL,
            activity varchar(200) NOT NULL DEFAULT '',
            tipo varchar(20) NOT NULL,
            risorsa_tipo varchar(20) NOT NULL,
            risorsa_uid varchar(100) NOT NULL DEFAULT '',
            note_risoluzione TEXT NOT NULL DEFAULT '',
            data_risoluzione datetime NULL,
            updated_at datetime NOT NULL,
            UNIQUE (
                commessa,
                item,
                activity,
                tipo,
                risorsa_tipo,
                risorsa_uid
            )
        );
        """
    )


def _seed_reference_data(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO stabilimento (nome, colore) VALUES (?, ?)",
        [
            ("Ricengo", "#2563eb"),
            ("Valbrembo", "#16a34a"),
            ("Schio", "#dc2626"),
        ],
    )
    conn.executemany(
        "INSERT INTO responsabile (nome) VALUES (?)",
        [
            ("Emanuele Galbiati",),
            ("Mirko Carminati",),
            ("Sara Colombo",),
            ("Luca Ferri",),
        ],
    )
    conn.execute("INSERT INTO app_settings (id, penale_max) VALUES (1, 5)")


def _seed_inventory(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO mandrino_item (uid, diameter, plant, notes) VALUES (?, ?, ?, ?)",
        [
            ("C101", "14.83", "Ricengo", "Nominale 14.83"),
            ("C102", "14.83", "Ricengo", "Gemello per setup TP60"),
            ("C103", "15.75", "Ricengo", "Bore range 15-16"),
            ("C104", "15.75", "Valbrembo", "Disponibile con trasferimento"),
            ("C105", "16.00", "Schio", "Jolly medio"),
            ("C106", "19.05", "Ricengo", "Passo 19.05"),
            ("C107", "19.05", "Valbrembo", "Secondo mandrino 19.05"),
            ("C108", "21.00", "Schio", "Bore alto"),
            ("C109", "12.70", "Ricengo", "Piccolo diametro"),
            ("C111", "12.70", "Valbrembo", "Secondo piccolo diametro"),
            ("C110", "0", "Ricengo", "Dato sporco intenzionale, ignorato"),
        ],
    )
    conn.executemany(
        "INSERT INTO tastatore_item (uid, diameter, plant, notes) VALUES (?, ?, ?, ?)",
        [
            ("T201", "14.83", "Ricengo", None),
            ("T202", "15.75", "Ricengo", None),
            ("T203", "15.75", "Valbrembo", None),
            ("T204", "19.05", "Ricengo", None),
            ("T205", "19.05", "Schio", None),
            ("T206", "21.00", "Schio", None),
            ("T207", "12.70", "Ricengo", None),
        ],
    )
    conn.executemany(
        "INSERT INTO maschera_item (uid, pitch, plant, center_distance) VALUES (?, ?, ?, ?)",
        [
            ("M301", "25.4", "Ricengo", "127"),
            ("M302", "25.4", "Valbrembo", "127"),
            ("M303", "19.05", "Ricengo", "114.3"),
            ("M304", "19.05", "Schio", "114.3"),
            ("M305", "31.75", "Schio", "158.75"),
            ("M306", "20.0", "Valbrembo", "120"),
        ],
    )
    conn.executemany(
        "INSERT INTO testa_item (uid, model, plant, notes) VALUES (?, ?, ?, ?)",
        [
            ("H401", "TP60", "Ricengo", None),
            ("H402", "TP60", "Ricengo", None),
            ("H403", "TP60", "Valbrembo", None),
            ("H404", "TP60", "Schio", None),
            ("H405", "SPX", "Ricengo", None),
            ("H406", "SPX", "Schio", None),
        ],
    )
    conn.executemany(
        "INSERT INTO generatore_item (uid, model, plant, notes) VALUES (?, ?, ?, ?)",
        [
            ("G501", "P6", "Ricengo", None),
            ("G502", "P6-3", "Ricengo", None),
            ("G503", "P406", "Valbrembo", None),
            ("G504", "PC2", "Schio", None),
            ("G505", "PC350DC", "Ricengo", None),
            ("G506", "SPX", "Ricengo", None),
            ("G507", "SPX", "Schio", None),
        ],
    )


def _seed_configurations(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO configurazione_testa (
            id,
            modello_testa,
            min_mandrini,
            min_tastatori,
            min_maschere,
            min_generatori
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "TP60", 2, 1, 1, 1),
            (2, "SPX", 1, 1, 1, 1),
        ],
    )
    conn.executemany(
        """
        INSERT INTO generatore_compatibile (
            configurazione_id,
            modello_generatore
        )
        VALUES (?, ?)
        """,
        [
            (1, "P6"),
            (1, "P6-3"),
            (1, "P406"),
            (1, "PC2"),
            (1, "PC350DC"),
            (2, "SPX"),
        ],
    )


def _seed_jobs_and_activities(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO job (commessa, total_cost, penalty, delivery_date)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("26001", 1_000_000, 1.6, "2026-07-24"),
            ("26002", 780_000, 1.8, "2026-07-31"),
            ("26003", 420_000, 2.5, "2026-08-07"),
            ("26004", 950_000, 1.4, "2026-08-14"),
            ("26005", 610_000, 2.1, "2026-08-21"),
            ("26006", 1_250_000, 1.2, "2026-08-28"),
        ],
    )

    activities = [
        (
            "26001",
            1,
            "Foratura",
            "Ricengo",
            "Emanuele Galbiati",
            "TP60",
            14.99,
            16.51,
            15.75,
            25.4,
            "2026-06-22",
            "2026-07-01",
            3840,
            None,
        ),
        (
            "26001",
            2,
            "Qualifica",
            "Ricengo",
            "Mirko Carminati",
            "TP60",
            14.99,
            16.51,
            15.75,
            25.4,
            "2026-07-02",
            "2026-07-13",
            4320,
            "1",
        ),
        (
            "26001",
            3,
            "Collaudo",
            "Ricengo",
            "Sara Colombo",
            "TP60",
            14.99,
            16.51,
            15.75,
            25.4,
            "2026-07-14",
            "2026-07-20",
            2400,
            "2",
        ),
        (
            "26002",
            1,
            "Preparazione",
            "Ricengo",
            "Emanuele Galbiati",
            "TP60",
            13.88,
            15.77,
            14.83,
            25.4,
            "2026-06-25",
            "2026-07-03",
            3360,
            None,
        ),
        (
            "26002",
            2,
            "Validazione",
            "Ricengo",
            "Emanuele Galbiati",
            "TP60",
            13.88,
            15.77,
            14.83,
            25.4,
            "2026-07-06",
            "2026-07-17",
            4800,
            "1",
        ),
        (
            "26003",
            1,
            "Setup SPX",
            "Schio",
            "Luca Ferri",
            "SPX",
            18.80,
            19.30,
            19.05,
            19.05,
            "2026-07-06",
            "2026-07-14",
            3360,
            None,
        ),
        (
            "26003",
            2,
            "Produzione SPX",
            "Schio",
            "Luca Ferri",
            "SPX",
            18.80,
            19.30,
            19.05,
            19.05,
            "2026-07-15",
            "2026-07-28",
            5280,
            "1",
        ),
        (
            "26004",
            1,
            "Foratura grande",
            "Valbrembo",
            "Mirko Carminati",
            "TP60",
            18.70,
            19.40,
            19.05,
            19.05,
            "2026-07-10",
            "2026-07-21",
            4800,
            None,
        ),
        (
            "26004",
            2,
            "Banco caldo",
            "Valbrembo",
            "Sara Colombo",
            "TP60",
            18.70,
            19.40,
            19.05,
            19.05,
            "2026-07-22",
            "2026-08-03",
            4320,
            "1",
        ),
        (
            "26004",
            3,
            "FAT",
            "Ricengo",
            "Sara Colombo",
            "TP60",
            18.70,
            19.40,
            19.05,
            19.05,
            "2026-08-04",
            "2026-08-10",
            2400,
            "2",
        ),
        (
            "26005",
            1,
            "Microforatura",
            "Ricengo",
            "Emanuele Galbiati",
            "TP60",
            12.40,
            13.00,
            12.70,
            20.0,
            "2026-07-20",
            "2026-07-29",
            3840,
            None,
        ),
        (
            "26005",
            2,
            "Ripresa",
            "Valbrembo",
            "Mirko Carminati",
            "TP60",
            12.40,
            13.00,
            12.70,
            20.0,
            "2026-07-30",
            "2026-08-07",
            3360,
            "1",
        ),
        (
            "26006",
            1,
            "Setup alto",
            "Schio",
            "Luca Ferri",
            "SPX",
            20.60,
            21.30,
            21.00,
            31.75,
            "2026-07-27",
            "2026-08-05",
            3840,
            None,
        ),
        (
            "26006",
            2,
            "Ciclo alto",
            "Schio",
            "Luca Ferri",
            "SPX",
            20.60,
            21.30,
            21.00,
            31.75,
            "2026-08-06",
            "2026-08-18",
            4800,
            "1",
        ),
    ]

    rows = []
    for commessa, item, activity, *_rest in activities:
        activity_id = f"{commessa}-{item:02d}-{activity.replace(' ', '_')}"
        rows.append(
            (*((commessa, item, activity) + tuple(_rest)), activity_id, commessa)
        )

    conn.executemany(
        """
        INSERT INTO attivita (
            commessa,
            item,
            activity,
            stabilimento,
            responsabile,
            modello_testa,
            bore_min,
            bore_max,
            bore_mid,
            tube_pitch,
            data_inizio,
            data_fine,
            duration_min,
            predecessors,
            created_at,
            activity_id,
            job_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-06-05 12:00:00', ?, ?)
        """,
        rows,
    )


def _seed_resource_assignments(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, commessa, item, modello_testa FROM attivita ORDER BY id"
    ).fetchall()
    resource_plan = {
        ("26001", 1): {
            "mandrino": ["C103", "C104"],
            "tastatore": ["T202"],
            "maschera": ["M301"],
            "testa": ["H401"],
            "generatore": ["G501"],
        },
        ("26001", 2): {
            "mandrino": ["C103", "C105"],
            "tastatore": ["T203"],
            "maschera": ["M302"],
            "testa": ["H402"],
            "generatore": ["G502"],
        },
        ("26001", 3): {
            "mandrino": ["C103", "C104"],
            "tastatore": ["T202"],
            "maschera": ["M301"],
            "testa": ["H401"],
            "generatore": ["G505"],
        },
        ("26002", 1): {
            "mandrino": ["C101", "C102"],
            "tastatore": ["T201"],
            "maschera": ["M301"],
            "testa": ["H402"],
            "generatore": ["G503"],
        },
        ("26002", 2): {
            "mandrino": ["C101", "C102"],
            "tastatore": ["T201"],
            "maschera": ["M302"],
            "testa": ["H403"],
            "generatore": ["G501"],
        },
        ("26003", 1): {
            "mandrino": ["C106"],
            "tastatore": ["T204"],
            "maschera": ["M303"],
            "testa": ["H405"],
            "generatore": ["G506"],
        },
        ("26003", 2): {
            "mandrino": ["C107"],
            "tastatore": ["T205"],
            "maschera": ["M304"],
            "testa": ["H406"],
            "generatore": ["G507"],
        },
        ("26004", 1): {
            "mandrino": ["C106", "C107"],
            "tastatore": ["T204"],
            "maschera": ["M303"],
            "testa": ["H403"],
            "generatore": ["G504"],
        },
        ("26004", 2): {
            "mandrino": ["C106", "C107"],
            "tastatore": ["T205"],
            "maschera": ["M304"],
            "testa": ["H404"],
            "generatore": ["G503"],
        },
        ("26004", 3): {
            "mandrino": ["C106", "C107"],
            "tastatore": ["T204"],
            "maschera": ["M303"],
            "testa": ["H401"],
            "generatore": ["G501"],
        },
        ("26005", 1): {
            "mandrino": ["C109", "C111"],
            "tastatore": ["T207"],
            "maschera": ["M306"],
            "testa": ["H402"],
            "generatore": ["G502"],
        },
        ("26005", 2): {
            "mandrino": ["C109", "C111"],
            "tastatore": ["T207"],
            "maschera": ["M306"],
            "testa": ["H403"],
            "generatore": ["G505"],
        },
        ("26006", 1): {
            "mandrino": ["C108"],
            "tastatore": ["T206"],
            "maschera": ["M305"],
            "testa": ["H406"],
            "generatore": ["G507"],
        },
        ("26006", 2): {
            "mandrino": ["C108"],
            "tastatore": ["T206"],
            "maschera": ["M305"],
            "testa": ["H405"],
            "generatore": ["G506"],
        },
    }

    assignments = []
    for activity_id, commessa, item, _head_model in rows:
        for resource_type, resource_ids in resource_plan[(commessa, item)].items():
            assignments.extend(
                (resource_type, resource_id, activity_id)
                for resource_id in resource_ids
            )
    conn.executemany(
        """
        INSERT INTO risorse_attivita (tipo, risorsa_id, attivita_id)
        VALUES (?, ?, ?)
        """,
        assignments,
    )


def _seed_problem_examples(conn: sqlite3.Connection) -> None:
    activity_id = conn.execute(
        "SELECT id FROM attivita WHERE commessa = '26004' AND item = 1"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO problema_attivita (
            tipo,
            risorsa_tipo,
            risorsa_uid,
            stabilimento_origine,
            descrizione,
            risolto,
            created_at,
            attivita_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "spostamento",
            "generatore",
            "G504",
            "Schio",
            "Generatore selezionato fuori stabilimento per simulare trasferimento.",
            0,
            "2026-06-05 12:05:00",
            activity_id,
        ),
    )


def create_mockup_db(
    db_path: str | Path = DEFAULT_MOCKUP_DB_PATH,
    *,
    overwrite: bool = True,
) -> Path:
    """Create and seed a complete deterministic scheduling test database."""
    path = Path(db_path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists.")
    if path.exists():
        path.unlink()

    with closing(sqlite3.connect(path)) as conn:
        _create_schema(conn)
        _seed_reference_data(conn)
        _seed_inventory(conn)
        _seed_configurations(conn)
        _seed_jobs_and_activities(conn)
        _seed_resource_assignments(conn)
        _seed_problem_examples(conn)
        conn.commit()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a scheduling mockup DB.")
    parser.add_argument("--output", default=str(DEFAULT_MOCKUP_DB_PATH))
    parser.add_argument("--no-overwrite", action="store_true")
    args = parser.parse_args()

    path = create_mockup_db(args.output, overwrite=not args.no_overwrite)
    print(path)


if __name__ == "__main__":
    main()
