import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


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
    status: str = ""
    darwin_eu: bool | None = None
    countries: list[str] = Field(default_factory=list)
    data_source_types: list[str] = Field(default_factory=list)
    catalogue_data_sources: list[str] = Field(default_factory=list)
    source_url: str
    metadata_source: str = ""
    retrieved_at: str = Field(default_factory=now)
    detail_checked_at: str | None = None
    tabs: dict[str, str] = Field(default_factory=dict)


class Document(Model):
    title: str
    document_url: str
    kind: Literal["updated", "protocol", "initial"]
    version: str | None = None
    document_date: str | None = None
    published_date: str | None = None


class Evidence(Model):
    page: int = Field(ge=1)
    section: str | None = None
    quote: str = Field(min_length=8, max_length=1000)


class Fact(Model):
    value: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class DataSource(Fact):
    usage: Literal["used", "planned", "candidate", "unclear"]


class Extraction(Model):
    schema_version: Literal["0.1"] = "0.1"
    study_design: Fact | None = None
    population: Fact | None = None
    exposure: Fact | None = None
    comparator: Fact | None = None
    outcomes: list[Fact] = Field(default_factory=list, max_length=40)
    data_sources: list[DataSource] = Field(default_factory=list, max_length=60)
    disease_definitions: list[Fact] = Field(default_factory=list, max_length=60)
    statistical_analysis: Fact | None = None
    key_notes: list[Fact] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=30)


class ProtocolAnswer(Model):
    answers: list[Fact] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=30)


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
