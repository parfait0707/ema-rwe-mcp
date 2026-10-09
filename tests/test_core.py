import csv
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ema_rwe.config import Settings
from ema_rwe.domain import Document, Extraction, RWEError
from ema_rwe.ema import BASE, is_non_interventional, parse_date, parse_documents, select_protocol
from ema_rwe.llm import drop_invalid_evidence
from ema_rwe.pdf import Page, extract_pages, sections, validate_evidence
from ema_rwe.selection import SearchFilters
from ema_rwe.service import Service, source_type_from_filename
from ema_rwe.storage import Repository, import_csv


def test_default_db_is_the_committed_checkout_catalogue(monkeypatch):
    monkeypatch.delenv("EMA_DB_PATH", raising=False)
    repo_root = Path(__file__).resolve().parents[1]
    assert Settings.__dataclass_fields__["db_path"].default_factory() == repo_root / "data" / "ema.sqlite3"
    monkeypatch.setenv("EMA_DB_PATH", "elsewhere.sqlite3")
    assert Settings.__dataclass_fields__["db_path"].default_factory() == Path("elsewhere.sqlite3")


def test_default_cache_ttls_are_30_days(monkeypatch, tmp_path):
    monkeypatch.delenv("EMA_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("EMA_CATALOGUE_TTL_SECONDS", raising=False)
    configured = Settings(db_path=tmp_path / "db.sqlite3", cache_dir=tmp_path / "http")
    assert configured.ttl == 2_592_000
    assert configured.catalogue_ttl == 2_592_000


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Non-interventional study", True),
        ("Non–interventional", True),
        ("Interventional study", False),
        ("", False),
        ("Other non-interventional trial", False),
    ],
)
def test_scope(value, expected):
    assert is_non_interventional(value) is expected


def test_import_search_scope_and_privacy(settings, csv_file):
    repo = Repository(settings.db_path)
    result = import_csv(repo, csv_file)
    assert result["imported"] == 2
    assert result["skipped_out_of_scope"] == 2
    assert repo.get("789") is None
    results = repo.search("opioid", 5, True, ["Finalised"])
    assert len(results) == 1
    assert results[0]["data_source_types"] == ["EHR", "Claims"]
    assert results[0]["protocol_data_sources_status"] == "not_analyzed"
    assert results[0]["protocol_data_sources"] == []
    assert "private@example.org" not in json.dumps(results)
    assert repo.search("Diabetes", 5, True, None) == []
    assert len(repo.search("Diabetes", 5, False, None)) == 1
    assert len(repo.search("opioid", 5, True, None, analyzed_only=True)) == 0
    assert (
        settings.db_path.parent / "raw" / f"{result['checksum']}.csv"
    ).read_bytes() == csv_file.read_bytes()
    import_csv(repo, csv_file)
    assert len(repo.search("opioid", 20, False, None)) == 1


def test_import_actual_export_columns_and_safe_clinical_search(settings, tmp_path):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "Title",
            "Study ID",
            "Study type",
            "Study countries",
            "Study description",
            "Non-interventional study design",
            "Non-interventional study design, other",
            "Data source(s) ",
            "Other linked data sources ",
            "Anatomical Therapeutic Chemical (ATC) code",
            "Outcomes",
            "Main contact: First name",
        ]
    )
    writer.writerow(
        [
            'A "quoted" safety study',
            "2468",
            "Non-interventional study",
            "Sweden",
            "Routinely collected data",
            "Cohort",
            "New-user design",
            "National Patient Register; Prescribed Drug Register",
            "Regional claims data\nDeath register",
            "B01AF02",
            "Major bleeding",
            "Private Name",
        ]
    )
    path = tmp_path / "actual-shape.csv"
    path.write_text(stream.getvalue(), encoding="utf-8-sig")

    repo = Repository(settings.db_path)
    result = import_csv(repo, path)

    assert result["imported"] == 1
    assert result["schema_warnings"] == [
        "CSV has no Data source type column; import filtered exports from source_type/ to tag studies."
    ]
    study = repo.get("2468")
    assert study.title == 'A "quoted" safety study'
    assert study.study_designs == ["Cohort", "New-user design"]
    assert study.catalogue_data_sources == [
        "National Patient Register",
        "Prescribed Drug Register",
        "Regional claims data",
        "Death register",
    ]
    assert repo.search("B01AF02", 5, False, None)[0]["study_id"] == "2468"
    assert repo.search("major bleeding", 5, False, None)[0]["study_id"] == "2468"
    assert "Private Name" not in json.dumps(repo.search("bleeding", 5, False, None))


