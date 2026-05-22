import re
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Union
from datetime import date


NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9]+"  # codice_commessa (alfanumerico)
    r"([-_][A-Za-z0-9]+"  # -/_ numero_item    (alfanumerico, opzionale)
    r"([-_][A-Za-z0-9][A-Za-z0-9 _\-]*)?"  # -/_ attività (alfanumerico con spazi/sep, opzionale)
    r")?$"
)


class Task(BaseModel):
    """Model for creating a new task."""

    name: str = Field(
        ...,
        description=(
            "Activity name — accepted formats:\n"
            "  {codice_commessa}                           e.g. AB123\n"
            "  {codice_commessa}_{numero_item}             e.g. AB123_01\n"
            "  {codice_commessa}_{numero_item}_{attività}  e.g. AB123_01_Montaggio\n"
            "  Legacy hyphen format also accepted:         e.g. AB123-01-Montaggio"
        ),
    )
    start: date = Field(..., description="Start date")
    end: date = Field(..., description="End date")
    predecessors: Optional[str] = Field(
        None, description="e.g. '1' or '2FF' or '1,2FS+5d'"
    )
    actual_start: Optional[date] = Field(None, description="Start1 — actual start date")
    actual_end: Optional[date] = Field(None, description="Finish1 — actual end date")
    plant: Optional[str] = Field(None, description="Text1 — plant name")
    owner: Optional[str] = Field(None, description="Text2 — responsible person")
    resources: list[str] = Field(
        default_factory=list, description="Names of assigned resources"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not NAME_PATTERN.match(v):
            raise ValueError(
                f"Invalid name format: '{v}'.\n"
                "Accepted formats:\n"
                "  {codice_commessa}                           e.g. AB123\n"
                "  {codice_commessa}_{numero_item}             e.g. AB123_01\n"
                "  {codice_commessa}_{numero_item}_{attività}  e.g. AB123_01_Montaggio\n"
                "  Legacy hyphen format also accepted:         e.g. AB123-01-Montaggio"
            )
        return v

    @model_validator(mode="after")
    def validate_dates(self) -> "Task":
        if self.end < self.start:
            raise ValueError(
                f"'end' ({self.end}) cannot be earlier than 'start' ({self.start})"
            )
        if self.actual_start and self.actual_end:
            if self.actual_end < self.actual_start:
                raise ValueError(
                    f"'actual_end' ({self.actual_end}) cannot be earlier than 'actual_start' ({self.actual_start})"
                )
        return self

    def fmt(self, d: date) -> str:
        return d.strftime("%d/%m/%Y")


class Resource(BaseModel):
    """Model for creating a new resource."""

    name: str = Field(..., description="Resource name")
    measure: Union[int, float, str] = Field(
        ...,
        description=(
            "Number1 — numeric value for Mandrino/Tastatore/Maschera; "
            "model name (str) for Testa/Generatore"
        ),
    )
    plant: Optional[str] = Field(None, description="Text1 — plant name")
    notes: Optional[str] = Field(None, description="Resource notes")
    category: Optional[str] = Field(None, description="Text2 — category")

    @field_validator("measure")
    @classmethod
    def validate_measure(cls, v: Union[int, float, str]) -> Union[int, float, str]:
        if not isinstance(v, (int, float, str)):
            raise ValueError(
                f"'measure' must be int, float or str, got {type(v).__name__}"
            )
        return v
