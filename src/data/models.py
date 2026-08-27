from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Union
from datetime import date


class Task(BaseModel):
    """Model for creating a new task."""

    name: str = Field(..., description="MS Project task name (no format restriction)")
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
    modello_testa: Optional[str] = Field(None, description="Text3 — testa model name")
    bore_min: Optional[float] = Field(
        None, description="Number1 — minimum bore diameter"
    )
    bore_mid: Optional[float] = Field(None, description="Number2 — mean bore diameter")
    bore_max: Optional[float] = Field(
        None, description="Number3 — maximum bore diameter"
    )
    tube_pitch: Optional[float] = Field(
        None, description="Number4 — tube pitch (distanza interasse)"
    )

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
    in_manutenzione: bool = Field(
        False, description="Flag1 — resource is in maintenance"
    )

    @field_validator("measure")
    @classmethod
    def validate_measure(cls, v: Union[int, float, str]) -> Union[int, float, str]:
        if not isinstance(v, (int, float, str)):
            raise ValueError(
                f"'measure' must be int, float or str, got {type(v).__name__}"
            )
        return v
