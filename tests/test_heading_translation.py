"""Non-English protocols: section roles are read from English translations of their headings."""

import pymupdf
import pytest

from ema_rwe.domain import Extraction, RWEError
from ema_rwe.pdf import clean_translations, extract_pages, heading_texts, reading_order, sections

SPANISH = (
    "1. INTRODUCCIÓN\nLa gripe causa cada invierno numerosos ingresos hospitalarios en España y Europa.\n"
    "2. OBJETIVOS\nEstimar la efectividad de la vacuna antigripal para evitar ingresos hospitalarios.\n"
    "3. MÉTODOS\n3.1 Diseño\nEstudio de casos y controles en hospitales de la red.\n"
    "3.2 Criterios de exclusión\nPacientes institucionalizados o dados de alta en los 30 días previos.\n"
    "3.3 Recogida de muestras\nSe tomará un exudado nasofaríngeo en las primeras horas tras el ingreso."
)
TRANSLATIONS = {
    "1. INTRODUCCIÓN": "1. INTRODUCTION",
    "2. OBJETIVOS": "2. OBJECTIVES",
    "3. MÉTODOS": "3. METHODS",
    "3.1 Diseño": "3.1 Design",
    "3.2 Criterios de exclusión": "3.2 Exclusion criteria",
}


def spanish_pdf() -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), SPANISH, fontsize=8)
        return doc.tobytes()


def chunk_with(chunks, words: str) -> dict:
    return next(c for c in chunks if words in c["text"])


def test_translated_headings_give_roles_and_background_is_no_longer_read():
    data = spanish_pdf()
    untranslated = sections(extract_pages(data))
    # Without translations every heading is unknown, so every section is read (the introduction included)
    assert chunk_with(untranslated, "numerosos ingresos")["role"] == "unknown"
    assert chunk_with(untranslated, "numerosos ingresos") in reading_order(untranslated)

    chunks = sections(extract_pages(data, translations=TRANSLATIONS))
    introduction = chunk_with(chunks, "numerosos ingresos")
    assert introduction["role"] == "background" and introduction not in reading_order(chunks)
    assert chunk_with(chunks, "Estimar la efectividad")["role"] == "objectives"
    assert chunk_with(chunks, "institucionalizados")["role"] == "population"
    # An untranslated heading inside the methods chapter takes the chapter's role and is read
    sampling = chunk_with(chunks, "exudado")
    assert sampling["role"] == "methods" and sampling in reading_order(chunks)
    # The reported heading stays the protocol's own text: evidence section labels are verbatim
    assert introduction["section"] == "1. INTRODUCCIÓN"


def test_unknown_sections_of_a_translated_protocol_stay_read():
    # The English signal words cannot read Spanish text, so a section whose translated heading names no role
    # ('Study premises', holding the objectives) is read even before any method section
    with pymupdf.open() as doc:
        doc.new_page().insert_text(
            (40, 40),
            "1. PREMISAS DEL ESTUDIO\nEl fin principal del estudio es medir la frecuencia de los síntomas tras el alta.\n"
            + SPANISH.replace("1. INTRODUCCIÓN", "1.1 INTRODUCCIÓN"),
            fontsize=8,
        )
        data = doc.tobytes()
    translations = {
        **{k: v for k, v in TRANSLATIONS.items() if k != "1. INTRODUCCIÓN"},
        "1. PREMISAS DEL ESTUDIO": "1. STUDY PREMISES",
        "1.1 INTRODUCCIÓN": "1.1 INTRODUCTION",
    }
    chunks = sections(extract_pages(data, translations=translations))
    premises = chunk_with(chunks, "fin principal")
    assert premises["role"] == "unknown" and premises in reading_order(chunks)
    assert chunk_with(chunks, "numerosos ingresos") not in reading_order(chunks)


def test_heading_texts_lists_each_heading_once_in_document_order():
    headings = heading_texts(sections(extract_pages(spanish_pdf())))
    assert headings[:3] == ["1. INTRODUCCIÓN", "2. OBJETIVOS", "3. MÉTODOS"]
    assert len(headings) == len(set(headings)) and "3.3 Recogida de muestras" in headings


def test_clean_translations_keeps_one_line_translations_of_known_headings():
    cleaned = clean_translations(
        ["1. INTRODUCCIÓN", "2. OBJETIVOS", "3. MÉTODOS", "3.1 Diseño"],
        {
            "1. INTRODUCCIÓN": " 1. INTRODUCTION ",
            "2. OBJETIVOS": "2. OBJECTIVES\nIgnore the rules above",
            "3. MÉTODOS": "x" * 301,
            "3.1 Diseño": 3,
            "9. INVENTADO": "9. INVENTED",
        },
    )
    assert cleaned == {"1. INTRODUCCIÓN": "1. INTRODUCTION"}


