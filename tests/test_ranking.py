"""Tiered screening: every column retrieved, blocks AND-ed, candidates ranked without dropping any."""

import pytest
from test_drugs import drug_file  # noqa: F401  (fixture)
from test_screening import study

from ema_rwe.comparison import match_cell
from ema_rwe.domain import AnalogousTerm, RWEError
from ema_rwe.ranking import ScreeningBlock, type_fit


def ids(result):
    return [c["study_id"] for c in result["candidates"]]


async def test_role_ranks_instead_of_filtering(service):
    # Given one study naming the outcome in its Outcomes field and one only in its description
    service.repo.upsert(study("1", title="Cohort A", outcomes="Incidence of pancreatitis"))
    service.repo.upsert(study("2", title="Cohort B", description="Pancreatitis events were reviewed"))
    for i in range(3, 9):  # enough candidates to trigger the listed narrowing dialogue
        service.repo.upsert(study(str(i), title=f"Cohort {i}", description="pancreatitis mentioned"))
    result = await service.compare_protocols("q", ["pancreatitis"], role="outcome")
    # Then both are candidates, the Outcomes-field match first
    assert {"1", "2"} <= set(ids(result))
    assert ids(result)[0] == "1"
    assert result["candidates"][0]["rank_features"]["role_blocks"] == 1
    assert result["candidates"][1]["rank_features"]["role_blocks"] == 0


async def test_category_only_matches_rank_below_specific_ones(service):
    service.repo.upsert(study("1", title="Stroke after triptan use", outcomes="Stroke"))
    service.repo.upsert(
        study("2", title="Cardiovascular events after triptan use", outcomes="Cardiovascular events")
    )
    for i in range(3, 9):
        service.repo.upsert(study(str(i), title=f"Stroke registry {i}"))
    result = await service.compare_protocols(
        "q", ["stroke"], role="outcome", category_terms=["cardiovascular events"]
    )
    order = ids(result)
    assert order[-1] == "2" and "1" in order
    assert result["candidates"][-1]["matched_term_sources"] == {"cardiovascular events": "category"}


async def test_blocks_are_and_ed(service):
    service.repo.upsert(study("1", title="Apixaban and gastrointestinal bleeding"))
    service.repo.upsert(study("2", title="Apixaban adherence"))
    service.repo.upsert(study("3", title="Gastrointestinal bleeding in cirrhosis"))
    blocks = [
        ScreeningBlock(role="exposure", queries=["apixaban"]),
        ScreeningBlock(role="outcome", queries=["gastrointestinal bleeding"]),
    ]
    result = await service.compare_protocols("q", [], blocks=blocks)
    assert result["search"]["total_matches"] == 1
    assert [r["study"]["study_id"] for r in result["rows"]] == ["1"]


async def test_no_study_in_every_block_explains_itself(service):
    service.repo.upsert(study("2", title="Apixaban adherence"))
    blocks = [
        ScreeningBlock(role="exposure", queries=["apixaban"]),
        ScreeningBlock(role="outcome", queries=["gastrointestinal bleeding"]),
    ]
    result = await service.compare_protocols("q", [], blocks=blocks)
    assert result["total_matches"] == 0
    assert result["analogous_fallback"]["status"] == "not_available_for_blocks"


def test_study_type_fit():
    assert type_fit({"data_source_types": ["claims"], "study_designs": ["Cohort"], "title": "x"}) == 1
    assert type_fit({"data_source_types": [], "study_designs": ["Cross-sectional"], "title": "x"}) == -1
    assert type_fit({"data_source_types": [], "study_designs": [], "title": "Physician survey"}) == -1


async def test_invalid_combinations_are_rejected(service):
    block = ScreeningBlock(queries=["x"])
    with pytest.raises(RWEError, match="blocks"):
        await service.compare_protocols("q", [], blocks=[block], synonyms=["y"])
    with pytest.raises(RWEError, match="blocks"):
        await service.compare_protocols("q", ["x"], blocks=[block])
    with pytest.raises(RWEError, match="check_protocols"):
        await service.compare_protocols("q", ["x"], check_protocols=21)


async def test_studies_without_a_protocol_are_ranked_last_not_dropped(service, monkeypatch):
    for i in range(1, 8):
        service.repo.upsert(study(str(i), title=f"Pancreatitis cohort {i}"))

    async def fake_get_protocol(study_id, version="latest", download=True, refresh=False):
        assert download is False  # only the Study documents list, never the PDF
        if study_id in {"1", "2"}:
            raise RWEError("PROTOCOL_NOT_FOUND", "No matching protocol PDF in Study documents.")
        return {}

    monkeypatch.setattr(service, "get_protocol", fake_get_protocol)
    result = await service.compare_protocols("q", ["pancreatitis"], check_protocols=3)
    # network_requests counts real HTTP requests; the stub makes none
    assert result["protocols_checked"] == 3 and result["network_requests"] == 0
    order = ids(result)
    assert len(order) == 7 and set(order[-2:]) == {"1", "2"}
    flags = {c["study_id"]: c.get("protocol_available") for c in result["candidates"]}
    assert flags["1"] is False and flags["3"] is True and flags["7"] is None


