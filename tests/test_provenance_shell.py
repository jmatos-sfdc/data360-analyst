"""Static contract tests for the config-driven provenance HTML shell."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from data360_analyst.render_provenance_report import render_file

from tests._client_terms import client_terms


ROOT = Path(__file__).parent.parent
SHELL = (
    ROOT
    / ".claude"
    / "skills"
    / "data360-provenance-report"
    / "assets"
    / "provenance-report.html"
)
FIXTURE = Path(__file__).parent / "fixtures" / "provenance" / "simple-one-ci.json"
TOKEN = "__PROVENANCE_CONFIG__"


def render_fixture(tmp_path):
    output = tmp_path / "index.html"
    render_file(FIXTURE, output)
    return output.read_text()


def test_shell_has_one_config_insertion_point():
    shell = SHELL.read_text()
    assert shell.count(TOKEN) == 1
    assert '<script id="provenance-config" type="application/json">' in shell


def test_minimal_fixture_renders_without_shell_edits(tmp_path):
    rendered = render_fixture(tmp_path)
    assert TOKEN not in rendered
    assert '"id":"inventory-health-provenance"' in rendered
    assert '<script id="provenance-config" type="application/json">' in rendered


@pytest.mark.parametrize(
    "landmark",
    [
        'id="groupSelect"',
        'id="endpointGrid"',
        'id="network"',
        'id="nodeNav"',
        'id="helpOverlay"',
        'id="minimapCanvas"',
        'id="nodeInspector"',
        'id="endpointResizer"',
        'id="inspectorResizer"',
    ],
)
def test_shell_contains_required_landmarks(landmark):
    assert landmark in SHELL.read_text()


def test_shell_has_no_client_specific_runtime_terms():
    shell = SHELL.read_text().lower()
    # Generic runtime/leak markers (never confidential — safe to name inline).
    forbidden = [
        "recordalert",
        "record_alert_item",
        "dpsection",
        "npi",
        "replacement",
        "displacement",
    ]
    # Confidential client terms come from the gitignored loader.
    forbidden += client_terms()
    assert [term for term in forbidden if term in shell] == []


def test_shell_uses_explicit_edge_semantics():
    shell = SHELL.read_text()
    assert "edge.type==='data_flow'" in shell
    assert "edge.type==='derivation'" in shell
    assert "semantic==='grouping'" in shell
    assert "semantic==='relationship'" in shell
    assert "edge.grouping" not in shell


def test_shell_properties_are_rendered_as_text_not_html():
    shell = SHELL.read_text()
    assert "row.children[1].textContent" in shell


def test_search_arrow_keys_transfer_focus_to_node_list():
    shell = SHELL.read_text()
    assert "function nodeSearchKeydown" in shell
    assert "nodeNavSearch').addEventListener('keydown',nodeSearchKeydown)" in shell


def test_shell_preserves_reference_node_proportions():
    shell = SHELL.read_text()
    assert "heightConstraint:node.role==='dpe'?{minimum:44}:{minimum:34}" in shell
    assert "{minimum:110,maximum:200}" in shell


def test_opening_and_reset_use_top_left_anchored_view():
    shell = SHELL.read_text()
    assert "function openingView()" in shell
    assert "x:bounds.x0-20+(container.clientWidth/2)/minScale" in shell
    assert "y:bounds.y0-20+(container.clientHeight/2)/minScale" in shell
    assert "setTimeout(openingView,300)" in shell
    assert "resetGraph').addEventListener('click',openingView)" in shell


def test_inspector_leads_with_onboarding_before_relationships():
    shell = SHELL.read_text()
    assert shell.index('id="onboarding"') < shell.index('class="relations"')


def test_endpoint_technical_detail_renders_in_right_inspector():
    shell = SHELL.read_text()
    assert 'id="inspectorEndpointDetail"' in shell
    assert "function renderEndpointDetail(nodeId)" in shell
    select_endpoint = shell[shell.index("function selectEndpoint"):shell.index("function clearSelection")]
    assert "endpointDetail" not in select_endpoint


def test_node_properties_support_lane_arrays_in_inspector():
    shell = SHELL.read_text()
    assert "function formatPropertyValue(value)" in shell
    assert "value.every(item=>typeof item!=='object'||item===null)?value.join(', ')" in shell
    assert "value.map(item=>formatPropertyValue(item)).join('\\n')" in shell


def test_endpoint_technical_label_renders_in_inspector():
    shell = SHELL.read_text()
    assert "if(endpoint.technicalLabel)" in shell
    assert "endpoint.technicalLabel" in shell
    assert "endpoint-technical-label" in shell


def test_consumer_role_uses_friendly_layer_label():
    shell = SHELL.read_text()
    assert "node.role==='consumer' ? layerById.get(node.layerId).label" in shell


def test_node_level_overrides_display_layer_order():
    shell = SHELL.read_text()
    assert "level:Number.isInteger(node.level)?node.level:layerById.get(node.layerId).order" in shell


def test_inspector_prefers_curated_display_properties():
    shell = SHELL.read_text()
    assert "node.displayProperties||fallbackDisplayProperties(node.properties||{})" in shell
    assert "property-value multiline" not in shell


def test_shell_palette_matches_reference_chrome():
    shell = SHELL.read_text().replace(" ", "")
    for declaration in (
        "--muted:#757575",
        "--hover:#f5f5f5",
        "--border:#e0e0e0",
        "--shadow-sm:01px2pxrgba(17,24,39,.06),01px3pxrgba(17,24,39,.08)",
        "--shadow-md:04px6pxrgba(17,24,39,.05),010px15pxrgba(17,24,39,.08)",
        "--border:#273244",
        "--hover:#1a2840",
        "--surface:#111b2e",
    ):
        assert declaration in shell
    assert "idle:'#b0b7c3'" in shell
    assert "dim:'#e0e0e0'" in shell
    assert "idle:'#3d4b63'" in shell
    assert "dim:'#222d40'" in shell


def test_node_borders_are_pronounced_and_fill_relative():
    shell = SHELL.read_text()
    assert "function darkenNodeBorder(hex)" in shell
    assert "Math.round(v*.7)" in shell
    assert "border:isDark()?shade(fill,15):darkenNodeBorder(fill)" in shell
    assert "borderWidth:node.role==='dpe'?2.6:2" in shell
    assert "borderWidthSelected:3" in shell


def test_edge_label_font_repaints_with_theme():
    shell = SHELL.read_text()
    assert "function edgeFont()" in shell
    assert "background:isDark()?'rgba(15,23,42,.92)':'rgba(255,255,255,.92)'" in shell
    assert "font:edgeFont()" in shell
    assert "edgesData.update(config.edges.map(edge=>({id:edge.id,...edgeStyle(edge)})))" in shell


def test_node_glow_matches_reference_and_repaints_with_theme():
    shell = SHELL.read_text()
    assert "function nodeShadow(node)" in shell
    assert "color:'rgba(203,213,225,.82)'" in shell
    assert "size:parent?25.5:16.5" in shell
    assert "color:'rgba(2,8,23,.42)'" in shell
    assert "size:parent?19.5:12" in shell
    assert "y:parent?10:7" in shell
    assert "shadow:nodeShadow(node)" in shell
    assert "color:nodeColor(node),shadow:nodeShadow(node),font:" in shell


def test_nodes_use_native_single_pass_draw_order():
    shell = SHELL.read_text()
    assert "redrawNodesOnTop" not in shell
    assert "bodyNode.draw(ctx)" not in shell


def test_node_labels_strip_only_trailing_api_suffixes():
    shell = SHELL.read_text()
    assert "node.label.replace(/__(c|cio|dll|dlm)$/i,'')" in shell
    assert "root.children[0].textContent=node.label" in shell
    assert "node.label.toLowerCase().includes(query)" in shell
    assert "inspectorTitle').textContent=node.label" in shell


def test_shell_javascript_parses_with_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    rendered = render_fixture(tmp_path)
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", rendered, re.DOTALL)
    executable = scripts[-1]
    script_path = tmp_path / "provenance-shell.js"
    script_path.write_text(executable)
    result = subprocess.run(
        [node, "--check", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
