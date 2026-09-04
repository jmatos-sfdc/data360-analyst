"""Tests for deterministic, validation-gated provenance report rendering."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml



from data360_analyst.render_provenance_report import (
    CONFIG_TOKEN,
    RenderError,
    embed_config,
    load_config,
    normalize_config,
    render_config,
    render_file,
)


ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "provenance" / "simple-one-ci.json"


def fixture_config():
    return json.loads(FIXTURE.read_text())


def test_render_config_embeds_validated_config():
    rendered, validation = render_config(fixture_config())
    assert validation.valid
    assert CONFIG_TOKEN not in rendered
    assert '<script id="provenance-config" type="application/json">' in rendered
    assert '"id":"inventory-health-provenance"' in rendered


def test_same_config_produces_byte_identical_html():
    first, _ = render_config(fixture_config())
    second, _ = render_config(fixture_config())
    assert first.encode() == second.encode()


def test_key_order_does_not_change_rendered_html():
    config = fixture_config()
    reordered = {key: config[key] for key in reversed(config)}
    first, _ = render_config(config)
    second, _ = render_config(reordered)
    assert first == second


def test_invalid_config_is_rejected_before_output_write(tmp_path):
    config = fixture_config()
    config["edges"][0]["to"] = "missing-node"
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(config))
    output = tmp_path / "report" / "index.html"
    with pytest.raises(RenderError, match="unknown node"):
        render_file(source, output)
    assert not output.exists()
    assert not output.parent.exists()


def test_invalid_config_does_not_overwrite_existing_output(tmp_path):
    config = fixture_config()
    config["nodes"][0]["sampleRows"] = [{"account": "Example"}]
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(config))
    output = tmp_path / "index.html"
    output.write_text("existing report")
    with pytest.raises(RenderError, match="customer row/value preview"):
        render_file(source, output)
    assert output.read_text() == "existing report"


def test_closing_script_content_reaches_validation_gate_and_writes_nothing(tmp_path):
    config = fixture_config()
    config["nodes"][0]["description"] = "payload </script><script>alert(1)</script>"
    source = tmp_path / "unsafe.json"
    source.write_text(json.dumps(config))
    output = tmp_path / "index.html"
    with pytest.raises(RenderError, match="closing script sequences"):
        render_file(source, output)
    assert not output.exists()


def test_embed_config_has_independent_closing_script_defense():
    config = fixture_config()
    config["report"]["title"] = "</script>"
    with pytest.raises(RenderError, match="closing script sequence"):
        embed_config(f"before{CONFIG_TOKEN}after", config)


@pytest.mark.parametrize("template", ["no token", CONFIG_TOKEN + CONFIG_TOKEN])
def test_template_requires_exactly_one_token(tmp_path, template):
    path = tmp_path / "template.html"
    path.write_text(template)
    with pytest.raises(RenderError, match="exactly one"):
        render_config(fixture_config(), template_path=path)


def test_render_file_writes_normalized_config(tmp_path):
    output = tmp_path / "report" / "index.html"
    normalized = tmp_path / "report" / "provenance.json"
    result = render_file(FIXTURE, output, normalized_config_path=normalized)
    assert result.valid
    assert output.exists()
    assert normalized.read_text() == normalize_config(fixture_config())
    assert json.loads(normalized.read_text()) == fixture_config()


def test_normalized_config_is_deterministic():
    config = fixture_config()
    reordered = {key: config[key] for key in reversed(config)}
    assert normalize_config(config) == normalize_config(reordered)


def test_yaml_and_json_load_to_same_config(tmp_path):
    config = fixture_config()
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert load_config(FIXTURE) == load_config(yaml_path)


def test_cli_writes_html_and_normalized_config(tmp_path):
    output = tmp_path / "report" / "index.html"
    normalized = tmp_path / "report" / "provenance.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "data360_analyst.render_provenance_report",
            "--config",
            str(FIXTURE),
            "--output",
            str(output),
            "--normalized-config",
            str(normalized),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert normalized.exists()
    assert result.stdout.count("WROTE:") == 2


def test_cli_invalid_config_returns_nonzero_without_output(tmp_path):
    config = copy.deepcopy(fixture_config())
    config["endpoints"][0]["nodeId"] = "missing-node"
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(config))
    output = tmp_path / "index.html"
    result = subprocess.run(
        [sys.executable, "-m", "data360_analyst.render_provenance_report",
         "--config", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "ERROR:" in result.stderr
    assert not output.exists()
