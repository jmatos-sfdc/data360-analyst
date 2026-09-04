"""Tests for ci_concordance.py."""
from data360_analyst import ci_concordance


def test_extract_dmo_field_refs_basic():
    sql = """
    SELECT
        Sales_Forecast_Period__dlm.BusinessUnit__c AS BusinessUnit__c,
        Sales_Forecast_Period__dlm.ForecastSetId__c AS ForecastSetId__c
    FROM Sales_Forecast_Period__dlm
    INNER JOIN ssot__ProductCatalog__dlm
        ON Sales_Forecast_Period__dlm.ProductId__c = ssot__ProductCatalog__dlm.ssot__Id__c
    """
    dmos, fields, joins = ci_concordance._extract_dmo_field_refs(sql)
    assert dmos == {"Sales_Forecast_Period__dlm", "ssot__ProductCatalog__dlm"}
    assert ("Sales_Forecast_Period__dlm", "BusinessUnit__c") in fields
    assert ("Sales_Forecast_Period__dlm", "ForecastSetId__c") in fields
    assert ("Sales_Forecast_Period__dlm", "ProductId__c") in fields
    assert ("ssot__ProductCatalog__dlm", "ssot__Id__c") in fields
    # Join pair — normalized alphabetically.
    pair = tuple(sorted(("Sales_Forecast_Period__dlm", "ssot__ProductCatalog__dlm")))
    assert pair in joins


def test_extract_ignores_dmo_names_in_strings_and_comments():
    sql = """
    -- Reference to ssot__Ghost__dlm in a comment shouldn't count
    SELECT ssot__Real__dlm.Id__c AS Id__c
    FROM ssot__Real__dlm
    WHERE ssot__Real__dlm.Note__c = 'mentions ssot__Fake__dlm in a string literal'
    """
    dmos, _, _ = ci_concordance._extract_dmo_field_refs(sql)
    assert dmos == {"ssot__Real__dlm"}
    assert "ssot__Ghost__dlm" not in dmos
    assert "ssot__Fake__dlm" not in dmos


def test_build_index_aggregates_fan_in_across_filename_prefixes(mini_client):
    idx = ci_concordance._build_index(mini_client / "queries")
    # SF DMO is referenced by 3 CIs — SalesFcst, SalesForecast, RegionForecast.
    fcst = idx["Sales_Forecast_Period__dlm"]
    assert fcst["ci_fan_in"] == 3
    # BusinessUnit__c projected in all 3 → the top field.
    top_field_names = [f["name"] for f in fcst["top_fields"]]
    assert top_field_names[0] == "BusinessUnit__c"
    assert fcst["top_fields"][0]["count"] == 3
    # Example CIs — all three appear.
    assert set(fcst["example_cis"]) == {
        "SalesFcst_Throughput__cio",
        "SalesForecast_Actualized_Plan__cio",
        "RegionForecast_Account__cio",
    }


def test_build_index_captures_join_partners(mini_client):
    idx = ci_concordance._build_index(mini_client / "queries")
    fcst = idx["Sales_Forecast_Period__dlm"]
    partner_names = {p["name"] for p in fcst["top_join_partners"]}
    # SF joins ssot__ProductCatalog__dlm (in SalesFcst) and ssot__Account__dlm (in SalesForecast).
    assert "ssot__ProductCatalog__dlm" in partner_names
    assert "ssot__Account__dlm" in partner_names


import yaml


def test_cmd_build_writes_dmo_usage_yaml(mini_client):
    rc = ci_concordance.cmd_build(mini_client)
    assert rc == 0
    yaml_path = mini_client / "object-model" / "dmo-usage.yaml"
    assert yaml_path.exists()
    data = yaml.safe_load(yaml_path.read_text())
    assert "Sales_Forecast_Period__dlm" in data
    entry = data["Sales_Forecast_Period__dlm"]
    assert entry["ci_fan_in"] == 3
    # Top field is BusinessUnit__c (projected by all 3 CIs).
    assert entry["top_fields"][0]["name"] == "BusinessUnit__c"
    # Example CIs listed alphabetically.
    assert entry["example_cis"][0] == "RegionForecast_Account__cio"


def test_cmd_build_writes_dmo_usage_md(mini_client):
    ci_concordance.cmd_build(mini_client)
    md_path = mini_client / "object-model" / "dmo-usage.md"
    text = md_path.read_text()
    assert "Sales_Forecast_Period__dlm" in text
    assert "3 CI" in text  # fan-in surfaced in a heading or table


def test_cmd_build_flags_coverage_gaps_when_concept_map_absent(mini_client):
    # No concept-map.yaml present → every DMO should appear as a gap.
    ci_concordance.cmd_build(mini_client)
    text = (mini_client / "object-model" / "dmo-usage.md").read_text()
    assert "Coverage gap" in text or "coverage gap" in text.lower()


