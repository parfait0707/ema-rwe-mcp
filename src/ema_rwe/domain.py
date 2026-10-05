import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write via a sibling temporary file and rename, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
    try:
        temporary.write_bytes(data.encode() if isinstance(data, str) else data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RWEError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)

    def as_dict(self):
        return {"error": {"code": self.code, "message": self.message}}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Study(Model):
    study_id: str = Field(pattern=r"^\d{1,20}$")
    title: str
    eupas_number: str | None = None
    description: str = ""
    study_type: str = ""
    study_designs: list[str] = Field(default_factory=list)
    status: str = ""
    darwin_eu: bool | None = None
    countries: list[str] = Field(default_factory=list)
    data_source_types: list[str] = Field(default_factory=list)
    data_source_types_source: str | None = None
    data_source_types_checked_at: str | None = None
    catalogue_data_sources: list[str] = Field(default_factory=list)
    # Role-specific catalogue text from the Studies export; empty when the snapshot lacks the column.
    conditions: list[str] = Field(default_factory=list)
    outcomes: str = ""
    exposures: list[str] = Field(default_factory=list)
    objective: str = ""
    # Whether the Studies export lists a protocol file or URL (None: unknown). A study without one rarely
    # has a protocol in Study documents, so ranking puts it after listed ones; it is never removed.
    protocol_listed: bool | None = None
    source_url: str
    metadata_source: str = ""
    retrieved_at: str = Field(default_factory=now)
    detail_checked_at: str | None = None
    tabs: dict[str, str] = Field(default_factory=dict)


class Document(Model):
    title: str
    document_url: str
    kind: Literal["updated", "initial"]
    version: str | None = None
    document_date: str | None = None
    published_date: str | None = None


def with_notes(missing: list[str], notes: list[str], limit: int = 30) -> list[str]:
    """missing_information plus notes within the schema limit: the notes are kept and any overflow is
    counted in a final entry, never dropped silently."""
    notes = [n for n in notes if n not in missing]
    combined = [*missing, *notes]
    if len(combined) <= limit:
        return combined
    kept = [*missing[: max(limit - 1 - len(notes), 0)], *notes][: limit - 1]
    return [
        *kept,
        f"{len(combined) - len(kept)} further missing-information entries omitted (limit {limit}).",
    ]


def quote_length(quote: str) -> int:
    """Length of a quote as evidence validation compares it: whitespace runs collapsed, soft hyphens removed."""
    return len(" ".join(quote.replace("\u00ad", "").split()))


class Evidence(Model):
    page: int = Field(ge=1)
    section: str | None = None
    quote: str = Field(min_length=8, max_length=1000)

    @field_validator("quote")
    @classmethod
    def quote_has_text(cls, quote: str) -> str:
        # A blank quote normalizes to '' and would be found on every page.
        if quote_length(quote) < 8:
            raise ValueError("quote needs at least 8 characters once whitespace is collapsed")
        return quote


class Fact(Model):
    value: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class DataSource(Fact):
    usage: Literal["used", "planned", "candidate", "unclear"]


SourceType = Literal["claims", "registry", "ehr", "drug_dispensing_prescription", "other"]
SourceRole = Literal["cohort", "outcome", "exposure", "covariate", "other", "unclear"]


class SourceAssessment(DataSource):
    """One source component's role in a definition, backed by protocol passages."""

    types: list[SourceType] = Field(min_length=1, max_length=5)
    role: SourceRole
    basis: Literal["explicit", "inferred"]
    definition: str = Field(min_length=1, max_length=4000)
    requires_linkage: bool


class SourcePreference(Model):
    types: list[SourceType] = Field(min_length=1, max_length=5)
    role: Literal["any", "cohort", "outcome", "exposure", "covariate", "other"] = "any"
    mode: Literal["prefer", "only"] = "prefer"


class DesignSchema(Model):
    """The study-design figure: where it is, and the time-window statements the text gives for it."""

    figure_pages: list[int] = Field(default_factory=list, max_length=10)
    time_windows: list[Fact] = Field(default_factory=list, max_length=20)


class Cohort(Model):
    """How the analysis cohort is built: eligibility, index date, baseline and follow-up rules."""

    inclusion_criteria: list[Fact] = Field(default_factory=list, max_length=40)
    exclusion_criteria: list[Fact] = Field(default_factory=list, max_length=40)
    index_date: Fact | None = None
    baseline_period: Fact | None = None
    follow_up: Fact | None = None
    design_schema: DesignSchema | None = None


class Extraction(Model):
    schema_version: Literal["0.3"] = "0.3"
    study_design: Fact | None = None
    cohort: Cohort | None = None
    population: Fact | None = None
    exposure: Fact | None = None
    comparator: Fact | None = None
    outcomes: list[Fact] = Field(default_factory=list, max_length=40)
    data_sources: list[DataSource] = Field(default_factory=list, max_length=60)
    source_assessments: list[SourceAssessment] = Field(default_factory=list, max_length=120)
    disease_definitions: list[Fact] = Field(default_factory=list, max_length=60)
    statistical_analysis: Fact | None = None
    key_notes: list[Fact] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=30)


class ProtocolAnswer(Model):
    answers: list[Fact] = Field(default_factory=list, max_length=30)
    source_assessments: list[SourceAssessment] = Field(default_factory=list, max_length=120)
    missing_information: list[str] = Field(default_factory=list, max_length=30)


class AnalogousTerm(Model):
    """A clinically analogous concept searched only on request or as the zero-hit fallback, never as the
    requested concept itself: broader category, sibling disease, or associated condition/complication."""

    term: str = Field(min_length=1, max_length=150, pattern=r"^[\x20-\x7e]+$")
    relation: Literal["broader", "sibling", "associated"]


class CodeCandidate(Model):
    """A retrieval hint, never evidence that a study used this code."""

    system: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-/]*$")
    label: str | None = Field(default=None, max_length=300)
    vocabulary_version: str | None = Field(default=None, max_length=80)
    relation: Literal["candidate", "related", "broader", "narrower", "unspecified_subtype"] = "candidate"
    source_url: str | None = Field(default=None, max_length=1000)
    origin: Literal["caller", "curated", "llm", "local_dictionary", "official_dictionary"] = "caller"
    verification: Literal["unverified", "source_checked"] = "unverified"

    @model_validator(mode="after")
    def validate_known_system(self):
        system = re.sub(r"[^a-z0-9]", "", self.system.casefold())
        if system == "atc":
            self.code = self.code.upper()
            if not re.fullmatch(r"[ABCDGHJLMNPRSV](?:\d{2}(?:[A-Z](?:[A-Z](?:\d{2})?)?)?)?", self.code):
                raise ValueError("ATC requires a valid level 1..5 code format, e.g. B01AF02")
        elif system.startswith("icd10"):
            self.code = self.code.upper()
            if not re.fullmatch(r"[A-Z]\d[0-9A-Z](?:\.?[A-Z0-9]{1,4})?", self.code):
                raise ValueError("ICD-10 requires a category or subcategory, e.g. J84.9 or J849")
        return self