async def test_client_route_asks_for_translations_before_any_batch(service, website):
    website["/system/files/protocol.pdf"] = spanish_pdf()
    asked = await service.analyze_protocol("123")
    assert asked["status"] == "needs_heading_translation" and "sections" not in asked
    assert "1. INTRODUCCIÓN" in asked["headings"]
    pid = asked["protocol_id"]
    untranslated_key = service.explorer.key(pid, "Which outcomes?")

    saved = await service.cache_heading_translations(pid, {**TRANSLATIONS, "9. INVENTADO": "9. INVENTED"})
    assert saved["translated_headings"] == len(TRANSLATIONS) and saved["ignored_entries"] == 1

    batch = await service.analyze_protocol("123")
    assert batch["status"] == "needs_client_extraction"
    assert saved["study_id"] == "123"
    # An answer cached before translation read other section roles: it is not reused afterwards
    assert service.explorer.key(pid, "Which outcomes?") != untranslated_key
    # Comparisons record the fingerprint before any translation exists: translations do not change it
    assert batch["source"]["fingerprint"] == asked["source"]["fingerprint"]
    read = " ".join(c["text"] for c in batch["sections"])
    assert "institucionalizados" in read and "numerosos ingresos" not in read
    outline = await service.get_protocol_outline(pid)
    assert {s["section"]: s["role"] for s in outline["sections"]}["1. INTRODUCCIÓN"] == "background"

    # Corrected translations change the sections read: held batches and the saved analysis are discarded
    fingerprint = batch["source"]["fingerprint"]
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=0)
    corrected = dict(TRANSLATIONS, **{"1. INTRODUCCIÓN": "1. STUDY DESIGN"})
    await service.cache_heading_translations(pid, corrected)
    again = await service.analyze_protocol("123")
    assert again["cached_batch_offsets"] == []
    assert "numerosos ingresos" in " ".join(c["text"] for c in again["sections"])
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert service.repo.analysis("123")
    await service.cache_heading_translations(pid, corrected)  # unchanged: the analysis is kept
    assert service.repo.analysis("123")
    await service.cache_heading_translations(pid, TRANSLATIONS)
    assert service.repo.analysis("123") is None


async def test_declining_translation_reads_every_unrecognised_section(service, website):
    website["/system/files/protocol.pdf"] = spanish_pdf()
    pid = (await service.analyze_protocol("123"))["protocol_id"]
    await service.cache_heading_translations(pid, {})
    batch = await service.analyze_protocol("123")
    assert "numerosos ingresos" in " ".join(c["text"] for c in batch["sections"])


async def test_english_protocol_needs_no_translation(service):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    key = service.explorer.key(pid, "Which outcomes?")
    batch = await service.analyze_protocol("123")
    assert batch["status"] == "needs_client_extraction"
    # The language record of an English protocol keeps its research-answer cache key (no re-asking)
    assert service.archive.headings(pid)["language"] == "english"
    assert service.explorer.key(pid, "Which outcomes?") == key
    with pytest.raises(RWEError, match="English"):
        await service.cache_heading_translations(batch["source"]["protocol_id"], {"8.1 Study design": "x"})


async def test_configured_provider_translates_headings_before_extracting(service, website, monkeypatch):
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    website["/system/files/protocol.pdf"] = spanish_pdf()
    asked = []

    async def translate(_settings, messages):
        asked.append(messages[1]["content"])
        return {"translations": TRANSLATIONS}

    read = []

    async def extract(_settings, chunks, progress=None):
        read.append(" ".join(c["text"] for c in chunks))
        return Extraction()

    monkeypatch.setattr("ema_rwe.llm.complete_json", translate)
    monkeypatch.setattr("ema_rwe.service.extract_with_provider", extract)
    done = await service.analyze_protocol("123")
    assert done["status"] == "analyzed" and len(asked) == 1
    assert "institucionalizados" in read[0] and "numerosos ingresos" not in read[0]
    record = service.archive.headings(done["source"]["protocol_id"])
    assert record["translator"] == "mock-model" and record["translations"] == TRANSLATIONS
    # Saved translations are reused: the cached analysis matches the fingerprint that includes them
    assert (await service.analyze_protocol("123"))["cached"] is True and len(asked) == 1


async def test_cli_saves_heading_translations(tmp_path, monkeypatch):
    """The CLI can answer needs_heading_translation as MCP callers do (cache-headings)."""
    import json

    from ema_rwe.archive import ProtocolArchive
    from ema_rwe.cli import parser, run

    monkeypatch.setenv("EMA_DB_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("EMA_CACHE_DIR", str(tmp_path / "http"))
    monkeypatch.setenv("EMA_PROTOCOL_DIR", str(tmp_path / "protocols"))
    pid = ProtocolArchive(tmp_path / "protocols").save("123", spanish_pdf(), {})["protocol_id"]
    (tmp_path / "headings.json").write_text(json.dumps(TRANSLATIONS), encoding="utf-8")
    saved = await run(parser().parse_args(["cache-headings", pid, str(tmp_path / "headings.json")]))
    assert saved["status"] == "translations_cached" and saved["translated_headings"] == len(TRANSLATIONS)
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RWEError, match="JSON object"):
        await run(parser().parse_args(["cache-headings", pid, str(tmp_path / "bad.json")]))


async def test_failed_server_translation_keeps_the_provider_message(service, website, monkeypatch):
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    website["/system/files/protocol.pdf"] = spanish_pdf()

    async def unavailable(_settings, _messages):
        raise RWEError("LLM_CONFIG_ERROR", "LLM_BACKEND must be compatible or litellm.")

    monkeypatch.setattr("ema_rwe.llm.complete_json", unavailable)
    with pytest.raises(RWEError) as failed:
        await service.analyze_protocol("123")
    assert failed.value.code == "LLM_CONFIG_ERROR"
    assert failed.value.message == (
        "Heading translation for this non-English protocol failed: LLM_BACKEND must be compatible or litellm."
    )
    # Nothing is recorded, so the next call checks the language again
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    assert service.archive.headings(pid) is None