def test_cmd_build_flags_only_uncovered_dmos_when_concept_map_present(mini_client):
    # Write a concept-map that covers SF but not the others.
    (mini_client / "concept-map.yaml").write_text(
        "SF:\n  aliases: [SalesFcst]\n  dmos:\n    - Sales_Forecast_Period__dlm\n"
    )
    ci_concordance.cmd_build(mini_client)
    text = (mini_client / "object-model" / "dmo-usage.md").read_text()
    # ssot__Account__dlm is referenced but not in the concept map → in gaps.
    assert "ssot__Account__dlm" in text
    # SF DMO IS in the concept map → not in the coverage-gap section.
    # We locate the gap section and check membership within it.
    gap_section = text.split("Coverage gap")[-1] if "Coverage gap" in text else ""
    assert "Sales_Forecast_Period__dlm" not in gap_section


def test_match_cis_for_term_matches_filename_tokens(mini_client):
    matches = ci_concordance._match_cis_for_term(
        mini_client / "queries", "SalesFcst", []
    )
    names = sorted(m.stem for m in matches)
    assert names == ["SalesFcst_Throughput__cio"]


def test_match_cis_for_term_includes_aliases(mini_client):
    matches = ci_concordance._match_cis_for_term(
        mini_client / "queries", "SF", ["SalesFcst", "SalesForecast"]
    )
    names = sorted(m.stem for m in matches)
    assert names == [
        "SalesFcst_Throughput__cio",
        "SalesForecast_Actualized_Plan__cio",
    ]


def test_propose_for_term_ranks_dmos_by_matched_ci_frequency(mini_client):
    proposal = ci_concordance._propose_for_term(
        mini_client / "queries", "SF", ["SalesFcst", "SalesForecast", "RegionForecast"]
    )
    assert proposal is not None
    assert proposal["term"] == "SF"
    dmo_names = [d["name"] for d in proposal["dmos"]]
    # SF DMO is referenced by all 3 matched CIs → top.
    assert dmo_names[0] == "Sales_Forecast_Period__dlm"
    # key_fields — BusinessUnit__c is projected by all 3 (≥50% threshold met).
    assert "BusinessUnit__c" in proposal["key_fields"]


def test_propose_for_term_returns_none_below_threshold(mini_client):
    proposal = ci_concordance._propose_for_term(
        mini_client / "queries", "NonexistentTerm", [], min_cis=3
    )
    assert proposal is None


def test_glossary_terms_parses_headings_and_aliases(mini_client):
    terms = ci_concordance._glossary_terms(mini_client / "glossary.md")
    term_names = [t[0] for t in terms]
    assert "SF" in term_names
    assert "Market Coverage" in term_names
    # SF has aliases.
    fcst = next(t for t in terms if t[0] == "SF")
    assert "SalesFcst" in fcst[1]
    assert "SalesForecast" in fcst[1]


def test_glossary_terms_handles_alias_on_its_own_line_without_period(tmp_path):
    glossary = tmp_path / "glossary.md"
    glossary.write_text("## FOO\nFoo description.\nAliases: bar, baz\nMore prose.\n")
    terms = ci_concordance._glossary_terms(glossary)
    assert terms == [("FOO", ["bar", "baz"])]


def test_cmd_propose_all_prints_blocks_for_qualifying_terms(mini_client, capsys):
    rc = ci_concordance.cmd_propose(mini_client, term=None, all_flag=True)
    assert rc == 0
    out = capsys.readouterr().out
    # SF hits 3 CIs via aliases → proposal emitted.
    assert "SF:" in out
    assert "Sales_Forecast_Period__dlm" in out


def test_main_build_end_to_end(mini_client, capsys):
    rc = ci_concordance.main(["build", "--output-dir", str(mini_client)])
    assert rc == 0
    assert (mini_client / "object-model" / "dmo-usage.yaml").exists()


def test_main_propose_single_term(mini_client, capsys):
    rc = ci_concordance.main([
        "propose", "--output-dir", str(mini_client), "--term", "SF",
    ])
    assert rc == 0
    # Aliases picked up from glossary → 3 CIs → proposal emitted.
    assert "SF:" in capsys.readouterr().out


def test_main_propose_all(mini_client, capsys):
    rc = ci_concordance.main([
        "propose", "--output-dir", str(mini_client), "--all",
    ])
    assert rc == 0
    assert "SF:" in capsys.readouterr().out


def test_main_missing_subcommand_errors_out(capsys):
    with __import__("pytest").raises(SystemExit):
        ci_concordance.main([])
