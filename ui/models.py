"""Django ORM models for production planning activities and resource assignments."""

from django.db import models
from django.db.models import UniqueConstraint

from ui.services.identifiers import build_activity_id, build_job_id
from ui.services.resource_types import TIPI, TIPO_LABELS


class Attivita(models.Model):
    """An activity planned for production scheduling.

    This is the single source of truth for all activity data.  Records are
    created through the multi-step add-activity wizard and edited from the
    web app and the tablet bridge.

    The commessa is a real link (``job``); ``commessa`` is the same name kept
    as text for display and filtering and is realigned on every save.
    Precedences are the ``predecessori`` relation: renaming an activity never
    breaks the links that point at it.  Resource assignments are stored in
    the related :class:`RisorsaAttivita` model.
    """

    commessa = models.CharField(max_length=100)
    item = models.CharField(max_length=50, default="1")
    activity = models.CharField(max_length=200, blank=True, default="")
    job = models.ForeignKey(
        "Job",
        on_delete=models.PROTECT,
        db_column="job_id",
        related_name="attivita",
    )
    activity_id = models.CharField(max_length=500, unique=True, blank=True, default="")

    duration_min = models.IntegerField(null=True, blank=True)
    data_inizio = models.DateField(null=True, blank=True)
    data_fine = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_finish = models.DateField(null=True, blank=True)
    baseline_start = models.DateField(null=True, blank=True)
    baseline_finish = models.DateField(null=True, blank=True)
    predecessori = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="successori",
        blank=True,
    )

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

    # TP60 Custom (Sporgenza Tubo-Piastra): resources are still selected the
    # usual way, but deselecting consumables (mandrini, tastatori, maschere)
    # must not raise an "acquisto" problem — such jobs may not need them.
    tp60_custom = models.BooleanField(default=False)

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
        """Keep the Job link and the canonical activity id aligned with the fields.

        The commessa text is the input, the Job row is the truth: a commessa
        that has no Job yet gets one, so an activity is never orphaned.
        """
        expected = build_job_id(self.commessa)
        if self._state.adding or self.job_id != expected:
            self.job, _ = Job.objects.get_or_create(
                commessa=self.commessa, defaults={"job_id": expected}
            )
        self.activity_id = build_activity_id(self.commessa, self.item, self.activity)
        super().save(*args, **kwargs)

    def predecessor_ids(self) -> list[str]:
        """Canonical ids of the predecessors (served from the prefetch when present)."""
        return [p.activity_id for p in self.predecessori.all()]

    def predecessors_text(self) -> str:
        """Predecessor ids joined by ``", "``: the wire format of the clients."""
        return ", ".join(self.predecessor_ids())


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


class TipoAttivita(models.Model):
    """A named activity type selectable when creating or editing activities.

    Maintained as a reference list in the settings page.  New activities must
    use one of these names; legacy imported activities may retain values not
    present in this catalog until edited.
    """

    nome = models.CharField(max_length=200, unique=True)

    class Meta:
        """Table metadata for TipoAttivita."""

        db_table = "tipo_attivita"
        ordering = ["nome"]

    def __str__(self) -> str:
        """Return the activity type name.

        Returns:
            str: The ``nome`` field value.
        """
        return self.nome


class UserProfile(models.Model):
    """Extended profile data for a Django auth user.

    ``ruolo`` implements the three access layers of the application:

    * ``admin`` — full write access plus the Django admin panel, where user
      permissions are assigned.
    * ``scrittura`` — can create and edit commesse, attività, inventory and
      settings, but cannot reach the admin panel.
    * ``lettura`` — read-only: every mutating endpoint is refused.

    The role is kept in sync with ``User.is_staff`` / ``User.is_superuser``
    by :func:`ui.permissions.sync_user_flags`, so promoting a user to
    ``admin`` is enough to grant admin-panel access.
    """

    RUOLO_LETTURA = "lettura"
    RUOLO_SCRITTURA = "scrittura"
    RUOLO_ADMIN = "admin"

    RUOLO_CHOICES = [
        (RUOLO_LETTURA, "Lettura"),
        (RUOLO_SCRITTURA, "Scrittura"),
        (RUOLO_ADMIN, "Admin"),
    ]

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
    ruolo = models.CharField(
        max_length=20,
        choices=RUOLO_CHOICES,
        default=RUOLO_LETTURA,
    )

    class Meta:
        """Table metadata for UserProfile."""

        db_table = "user_profile"

    @property
    def is_admin(self) -> bool:
        """Return ``True`` when the profile grants admin-panel access."""
        return self.ruolo == self.RUOLO_ADMIN

    @property
    def can_write(self) -> bool:
        """Return ``True`` when the profile grants write access."""
        return self.ruolo in {self.RUOLO_SCRITTURA, self.RUOLO_ADMIN}

    def __str__(self) -> str:
        """Return a readable profile identifier."""
        stab = self.stabilimento.nome if self.stabilimento_id else "—"
        return f"{self.user.username} ({stab}, {self.get_ruolo_display()})"


