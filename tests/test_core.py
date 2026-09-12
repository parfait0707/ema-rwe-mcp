import csv
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ema_rwe.config import Settings
from ema_rwe.domain import Document, Extraction, RWEError
from ema_rwe.ema import BASE, is_non_interventional, parse_date, parse_documents, select_protocol
from ema_rwe.pdf import Page, extract_pages, sections, validate_evidence
from ema_rwe.service import Service
from ema_rwe.storage import Repository, import_csv


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
        "CSV has no Data source type column; detail-page enrichment is required."
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
    inbox = Path(initial["import_directory"])
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