def test_catalogue_download_inbox_status_and_import(settings, csv_file):
    service = Service(settings)
    initial = service.catalogue_status()
    assert initial["status"] == "not_imported"
    assert initial["browser_refresh_recommended"] is True
    assert service.search_studies("opioid")["catalogue_action"] == "browser_export_then_import"
    inbox = Path(initial["study_import_directory"])
    inbox.mkdir(parents=True)
    shutil.copyfile(csv_file, inbox / "export-data.csv")
    result = service.import_catalogue_csv("export-data.csv")
    assert result["imported"] == 2
    assert result["catalogue"]["status"] == "current"
    assert result["catalogue"]["browser_refresh_recommended"] is False
    assert service.search_studies("opioid")["catalogue"]["status"] == "current"
    assert service.search_studies("unmatched")["catalogue_action"] == "refine_queries_or_request_fresh_export"
    with pytest.raises(RWEError, match="basename"):
        service.import_catalogue_csv("../export-data.csv")
    with pytest.raises(RWEError, match="not found"):
        service.import_catalogue_csv("missing.csv")
    with service.repo.connection() as db:
        old = (datetime.now(UTC) - timedelta(seconds=settings.catalogue_ttl + 1)).isoformat()
        db.execute("UPDATE imports SET imported_at=?", (old,))
    assert service.catalogue_status()["status"] == "stale"


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("20260913_claims_export-data.csv", "claims"),
        ("20260913_EHR_export-data.csv", "ehr"),
        ("registry-export.csv", "registry"),
        ("20260913_all_export-data.csv", None),
        ("claims_and_ehr.csv", None),
    ],
)
def test_source_type_is_read_from_filename(filename, expected):
    assert source_type_from_filename(filename) == expected


def test_typed_exports_tag_studies_and_survive_full_reimport(settings, csv_file):
    service = Service(settings)
    studies = service.study_import_dir
    typed = service.source_type_import_dir
    studies.mkdir(parents=True)
    typed.mkdir(parents=True)
    # Filtered exports share the plain schema; drop the column so tags can only come from the file name.
    rows = list(csv.reader(io.StringIO(csv_file.read_text(encoding="utf-8-sig"))))
    column = rows[0].index("Data sources (types)")
    untyped = "\n".join(",".join(f'"{c}"' for i, c in enumerate(r) if i != column) for r in rows)
    (studies / "20260913_all_export-data.csv").write_text(untyped, encoding="utf-8")
    (typed / "20260913_claims_export-data.csv").write_text(untyped, encoding="utf-8")
    (typed / "20260913_ehr_export-data.csv").write_text(
        "\n".join(line for line in untyped.splitlines() if not line.startswith('"456"')), encoding="utf-8"
    )
    (typed / "20260913_mystery_export-data.csv").write_text(untyped, encoding="utf-8")
    with pytest.raises(RWEError, match="claims|ehr|registry"):
        service.import_catalogue_csv("20260913_mystery_export-data.csv")
    (typed / "20260913_mystery_export-data.csv").unlink()
    results = service.import_all()
    assert [r["source_type"] for r in results] == [None, "claims", "ehr"]
    assert results[0]["schema_warnings"][0].startswith("CSV has no Data source type column")
    assert service.repo.get("123").data_source_types == ["claims", "ehr"]
    assert service.repo.get("456").data_source_types == ["claims"]
    assert service.repo.get("123").data_source_types_source.startswith("filtered export 20260913_ehr")
    assert service.catalogue_status()["source_type_imports"] == ["claims", "ehr"]
    # A later full export without the column keeps the tags.
    service.import_catalogue_csv("20260913_all_export-data.csv")
    assert service.repo.get("123").data_source_types == ["claims", "ehr"]
    found = service.search_studies("", darwin_only=False, filters=SearchFilters(data_source_types=["ehr"]))
    assert [r["study_id"] for r in found["results"]] == ["123"]
    assert found["facets"]["data_source_types"] == {"claims": 1, "ehr": 1}
    assert found["unknown_metadata_counts"]["data_source_types"] == 0


