"""Django forms for the add-activity multi-step wizard."""

from django import forms


class AttivitaBaseForm(forms.Form):
    """Step 1 — basic job metadata: plant and responsible.

    ``stabilimento`` and ``responsabile`` are free-text choice fields whose
    options are populated dynamically from the imported ``database.db`` via
    :func:`~ui.services.activity_inventory.get_stabilimenti` and
    :func:`~ui.services.activity_inventory.get_responsabili`.
    """

    stabilimento = forms.ChoiceField(
        label="Stabilimento",
        choices=[],
    )
    responsabile = forms.ChoiceField(
        label="Responsabile",
        choices=[],
    )

    def __init__(
        self,
        *args,
        stabilimenti: list[str] | None = None,
        responsabili: list[str] | None = None,
        **kwargs,
    ) -> None:
        """Inject dynamic choice lists for plant and responsible fields.

        Args:
            *args: Positional arguments forwarded to :class:`~django.forms.Form`.
            stabilimenti: List of plant name strings for the dropdown.
            responsabili: List of responsible-person name strings for the dropdown.
            **kwargs: Keyword arguments forwarded to :class:`~django.forms.Form`.
        """
        super().__init__(*args, **kwargs)
        stab_choices = [("", "Seleziona")] + [(s, s) for s in (stabilimenti or [])]
        resp_choices = [("", "Seleziona")] + [(r, r) for r in (responsabili or [])]
        self.fields["stabilimento"].choices = stab_choices
        self.fields["responsabile"].choices = resp_choices


class WpsUploadForm(forms.Form):
    """Step 2 — WPS PDF file upload.

    Validates that the uploaded file is a PDF and that its filename follows
    the expected naming convention:
    ``{modello_testa}_{commessa}[-{item}][_{attivita}].pdf``.
    """

    wps_file = forms.FileField(
        label="File WPS (PDF)",
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )

    def clean_wps_file(self) -> forms.FileField:
        """Validate file extension and filename format.

        Returns:
            The cleaned file object.

        Raises:
            ValidationError: When the file is not a PDF or the filename
                does not contain at least one underscore separator.
        """
        f = self.cleaned_data.get("wps_file")
        if not f:
            return f
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Il file deve essere in formato PDF.")
        stem = f.name[:-4]
        if "_" not in stem:
            raise forms.ValidationError(
                "Nome file non valido. Formato atteso: "
                "modellotesta_commessa[_attivita].pdf"
            )
        return f


class AttivitaWpsConfermaForm(forms.Form):
    """Step 3 — confirm and optionally correct data extracted from the WPS.

    Fields are pre-populated from the filename parser and Gemini analysis.
    The user can override any value before proceeding.
    """

    modello_testa = forms.CharField(
        label="Modello testa",
        max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    commessa = forms.CharField(
        label="Commessa",
        max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    item = forms.IntegerField(
        label="Numero item",
        min_value=1,
        widget=forms.NumberInput(attrs={"min": "1"}),
    )
    attivita = forms.CharField(
        label="Attività",
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )


class AttivitaPianificazioneForm(forms.Form):
    """Step 4 — planned start and end dates for the activity.

    Validates that the end date is not earlier than the start date.
    """

    data_inizio = forms.DateField(
        label="Data inizio pianificata",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    data_fine = forms.DateField(
        label="Data fine pianificata",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self) -> dict:
        """Cross-validate that data_fine is not before data_inizio.

        Returns:
            dict: The cleaned data dictionary.

        Raises:
            ValidationError: When ``data_fine`` precedes ``data_inizio``.
        """
        cleaned = super().clean()
        d_start = cleaned.get("data_inizio")
        d_end = cleaned.get("data_fine")
        if d_start and d_end and d_end < d_start:
            raise forms.ValidationError(
                "La data di fine non può essere precedente alla data di inizio."
            )
        return cleaned