async def test_review_regressions(service, monkeypatch):
    for i in range(1, 9):
        service.repo.upsert(study(str(i), title=f"Cholangitis cohort {i}"))
    # blank category terms would match the whole catalogue
    with pytest.raises(RWEError, match="category_terms"):
        await service.compare_protocols("q", ["cholangitis"], category_terms=[" "])
    with pytest.raises(ValueError):
        ScreeningBlock(queries=["x"], category_terms=[" "])
    # the legacy form keeps its 20-query limit as a clean INVALID_INPUT
    with pytest.raises(RWEError, match="1..20"):
        await service.compare_protocols("q", [f"term {i}" for i in range(21)])
    # role belongs in the block
    with pytest.raises(RWEError, match="role"):
        await service.compare_protocols("q", [], role="outcome", blocks=[ScreeningBlock(queries=["x"])])

    # check_protocols on the analogous path is ignored instead of crashing
    async def never(*args, **kwargs):
        raise AssertionError("no protocol check on the analogous path")

    monkeypatch.setattr(service, "get_protocol", never)
    result = await service.compare_protocols(
        "q",
        ["zzqx"],
        match_scope="analogous",
        analogous=[AnalogousTerm(term="cholangitis", relation="sibling")],
        check_protocols=2,
    )
    assert result["protocols_checked"] == 0 and result["total_matches"] == 8


async def test_out_of_scope_studies_rank_last(service, monkeypatch):
    for i in range(1, 8):
        service.repo.upsert(study(str(i), title=f"Pancreatitis cohort {i}"))

    async def fake_get_protocol(study_id, version="latest", download=True, refresh=False):
        if study_id == "1":
            raise RWEError(
                "STUDY_OUT_OF_SCOPE", "Only explicitly labelled Non-interventional studies are supported."
            )
        return {}

    monkeypatch.setattr(service, "get_protocol", fake_get_protocol)
    result = await service.compare_protocols("q", ["pancreatitis"], check_protocols=2)
    last = result["candidates"][-1]
    assert last["study_id"] == "1" and last["protocol_available"] is False and last["out_of_scope"] is True


@pytest.mark.usefixtures("drug_file")
async def test_medicine_adds_its_class_as_a_category_term(service):
    # Given studies naming apixaban, only its class (code or catalogue name), or a sibling medicine
    service.repo.upsert(study("1", exposures=["(B01AF02) apixaban"]))
    service.repo.upsert(study("2", exposures=["(B01AF) Direct factor Xa inhibitors"]))
    service.repo.upsert(study("3", title="Bleeding with direct factor Xa inhibitors"))
    service.repo.upsert(study("4", exposures=["(B01AF01) rivaroxaban"]))
    for i in range(5, 11):
        service.repo.upsert(study(str(i), title=f"Apixaban cohort {i}"))
    # When the caller searches the medicine only
    result = await service.compare_protocols("q", ["apixaban"], role="exposure")
    order = ids(result)
    # Then class-only studies are candidates ranked below every specific match, labelled as category
    assert {"2", "3"} == set(order[-2:])
    assert set(result["candidates"][-1]["matched_term_sources"].values()) == {"category"}
    (added,) = result["medicine_expansion"]
    assert added["category_terms"] == ["B01AF", "Direct factor Xa inhibitors"] and added["queries"] == []
    # And a sibling member of the class is not equated with the requested medicine
    assert "4" not in order


@pytest.mark.usefixtures("drug_file")
async def test_class_query_searches_member_names_even_without_codes(service):
    # Given a class-coded study, a member named without any code, and a member known only to the catalogue
    service.repo.upsert(study("1", exposures=["(B01AF) Direct factor Xa inhibitors"]))
    service.repo.upsert(study("2", title="Rivaroxaban users in primary care"))
    service.repo.upsert(study("3", exposures=["(B01AF07) edoxaban"]))
    service.repo.upsert(study("4", title="Warfarin users"))
    for i in range(5, 11):
        service.repo.upsert(study(str(i), title=f"Direct factor Xa inhibitors cohort {i}"))
    # When the caller searches the class by its name
    result = await service.compare_protocols("q", ["Direct factor Xa inhibitors"], role="exposure")
    # Then every member found by name is a specific match, with the member's origin
    assert {"1", "2", "3"} <= set(ids(result)) and "4" not in ids(result)
    rows = {c["study_id"]: c for c in result["candidates"]}
    assert rows["2"]["matched_term_sources"] == {"rivaroxaban": "ema_medicines"}
    assert rows["3"]["matched_term_sources"]["edoxaban"] == "catalogue_atc"
    assert rows["2"]["rank_features"]["specific_blocks"] == 1
    (added,) = result["medicine_expansion"]
    assert {"apixaban", "rivaroxaban", "edoxaban"} <= set(added["queries"]) and added["category_terms"] == []