class RisorsaAttivita(models.Model):
    """A single inventory resource assigned to an activity.

    Each row links one physical resource (identified by ``risorsa_id`` and
    ``tipo``) to an :class:`Attivita` for the duration of that activity's
    scheduled date range.
    """

    TIPO_CHOICES = [(tipo, TIPO_LABELS[tipo]) for tipo in TIPI]

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
        """Keep canonical job identifier aligned with the commessa field.

        The commessa is the primary key in disguise: an existing Job cannot
        be renamed, its activities point at it.
        """
        expected = build_job_id(self.commessa)
        if not self._state.adding and self.job_id and self.job_id != expected:
            raise ValueError(
                "La commessa di un Job esistente non puo' essere rinominata."
            )
        self.job_id = expected
        super().save(*args, **kwargs)


class TestaItem(models.Model):
    """A Testa measurement head resource in the inventory."""

    uid = models.CharField(max_length=100, primary_key=True)
    model = models.CharField(max_length=200, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    in_manutenzione = models.BooleanField(default=False)

    class Meta:
        db_table = "testa_item"

    def __str__(self) -> str:
        return self.uid


class MandrinoItem(models.Model):
    """A Mandrino spindle resource in the inventory."""

    uid = models.CharField(max_length=100, primary_key=True)
    diameter = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "mandrino_item"

    def __str__(self) -> str:
        return self.uid


class TastatoreItem(models.Model):
    """A Tastatore probe resource in the inventory."""

    uid = models.CharField(max_length=100, primary_key=True)
    diameter = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "tastatore_item"

    def __str__(self) -> str:
        return self.uid


class MascheraItem(models.Model):
    """A Maschera fixture resource in the inventory."""

    uid = models.CharField(max_length=100, primary_key=True)
    pitch = models.CharField(max_length=50, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    center_distance = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "maschera_item"

    def __str__(self) -> str:
        return self.uid


class GeneratoreItem(models.Model):
    """A Generatore power-supply resource in the inventory."""

    uid = models.CharField(max_length=100, primary_key=True)
    model = models.CharField(max_length=200, null=True, blank=True)
    plant = models.CharField(max_length=200, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    in_manutenzione = models.BooleanField(default=False)

    class Meta:
        db_table = "generatore_item"

    def __str__(self) -> str:
        return self.uid


class ConfigurazioneTesta(models.Model):
    """Per-testa-model configuration: resource minimums and compatible generators."""

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
    TIPO_SOVRAPPOSIZIONE = "sovrapposizione"
    TIPO_MANUTENZIONE = "manutenzione"
    TIPO_RITARDO = "ritardo"
    TIPO_SOSTITUZIONE = "sostituzione"

    TIPO_CHOICES = [
        (TIPO_SPOSTAMENTO, "Spostamento richiesto"),
        (TIPO_ACQUISTO, "Risorsa da recuperare"),
        (TIPO_SOVRAPPOSIZIONE, "Sovrapposizione risorse"),
        (TIPO_MANUTENZIONE, "Sostituzione per manutenzione"),
        (TIPO_RITARDO, "Attivita in ritardo"),
        (TIPO_SOSTITUZIONE, "Sostituzione da tablet"),
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
    bloccata_fino = models.DateField(null=True, blank=True)
    riferimento_attivita = models.CharField(max_length=500, blank=True, default="")
    descrizione = models.TextField()
    proposta_json = models.JSONField(null=True, blank=True, default=None)
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


class OperationLog(models.Model):
    """Immutable audit trail entry for a write operation or a system event.

    Populated by :func:`ui.services.operation_log.record_operation` from
    every human-facing mutating endpoint (web and tablet) and from the
    automatic problem-detection / auto-resolution / auto-reassignment
    services.  Never carries a foreign key to :class:`Attivita` or any
    inventory row: the log must outlive deleted activities and resources,
    so identity is stored as plain text in ``riferimento``
    (commessa/item/activity or resource uid).
    """

    SOURCE_WEB = "web"
    SOURCE_TABLET = "tablet"
    SOURCE_SISTEMA = "sistema"

    SOURCE_CHOICES = [
        (SOURCE_WEB, "Web"),
        (SOURCE_TABLET, "Tablet"),
        (SOURCE_SISTEMA, "Sistema"),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operation_logs",
    )
    username = models.CharField(max_length=150, blank=True, default="")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    azione = models.CharField(max_length=50)
    descrizione = models.TextField()
    riferimento = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        """Table metadata for OperationLog."""

        db_table = "operation_log"
        ordering = ["-timestamp", "-id"]

    def __str__(self) -> str:
        """Return a short summary of this log entry."""
        return f"[{self.source}] {self.azione} — {self.username or 'Sistema'}"