@pytest.mark.parametrize(
    "query", ['" OR 1=1 --', "() ***", "AND OR NOT", 'cohort NEAR("x")', "高齢 オピオイド"]
)
def test_fts_escaping(settings, csv_file, query):
    repo = Repository(settings.db_path)
    import_csv(repo, csv_file)
    assert isinstance(repo.search(query, 5, True, None), list)


def test_bad_csv_is_not_silently_imported(settings, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Name,Type\na,b")
    with pytest.raises(RWEError, match="Required CSV columns"):
        import_csv(Repository(settings.db_path), path)


@pytest.mark.parametrize(
    "value,result",
    [
        ("V4_2023_09_19", "2023-09-19"),
        ("20200115", "2020-01-15"),
        ("2020.10.30", "2020-10-30"),
        ("26/08/2026", "2026-08-26"),
        ("2025-99-99", None),
    ],
)
def test_date_parsing(value, result):
    assert parse_date(value) == result


def test_latest_numeric_version_over_reupload_date():
    docs = [
        Document(
            title="V2",
            document_url=BASE + "/v2.pdf",
            kind="updated",
            version="2.0",
            published_date="2026-08-26",
        ),
        Document(
            title="V4",
            document_url=BASE + "/v4.pdf",
            kind="updated",
            version="4.0",
            published_date="2026-04-14",
        ),
        Document(title="V10", document_url=BASE + "/v10.pdf", kind="updated", version="10.0"),
    ]
    assert select_protocol(docs)[0].version == "10.0"
    assert select_protocol(docs, "v2.0")[0].version == "2.0"
    with pytest.raises(RWEError):
        select_protocol(docs, "9")


def test_updated_before_initial_and_no_reports():
    docs = parse_documents("""<div class="darwin-study__field-darwin-protocol-file">
    <div class="bcl-file"><p class="file-title">protocol_V10.pdf</p><a href="/initial.pdf">View</a></div>
    </div><details class="field--name-field-darwin-protocol-file-upd"><div class="bcl-file">
    <p class="file-title">protocol_V2.pdf</p><a href="/updated.pdf">View</a></div></details>
    <div class="bcl-file"><p class="file-title">Protocol final report V30</p><a href="/report.pdf">View</a></div>""")
    assert len(docs) == 2
    assert select_protocol(docs)[0].version == "2"


def sample_analysis():
    return Extraction.model_validate(
        {
            "study_design": {
                "value": "New-user active comparator cohort",
                "evidence": [
                    {
                        "page": 1,
                        "section": "8.1 Study design",
                        "quote": "A new-user active comparator cohort study of adults.",
                    }
                ],
            },
            "data_sources": [
                {
                    "value": "Example Primary Care Database",
                    "usage": "planned",
                    "evidence": [
                        {
                            "page": 1,
                            "section": "8.2 Data sources",
                            "quote": "The study will use Example Primary Care Database (EPCD).",
                        }
                    ],
                }
            ],
            "disease_definitions": [
                {
                    "value": "Two ICD-10 E11 diagnoses within 365 days",
                    "evidence": [
                        {
                            "page": 1,
                            "section": "8.3 Disease definitions",
                            "quote": "Diabetes is defined by two ICD-10 E11 diagnoses in 365 days.",
                        }
                    ],
                }
            ],
        }
    )


def test_native_pdf_and_citations(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    assert len(pages) == 1
    assert any(c["section"] == "8.2 Data sources" for c in sections(pages))
    validate_evidence(sample_analysis(), pages)


@pytest.mark.parametrize("change", ["page", "quote", "section", "source_name"])
def test_fabricated_evidence_rejected(pdf_bytes, change):
    analysis = sample_analysis()
    if change == "source_name":
        analysis.data_sources[0].value = "Made Up Registry"
    else:
        setattr(
            analysis.study_design.evidence[0],
            change,
            {"page": 9, "quote": "This quotation does not exist.", "section": "9.7 Fiction"}[change],
        )
    with pytest.raises(RWEError, match="Quote|Section|Data source"):
        validate_evidence(analysis, extract_pages(pdf_bytes))


@pytest.mark.parametrize("quote", [" " * 8, "a" + " " * 9, "\u00ad" * 8 + "abc"])
def test_quote_must_have_text_once_whitespace_is_collapsed(quote):
    # A blank quote normalizes to '' and would otherwise be found on every page
    raw = sample_analysis().model_dump()
    raw["study_design"]["evidence"][0]["quote"] = quote
    with pytest.raises(ValidationError, match="quote needs at least 8 characters"):
        Extraction.model_validate(raw)
    assert (
        drop_invalid_evidence({"population": {"value": "x", "evidence": [{"page": 1, "quote": quote}]}})[
            "population"
        ]
        is None
    )


def test_non_pdf_rejected():
    with pytest.raises(RWEError, match="not a PDF"):
        extract_pages(b"<html>WAF error</html>")


async def test_e2e_client_extraction_cache_local_search(service):
    first = await service.analyze_protocol("123")
    assert first["status"] == "needs_client_extraction"
    assert first["next_offset"] is None
    saved = await service.cache_protocol_analysis(
        "123", first["source"]["fingerprint"], sample_analysis(), True
    )
    assert saved["status"] == "analyzed"
    before = len(service.requests)
    again = await service.analyze_protocol("123")
    assert again["cached"] is True
    assert len(service.requests) == before
    found = service.search_studies("ICD-10 E11")
    assert found["results"][0]["protocol_data_sources"][0]["value"] == "Example Primary Care Database"
    assert found["results"][0]["data_source_types"] == ["Electronic healthcare records (EHR)"]
    assert found["network_requests"] == 0
    assert len(service.requests) == before
    assert "private@example.org" not in json.dumps(found)


async def test_scope_enforced_before_pdf(service, website):
    website["/node/99/methodological-aspects"] = "<dl><dt>Study type</dt><dd>Interventional study</dd></dl>"
    with pytest.raises(RWEError, match="Only explicitly"):
        await service.analyze_protocol("123")
    assert not any(".pdf" in url for url in service.requests)


async def test_refresh_same_url_changed_bytes_invalidates(service, website, pdf_bytes):
    first = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis(), True)
    website["/system/files/protocol.pdf"] = pdf_bytes + b"\n%updated"
    second = await service.analyze_protocol("123", force_refresh=True)
    assert first["source"]["fingerprint"] != second["source"]["fingerprint"]
    assert service.repo.analysis("123") is None
    with pytest.raises(RWEError, match="changed"):
        await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis(), True)


