"""Write pending ORM changes back to the .mpp and .xlsx source files."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Scrive le modifiche pendenti su dataset.mpp e jobs.xlsx senza "
        "resettare il database. Crea un backup dei file sorgente; "
        "opzionalmente crea anche un backup di db.sqlite3."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--backup-db",
            action="store_true",
            help="Crea una copia di db.sqlite3 prima del write-back.",
        )
        parser.add_argument(
            "--backup-dir",
            default="",
            help="Cartella per i backup (default: directory del progetto).",
        )
        parser.add_argument(
            "--no-source-backup",
            action="store_true",
            help="Non creare backup di .mpp/.xlsx prima del write-back.",
        )

    def handle(self, *args, **options):
        from ui.services.importer import configure, set_files_readonly
        from ui.services.runtime_config import load_runtime_config
        from ui.services.sync_state import backup_source_files, clear_sync_queue

        config = load_runtime_config()
        configure(config.mpp_path, config.xlsx_path)

        backup_dir = options["backup_dir"] or None
        if options["backup_db"]:
            backup_path = self._backup_sqlite(backup_dir or "")
            self.stdout.write(self.style.SUCCESS(f"Backup DB: {backup_path}"))

        if not options["no_source_backup"]:
            created = backup_source_files(config.mpp_path, config.xlsx_path, backup_dir)
            for path in created:
                self.stdout.write(self.style.SUCCESS(f"Backup sorgente: {path}"))

        self.stdout.write("Avvio write-back su .mpp e .xlsx...")
        try:
            self._write_back(config.mpp_path, config.xlsx_path)
            clear_sync_queue()
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Write-back fallito: {exc}") from exc

        set_files_readonly()
        self.stdout.write(self.style.SUCCESS("Write-back completato; coda cancellata."))

    def _backup_sqlite(self, backup_dir: str) -> Path:
        db_path = Path(settings.DATABASES["default"]["NAME"])
        if not db_path.is_file():
            raise CommandError(f"Database non trovato: {db_path}")

        target_dir = Path(backup_dir) if backup_dir else db_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = target_dir / f"{db_path.stem}.backup-{stamp}{db_path.suffix}"

        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(backup_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        return backup_path

    def _write_back(self, mpp_path: str, xlsx_path: str) -> None:
        import importlib

        import pythoncom

        from ui.models import MppSyncQueue
        from ui.services.importer import set_files_writable
        from ui.services.writeback import write_pending_changes

        project_module = importlib.import_module("src.data.project")
        ProjectClass = project_module.Project
        _dispatch_msp = project_module._dispatch_msp

        pythoncom.CoInitialize()
        app = None
        try:
            set_files_writable()

            if MppSyncQueue.objects.exists():
                app = _dispatch_msp()
                app.Visible = False
                app.FileOpen(os.path.abspath(mpp_path))
                project_com = app.ActiveProject

                if project_com.ReadOnly:
                    raise CommandError(
                        "Il file .mpp è in sola lettura (aperto da un altro processo?)."
                    )

                wrapper = ProjectClass.from_open_app(app, project_com)
                logger.info(
                    "COM session aperta per write-back: %s", os.path.basename(mpp_path)
                )

                write_pending_changes(wrapper)
                app.FileSave()
                logger.info("File .mpp salvato.")
            else:
                logger.info("Coda vuota — salto fase COM.")

            from src.data.export_xlsx import export_to_xlsx

            export_to_xlsx(xlsx_path)
            logger.info("File .xlsx esportato.")
        finally:
            if app is not None:
                try:
                    project_module._safe_close_and_quit(app)
                except Exception:  # noqa: BLE001
                    pass
            pythoncom.CoUninitialize()
