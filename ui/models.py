"""Django ORM models for production planning activities and resource assignments."""

from decimal import Decimal

from django.db import models
from django.db.models import UniqueConstraint

from ui.services.identifiers import build_activity_id, build_job_id


class Attivita(models.Model):
    """An activity planned for production scheduling.

    This is the single source of truth for all activity data.  Records are
    created in two ways: by :func:`src.data.import.import_from_mpp` during
    the initial import (``imported_from_mpp=True``) and through the
    multi-step add-activity wizard (``imported_from_mpp=False``).

    All CRUD operations during a web-app session target this table.
    When the last user logs out, pending changes are written back to the
    ``.mpp`` file via :class:`MppSyncQueue`.

    Resource assignments are stored in the related :class:`RisorsaAttivita`
    model.
    """

    commessa = models.CharField(max_length=100)
    item = models.IntegerField()
    activity = models.CharField(max_length=200, blank=True, default="")
    job_id = models.CharField(max_length=250, db_index=True, blank=True, default="")
    activity_id = models.CharField(max_length=500, unique=True, blank=True, default="")

    duration_min = models.IntegerField(null=True, blank=True)
    data_inizio = models.DateField(null=True, blank=True)
    data_fine = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_finish = models.DateField(null=True, blank=True)
    baseline_start = models.DateField(null=True, blank=True)
    baseline_finish = models.DateField(null=True, blank=True)
    predecessors = models.TextField(null=True, blank=True)

    stabilimento = models.CharField(max_length=200, blank=True, default="")
    responsabile = models.CharField(max_length=200, blank=True, default="")

    modello_testa = models.CharField(max_length=100, blank=True, default="")
    bore_min = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    bore_max = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    bore_mid = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    tube_pitch = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )

    synced_to_mpp = models.BooleanField(default=False)
    imported_from_mpp = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table metadata for Attivita."""

        db_table = "attivita"
        constraints = [
            UniqueConstraint(
                fields=["commessa", "item", "activity"],
                name="uq_attivita_commessa_item_activity",
            )
        ]

    def __str__(self) -> str:
        """Return a human-readable identifier for this activity.

        Returns:
            str: Commessa, item, and activity separated by underscores.
        """
        return self.activity_id or build_activity_id(
            self.commessa, self.item, self.activity
        )

    def save(self, *args, **kwargs):
        """Keep canonical identifiers aligned with commessa/item/activity fields."""
        self.job_id = build_job_id(self.commessa)
        self.activity_id = build_activity_id(self.commessa, self.item, self.activity)
        super().save(*args, **kwargs)


class Stabilimento(models.Model):
    """A production plant/facility available for scheduling.

    Used as a reference list in the settings page so that planners can
    assign activities to a named, colour-coded stabilimento.
    """

    nome = models.CharField(max_length=200, unique=True)
    colore = models.CharField(max_length=7, default="#6366f1")

    class Meta:
        """Table metadata for Stabilimento."""

        db_table = "stabilimento"
        ordering = ["nome"]

    def __str__(self) -> str:
        """Return the stabilimento name.

        Returns:
            str: The ``nome`` field value.
        """
        return self.nome


class Responsabile(models.Model):
    """A person responsible for one or more production activities.

    Maintained as a reference list in the settings page.
    """

    nome = models.CharField(max_length=200, unique=True)

    class Meta:
        """Table metadata for Responsabile."""

        db_table = "responsabile"
        ordering = ["nome"]

    def __str__(self) -> str:
        """Return the responsabile name.

        Returns:
            str: The ``nome`` field value.
        """
        return self.nome


class UserProfile(models.Model):
    """Extended profile data for a Django auth user."""

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="profile",
    )
    stabilimento = models.ForeignKey(
        Stabilimento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="utenti",
    )

    class Meta:
        """Table metadata for UserProfile."""

        db_table = "user_profile"

    def __str__(self) -> str:
        """Return a readable profile identifier."""
        stab = self.stabilimento.nome if self.stabilimento_id else "—"
        return f"{self.user.username} ({stab})"


class RisorsaAttivita(models.Model):
    """A single inventory resource assigned to an activity.

    Each row links one physical resource (identified by ``risorsa_id`` and
    ``tipo``) to an :class:`Attivita` for the duration of that activity's
    scheduled date range.
    """

    TIPO_CHOICES = [
        ("testa", "Testa"),
        ("mandrino", "Mandrino"),
        ("tastatore", "Tastatore"),
        ("maschera", "Maschera"),
        ("generatore", "Generatore"),
    ]

    attivita = models.ForeignKey(
        Attivita,
        on_delete=models.CASCADE,
        related_name="risorse",
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    risorsa_id = models.CharField(max_length=50)

    class Meta:
        """Table metadata for RisorsaAttivita."""

        db_table = "risorse_attivita"

    def __str__(self) -> str:
        """Return a human-readable description of this resource assignment.

        Returns:
            str: Type and ID linked to the parent activity.
        """
        return f"{self.tipo} {self.risorsa_id} → {self.attivita}"


class Job(models.Model):
    """A commessa record with commercial metadata.

    One row per commessa. The ``commessa`` field is the natural unique key;
    ``job_id`` is its URL-encoded form used as the primary key.

    Item numbers belong to :class:`Attivita` rows, not to the commessa.
    """

    job_id = models.CharField(max_length=250, primary_key=True)
    commessa = models.CharField(max_length=100, unique=True)
    total_cost = models.FloatField(null=True, blank=True)
    penalty = models.FloatField(null=True, blank=True)
    delivery_date = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        """Table metadata for Job."""

        db_table = "job"

    def __str__(self) -> str:
        return self.job_id

    def save(self, *args, **kwargs):
        """Keep canonical job identifier aligned with the commessa field."""
        self.job_id = build_job_id(self.commessa)
        super().save(*args, **kwargs)


class TestaItem(models.Model):
    """A Testa measurement head resource imported from the MS Project file."""

    uid = models.CharField(max_length=100, primary_key=True)
    model = models.CharField(max_length=200, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "testa_item"

    def __str__(self) -> str:
        return self.uid


class MandrinoItem(models.Model):
    """A Mandrino spindle resource imported from the MS Project file."""

    uid = models.CharField(max_length=100, primary_key=True)
    diameter = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "mandrino_item"

    def __str__(self) -> str:
        return self.uid


class TastatoreItem(models.Model):
    """A Tastatore probe resource imported from the MS Project file."""

    uid = models.CharField(max_length=100, primary_key=True)
    diameter = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "tastatore_item"

    def __str__(self) -> str:
        return self.uid


class MascheraItem(models.Model):
    """A Maschera fixture resource imported from the MS Project file."""

    uid = models.CharField(max_length=100, primary_key=True)
    pitch = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    center_distance = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "maschera_item"

    def __str__(self) -> str:
        return self.uid


class GeneratoreItem(models.Model):
    """A Generatore power-supply resource imported from the MS Project file."""

    uid = models.CharField(max_length=100, primary_key=True)
    model = models.CharField(max_length=200, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "generatore_item"

    def __str__(self) -> str:
        return self.uid


class AppSettings(models.Model):
    """Application-wide configurable settings (singleton row, pk=1).

    Access via ``AppSettings.load()`` — never instantiate directly.
    """

    penale_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5,
        help_text="Percentuale massima di penale applicabile.",
    )
    solver_time_limit = models.FloatField(
        default=10.0,
        help_text="Timeout massimo del solver di ottimizzazione (secondi).",
    )
    mpp_path = models.CharField(
        max_length=500,
        default="dataset.mpp",
        help_text="Path file MS Project sorgente (.mpp).",
    )
    xlsx_path = models.CharField(
        max_length=500,
        default="jobs.xlsx",
        help_text="Path file Excel sorgente (.xlsx).",
    )
    server_dir = models.CharField(
        max_length=500,
        default="",
        blank=True,
        help_text="Directory server per dataset condiviso (opzionale).",
    )
    heartbeat_interval_seconds = models.PositiveIntegerField(
        default=30,
        help_text="Intervallo heartbeat client in secondi.",
    )
    heartbeat_timeout_seconds = models.PositiveIntegerField(
        default=90,
        help_text="Timeout heartbeat sessione in secondi.",
    )
    msp_dispatch_retry_interval_seconds = models.FloatField(
        default=0.5,
        help_text="Intervallo retry COM MS Project (secondi).",
    )
    msp_dispatch_retry_max_wait_seconds = models.FloatField(
        default=5.0,
        help_text="Attesa massima totale retry COM MS Project (secondi).",
    )
    gemini_model = models.CharField(
        max_length=120,
        default="gemini-2.5-flash",
        help_text="Modello Gemini usato per analisi WPS.",
    )
    gemini_thinking_level = models.CharField(
        max_length=20,
        default="low",
        help_text="Livello thinking Gemini: none/low/medium/high.",
    )
    gemini_thinking_budget = models.PositiveIntegerField(
        default=512,
        help_text="Budget thinking Gemini.",
    )
    auto_import_on_startup = models.BooleanField(
        default=True,
        help_text="Abilita configurazione import automatica all'avvio app.",
    )

    class Meta:
        """Table metadata for AppSettings."""

        db_table = "app_settings"

    def save(self, *args, **kwargs):
        """Force singleton: always write to pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "AppSettings":
        """Return the singleton settings row, creating it on first access.

        Returns:
            AppSettings: The singleton row.
        """
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "penale_max": Decimal("5"),
                "solver_time_limit": 10.0,
                "mpp_path": "dataset.mpp",
                "xlsx_path": "jobs.xlsx",
                "server_dir": "",
                "heartbeat_interval_seconds": 30,
                "heartbeat_timeout_seconds": 90,
                "msp_dispatch_retry_interval_seconds": 0.5,
                "msp_dispatch_retry_max_wait_seconds": 5.0,
                "gemini_model": "gemini-2.5-flash",
                "gemini_thinking_level": "low",
                "gemini_thinking_budget": 512,
                "auto_import_on_startup": True,
            },
        )
        return obj

    def __str__(self) -> str:
        return (
            f"AppSettings(penale_max={self.penale_max}, "
            f"solver_time_limit={self.solver_time_limit}, "
            f"mpp_path={self.mpp_path}, "
            f"xlsx_path={self.xlsx_path}, "
            f"heartbeat={self.heartbeat_interval_seconds}/{self.heartbeat_timeout_seconds}, "
            f"gemini_model={self.gemini_model})"
        )