async def test_coverage_required(service):
    first = await service.analyze_protocol("123")
    with pytest.raises(RWEError, match="Read all"):
        await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis())


def test_real_ema_document_fixture():
    path = Path(__file__).parent / "fixtures" / "ema_protocol_cards.html"
    docs = parse_documents(path.read_text(encoding="utf-8"))
    assert len(docs) == 3
    assert select_protocol(docs)[0].version == "4.0"


def test_numbered_database_list_is_not_a_section_heading():
    chunks = sections(
        [
            Page(
                21,
                "8.4 Data sources\nThis study will use eight databases.\n"
                "1. Example Primary Care Registry, France\n2. Example Secondary Care Registry, Spain\n"
                "6 databases include records from primary care\n8.5 Study size\nNo sample size was calculated.",
            )
        ]
    )
    relevant = [c for c in chunks if c["relevant"]]
    assert "2. Example Secondary Care Registry" in relevant[0]["text"]
    assert relevant[0]["section"] == "8.4 Data sources"


def test_csv_update_to_interventional_removes_old_entry(settings, csv_file):
    repo = Repository(settings.db_path)
    import_csv(repo, csv_file)
    csv_file.write_text(
        csv_file.read_text(encoding="utf-8-sig").replace("Non-interventional study", "Interventional study"),
        encoding="utf-8-sig",
    )
    import_csv(repo, csv_file)
    assert repo.get("123") is None
    assert repo.search("opioid", 5, True, None) == []


def test_bad_record_does_not_partially_import(settings, csv_file):
    repo = Repository(settings.db_path)
    csv_file.write_text(
        csv_file.read_text(encoding="utf-8-sig").replace("456,", "bad-id,"), encoding="utf-8-sig"
    )
    with pytest.raises(RWEError):
        import_csv(repo, csv_file)
    assert repo.get("123") is None


async def test_force_refresh_allows_following_client_batches(service):
    first = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis(), True)
    refreshed = await service.analyze_protocol("123", force_refresh=True)
    assert refreshed["status"] == "needs_client_extraction"
    following = await service.analyze_protocol("123", offset=0)
    assert following["status"] == "needs_client_extraction"


def test_catalogue_status_reports_total_studies_separately_from_export_counts(service):
    from test_source_types import seed

    seed(service.repo, 3)
    status = service.catalogue_status()
    assert status["studies_total"] == service.repo.study_count() == 3
    assert status["latest_import"] is None or "note" in status["latest_import"]


