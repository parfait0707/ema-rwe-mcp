import csv
import io

import httpx
import pymupdf
import pytest

from ema_rwe.config import Settings
from ema_rwe.http import EMAClient
from ema_rwe.service import Service


@pytest.fixture(autouse=True)
def no_user_dictionaries(tmp_path_factory, monkeypatch):
    """Tests start from the shipped default (no concept dictionary), whatever lies in data/dictionaries/."""
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(tmp_path_factory.mktemp("dictionaries")))


def labelled(key, value):
    return f"<dl><dt>{key}</dt><dd>{value}</dd></dl>"


def document_card(version="2.0", path="protocol.pdf", published="01/01/2025"):
    return f"""<div class="darwin-study__field-darwin-protocol-file-upd">
    <div class="bcl-file"><p class="file-title">Protocol_V{version}_2024-12-01.pdf</p>
    <div class="file-metadata">First published: {published}</div>
    <a href="/system/files/{path}">View document</a></div></div>"""


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "http",
        interval=0,
        llm_base_url="",
        llm_model="",
        llm_api_key="",
    )


@pytest.fixture
def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (50, 50),
            "8.1 Study design\nA new-user active comparator cohort study of adults.\n"
            "8.2 Data sources\nThe study will use Example Primary Care Database (EPCD).\n"
            "8.3 Disease definitions\nDiabetes is defined by two ICD-10 E11 diagnoses in 365 days.\n"
            "8.4 Analysis\nWe will use propensity score weighting and Cox regression.",
        )
        return doc.tobytes()


@pytest.fixture
def website(pdf_bytes):
    admin = labelled("Study ID", "123") + labelled("Official title and acronym", "Opioid safety")
    admin += labelled("DARWIN EU® study", "Yes") + labelled("Study status", "Finalised")
    admin += labelled("Study description", "Safety in older adults")
    admin += labelled("Study countries", '<div class="field__item">France</div>')
    admin += labelled("Contact email", "private@example.org")
    for tab in ("methodological-aspects", "data-management", "study-documents"):
        admin += f'<a href="/node/99/{tab}">{tab}</a>'
    return {
        "/robots.txt": "User-agent: *\nDisallow: /search/",
        "/study/123": admin,
        "/node/99/methodological-aspects": labelled("Study type", "Non-interventional study"),
        "/node/99/data-management": labelled("Data sources (types)", "Electronic healthcare records (EHR)"),
        "/node/99/study-documents": document_card(),
        "/system/files/protocol.pdf": pdf_bytes,
    }


@pytest.fixture
async def service(settings, website):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        content = website.get(request.url.path)
        return httpx.Response(200 if content is not None else 404, content=content or "")

    client = EMAClient(settings, transport=httpx.MockTransport(handler))
    service = Service(settings, client=client)
    service.requests = requests
    yield service
    await service.close()


@pytest.fixture
def csv_file(tmp_path):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "Study ID",
            "Official title and acronym",
            "Study type",
            "DARWIN EU® study",
            "Study description",
            "Data sources (types)",
            "Study status",
            "Email",
        ]
    )
    writer.writerows(
        [
            [
                "123",
                "Opioid safety",
                "Non-interventional study",
                "Yes",
                "Active comparator cohort",
                "EHR|Claims",
                "Finalised",
                "private@example.org",
            ],
            ["456", "Diabetes treatment", "Non-interventional", "No", "Drug use", "Registry", "Ongoing", ""],
            ["789", "Randomised opioid trial", "Interventional study", "Yes", "", "", "Ongoing", ""],
            ["890", "Missing type", "", "Yes", "", "", "", ""],
        ]
    )
    path = tmp_path / "export.csv"
    path.write_text(stream.getvalue(), encoding="utf-8-sig")
    return path