class MppSyncQueue(models.Model):
    """A pending change to be written back to the MS Project file.

    Each entry represents one atomic operation (add task, add resource, or
    delete resource) that was performed via the web app and has not yet been
    applied to the ``.mpp`` source file.  Entries are processed in creation
    order by the write-back service when the last user logs out.
    """

    OPERATION_CHOICES = [
        ("add_task", "Add Task"),
        ("update_task", "Update Task"),
        ("delete_task", "Delete Task"),
        ("add_resource", "Add Resource"),
        ("update_resource", "Update Resource"),
        ("delete_resource", "Delete Resource"),
        ("add_job", "Add Job"),
        ("update_job", "Update Job"),
        ("delete_job", "Delete Job"),
    ]

    operation = models.CharField(max_length=30, choices=OPERATION_CHOICES)
    payload_json = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table metadata for MppSyncQueue."""

        db_table = "mpp_sync_queue"
        ordering = ["created_at"]

    def __str__(self) -> str:
        """Return a short description of this queue entry.

        Returns:
            str: Operation type and creation timestamp.
        """
        return f"{self.operation} @ {self.created_at}"


class ConfigurazioneTesta(models.Model):
    """Per-testa-model configuration: resource minimums and compatible generators.

    Persistent data — not cleared by reset_app_dataset().
    """

    modello_testa = models.CharField(max_length=100, unique=True)
    min_mandrini = models.PositiveIntegerField(default=1)
    min_tastatori = models.PositiveIntegerField(default=1)
    min_maschere = models.PositiveIntegerField(default=1)
    min_generatori = models.PositiveIntegerField(default=1)

    class Meta:
        """Table metadata for ConfigurazioneTesta."""

        db_table = "configurazione_testa"
        ordering = ["modello_testa"]

    def __str__(self) -> str:
        return self.modello_testa


class GeneratoreCompatibile(models.Model):
    """A generator model that is compatible with a specific testa model."""

    configurazione = models.ForeignKey(
        ConfigurazioneTesta,
        on_delete=models.CASCADE,
        related_name="generatori_compatibili",
    )
    modello_generatore = models.CharField(max_length=100)

    class Meta:
        """Table metadata for GeneratoreCompatibile."""

        db_table = "generatore_compatibile"
        constraints = [
            UniqueConstraint(
                fields=["configurazione", "modello_generatore"],
                name="uq_gen_per_configurazione",
            )
        ]

    def __str__(self) -> str:
        return f"{self.modello_generatore} → {self.configurazione.modello_testa}"


class ProblemaAttivita(models.Model):
    """An operational problem flagged for a specific activity.

    Problems are detected automatically when an activity is saved through the
    wizard.  Two kinds exist:

    * ``spostamento`` — a selected resource belongs to a different plant and
      must be physically transferred before the activity can start.
    * ``acquisto`` — no resource of a required category was selected; one must
      be sourced (purchased, rented, or borrowed).

    Each problem starts unresolved (``risolto=False``).  A planner can mark
    it resolved via the UI, optionally adding free-text notes that are kept
    permanently for audit purposes.
    """

    TIPO_SPOSTAMENTO = "spostamento"
    TIPO_ACQUISTO = "acquisto"

    TIPO_CHOICES = [
        (TIPO_SPOSTAMENTO, "Spostamento richiesto"),
        (TIPO_ACQUISTO, "Risorsa da recuperare"),
    ]

    attivita = models.ForeignKey(
        Attivita,
        on_delete=models.CASCADE,
        related_name="problemi",
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    risorsa_tipo = models.CharField(max_length=20)
    risorsa_uid = models.CharField(max_length=100, blank=True, default="")
    stabilimento_origine = models.CharField(max_length=200, blank=True, default="")
    descrizione = models.TextField()
    risolto = models.BooleanField(default=False)
    note_risoluzione = models.TextField(blank=True, default="")
    data_risoluzione = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table metadata for ProblemaAttivita."""

        db_table = "problema_attivita"
        ordering = ["created_at"]

    def __str__(self) -> str:
        """Return a short description of this problem.

        Returns:
            str: Problem type, resource type, and parent activity identifier.
        """
        return f"[{self.tipo}] {self.risorsa_tipo} → {self.attivita}"