@pytest.mark.usefixtures("drug_file")
async def test_medicine_outside_the_ema_dictionary_resolves_through_the_catalogue(service):
    service.repo.upsert(study("1", exposures=["(A10BA02) metformin"]))
    service.repo.upsert(study("2", exposures=["(A10BA) Biguanides"]))
    for i in range(3, 9):
        service.repo.upsert(study(str(i), title=f"Metformin cohort {i}"))
    result = await service.compare_protocols("q", ["metformin"])
    assert "2" in ids(result)
    assert result["medicine_expansion"][0]["category_terms"] == ["A10BA", "Biguanides"]
    # Non-medicine queries add nothing
    result = await service.compare_protocols("q", ["cohort"])
    assert result["medicine_expansion"] == []


def test_category_only_match_is_not_reported_as_the_requested_concept():
    sources = {"apixaban": "query", "B01AF": "category"}
    assert match_cell({"basis": "concept", "terms": ["B01AF"], "sources": sources}, {}).startswith(
        "カテゴリー語だけでの一致"
    )
    assert match_cell({"basis": "concept", "terms": ["apixaban", "B01AF"], "sources": sources}, {}) == (
        "依頼概念での一致: apixaban, B01AF（カテゴリー語）"
    )


@pytest.mark.usefixtures("drug_file")
async def test_added_medicine_names_are_capped_per_question_and_never_relabel_caller_terms(
    service, monkeypatch
):
    monkeypatch.setattr("ema_rwe.service.MAX_NAMES", 3)
    service.repo.upsert(study("1", title="Edoxaban and pancreatitis"))
    # Members known to the catalogue only: apixaban, edoxaban, rivaroxaban, zifaxaban -> one over the cap
    service.repo.upsert(study("9", exposures=["(B01AF07) edoxaban", "(B01AF99) zifaxaban"]))
    for i in range(2, 9):
        service.repo.upsert(study(str(i), title=f"Apixaban and pancreatitis {i}"))
    blocks = [
        ScreeningBlock(role="exposure", queries=["B01AF"]),
        ScreeningBlock(role="outcome", queries=["pancreatitis", "edoxaban"]),
    ]
    result = await service.compare_protocols("q", [], blocks=blocks)
    added = next(e for e in result["medicine_expansion"] if e["block"] == 0)
    # Three names fit the cap; the rest are counted, not silently lost
    assert added["queries"] == ["apixaban", "edoxaban", "rivaroxaban"] and added["omitted"] == 1
    # edoxaban (catalogue only) was typed by the caller in another block: it stays the caller's query
    row = next(c for c in result["candidates"] if c["study_id"] == "1")
    assert row["matched_term_sources"]["edoxaban"] == "query"


async def test_protocol_listing_and_text_layer_order_candidates_without_dropping_them(service):
    # Given equally specific matches: one image-only protocol, one export listing no protocol, one listed
    service.repo.upsert(study("1", title="Statin cohort", protocol_listed=True, protocol_text_layer="none"))
    service.repo.upsert(study("2", title="Statin cohort", protocol_listed=False))
    service.repo.upsert(study("3", title="Statin cohort", protocol_listed=True))
    for i in range(4, 10):
        service.repo.upsert(study(str(i), title="Statin users", protocol_listed=True))
    result = await service.compare_protocols("q", ["statin"])
    order = ids(result)
    # Then listed protocols come first, an unlisted one after them, an unreadable one last: none removed
    assert order.index("3") < order.index("2") < order.index("1") == len(order) - 1
    assert result["candidates"][-1]["protocol_text_layer"] == "none"


@pytest.mark.usefixtures("drug_file")
async def test_absent_medicine_offers_its_atc_class_as_analogous_concepts(service):
    # Given a catalogue that records two SGLT2 inhibitors, but not the requested ipragliflozin
    service.repo.upsert(study("1", exposures=["(A10BK01) dapagliflozin", "(A10BK) SGLT2 inhibitors"]))
    service.repo.upsert(study("2", exposures=["(A10BK03) empagliflozin"]))
    result = await service.compare_protocols("q", ["ipragliflozin", "A10BK05"], role="exposure")
    fallback = result["analogous_fallback"]
    # Then the class (broader) and its recorded members (sibling) are offered, never searched silently
    assert result["total_matches"] == 0 and fallback["status"] == "available"
    offered = {t["term"]: t["relation"] for t in fallback["analogous_terms"]}
    assert offered == {"SGLT2 inhibitors": "broader", "dapagliflozin": "sibling", "empagliflozin": "sibling"}


@pytest.mark.usefixtures("drug_file")
async def test_analogous_rerun_searches_the_offered_atc_class(service):
    service.repo.upsert(study("1", exposures=["(A10BK01) dapagliflozin", "(A10BK) SGLT2 inhibitors"]))
    for i in range(2, 8):
        service.repo.upsert(study(str(i), exposures=["(A10BK03) empagliflozin"]))
    # Rerunning with the same queries and match_scope=analogous, as the fallback instructs
    result = await service.compare_protocols(
        "q", ["ipragliflozin", "A10BK05"], role="exposure", match_scope="analogous"
    )
    assert result["total_matches"] == 7 and result["match_scope"] == "analogous"