def test_catalogue_freshness_is_judged_per_export(settings, csv_file):
    """A fresh source-type export does not make an old full Studies export current (each kind ages alone)."""
    service = Service(settings)
    service.study_import_dir.mkdir(parents=True)
    service.source_type_import_dir.mkdir(parents=True)
    shutil.copyfile(csv_file, service.study_import_dir / "20260901_all_export-data.csv")
    # A filtered export lists a subset of the studies
    lines = csv_file.read_text(encoding="utf-8-sig").splitlines()
    (service.source_type_import_dir / "20261001_registry_export-data.csv").write_text(
        "\n".join(lines[:2]), encoding="utf-8"
    )
    service.import_catalogue_csv("20260901_all_export-data.csv")
    service.import_catalogue_csv("20261001_registry_export-data.csv")
    status = service.catalogue_status()
    assert status["status"] == "current" and status["missing_source_type_exports"] == ["claims", "ehr"]
    # Exports of different refreshes are flagged, but only an expired export calls for a browser export
    assert status["snapshot_aligned"] is False and status["browser_refresh_recommended"] is False
    assert "snapshot_note" in status
    with service.repo.connection() as db:
        old = (datetime.now(UTC) - timedelta(seconds=settings.catalogue_ttl + 1)).isoformat()
        db.execute("UPDATE imports SET imported_at=? WHERE filename=?", (old, "20260901_all_export-data.csv"))
    status = service.catalogue_status()
    assert status["status"] == "stale" and status["exports"]["studies"]["status"] == "stale"
    assert status["exports"]["registry"]["status"] == "current"


def test_identical_exports_of_different_kinds_each_keep_their_import_record(settings, csv_file):
    """Types overlap, so a typed export may equal another type's or the full export byte for byte."""
    service = Service(settings)
    service.study_import_dir.mkdir(parents=True)
    service.source_type_import_dir.mkdir(parents=True)
    shutil.copyfile(csv_file, service.study_import_dir / "20260901_all_export-data.csv")
    for kind in ("claims", "ehr"):
        shutil.copyfile(csv_file, service.source_type_import_dir / f"20260901_{kind}_export-data.csv")
    service.import_all()
    status = service.catalogue_status()
    assert {k: e["filename"] for k, e in status["exports"].items()} == {
        "studies": "20260901_all_export-data.csv",
        "claims": "20260901_claims_export-data.csv",
        "ehr": "20260901_ehr_export-data.csv",
    }
    assert status["missing_source_type_exports"] == ["registry"] and status["snapshot_aligned"] is True
    # The same kind exported again unchanged replaces that kind's record only
    shutil.copyfile(csv_file, service.source_type_import_dir / "20261001_claims_export-data.csv")
    service.import_catalogue_csv("20261001_claims_export-data.csv")
    status = service.catalogue_status()
    assert status["exports"]["claims"]["filename"] == "20261001_claims_export-data.csv"
    assert len(service.repo.imports()) == 3 and set(status["exports"]) == {"studies", "claims", "ehr"}


async def test_cli_import_csv_tags_a_typed_export_by_its_file_name(tmp_path, monkeypatch, csv_file):
    """The CLI tags like import_catalogue_csv, so catalogue_status never reports an untagged type as imported."""
    from ema_rwe.cli import parser, run

    monkeypatch.setenv("EMA_DB_PATH", str(tmp_path / "db.sqlite3"))
    typed = tmp_path / "20261001_claims_export-data.csv"
    shutil.copyfile(csv_file, typed)
    result = await run(parser().parse_args(["import-csv", str(typed)]))
    assert result["source_type"] == "claims"
    assert "claims" in Repository(tmp_path / "db.sqlite3").get("123").data_source_types
    plain = await run(parser().parse_args(["import-csv", str(csv_file)]))
    assert plain["source_type"] is None


def test_a_typed_export_outside_source_type_is_still_tagged_by_its_name(settings, csv_file):
    service = Service(settings)
    service.study_import_dir.mkdir(parents=True)
    shutil.copyfile(csv_file, service.study_import_dir / "20261001_registry_export-data.csv")
    assert service.import_catalogue_csv("20261001_registry_export-data.csv")["source_type"] == "registry"
    assert "registry" in service.repo.get("123").data_source_types
    assert "registry" in service.catalogue_status()["exports"]