class RisoluzioneProblema(models.Model):
    """Persistent resolved-problem note that survives dataset resets.

    Unlike :class:`ProblemaAttivita`, this record is NOT linked to an
    :class:`Attivita` foreign-key, so it is not cascade-deleted when the
    activity table is wiped during a re-import cycle.  After each import
    the resolution notes are matched back to the newly created
    :class:`ProblemaAttivita` rows by activity identity
    (commessa + item + activity) and problem signature
    (tipo + risorsa_tipo + risorsa_uid).
    """

    commessa = models.CharField(max_length=100)
    item = models.IntegerField()
    activity = models.CharField(max_length=200, blank=True, default="")
    tipo = models.CharField(max_length=20)
    risorsa_tipo = models.CharField(max_length=20)
    risorsa_uid = models.CharField(max_length=100, blank=True, default="")
    note_risoluzione = models.TextField(blank=True, default="")
    data_risoluzione = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Table metadata for RisoluzioneProblema."""

        db_table = "risoluzione_problema"
        constraints = [
            UniqueConstraint(
                fields=[
                    "commessa",
                    "item",
                    "activity",
                    "tipo",
                    "risorsa_tipo",
                    "risorsa_uid",
                ],
                name="uq_risoluzione_problema_identity",
            )
        ]

    def __str__(self) -> str:
        """Return a short description.

        Returns:
            str: Activity identity + problem type.
        """
        return f"{self.commessa}/{self.item}/{self.activity} [{self.tipo}] {self.risorsa_tipo}"
