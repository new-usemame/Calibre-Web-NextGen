"""Stats chrome, live chart text and filter semantics follow the request locale (#1173).

Render the shipped templates with a compiled gettext catalog and execute their
actual scripts. A translated heading alone cannot hide English demo/empty states
or a category rename that silently drops its metric count.
"""
import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from flask import Flask, render_template
from flask_babel import Babel, gettext
from jinja2 import ChoiceLoader, DictLoader

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _render(tmp_path, locale="fr", return_app=False, shipped=False, **overrides):
    # A translator's compiled contribution exercises the real gettext boundary;
    # translation contents are deliberately hostile to JS/HTML interpolation.
    messages = {
        "User Activity": "Activité des utilisateurs",
        "Server Activity Dashboard": "Activité du serveur",
        "Show Real Data": "Données réelles",
        "No API usage data available": "Aucune donnée d’utilisation de l’API",
        "Email Delivery": "Courriel <livraison> \"'`\n</script>",
        "All Time": "Tout le temps",
        "No failed attempts": "Aucune tentative échouée",
        "Timestamp": "Horodatage",
    }
    if shipped:
        with (ROOT / "cps/translations" / locale / "LC_MESSAGES/messages.po").open("rb") as source:
            catalog = read_po(source, locale=locale)
    else:
        catalog = Catalog(locale=locale)
        for key, value in messages.items():
            catalog.add(key, value)
    directory = tmp_path / locale / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "messages.mo").open("wb") as out:
        write_mo(out, catalog)
    app = Flask(__name__, template_folder=str(ROOT / "cps/templates"))
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(tmp_path)
    Babel(app, locale_selector=lambda: locale)
    app.jinja_loader = ChoiceLoader([
        DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
        app.jinja_loader,
    ])
    context = dict(
        title="Stats", active_tab="activity", is_admin=True, active_users=[],
        selected_user_id=None, days=None, today="2026-10-03", date_range_label="",
        start_date=None, end_date=None, show_warning=False,
        dashboard_stats={**{key: [] for key in (
            "timeline", "top_users", "top_books", "recent_searches",
            "format_distribution", "event_breakdown")}, "totals": {"total_events": 0}},
        cwa_stats={"total_books": 0, "cwa_enforcement": 0, "cwa_conversions": 0, "epub_fixes": 0},
        hardcover_stats=None, conversion_stats={"total": 0, "trend": 0},
        books_added_stats={"total": 0, "trend": 0}, rating_statistics={},
        session_duration={"average_minutes": 0, "distribution": []},
        search_success={"total_searches": 0, "successful_searches": 0, "success_rate": 0},
        failed_logins=[], shelf_activity=[],
    )
    for key in (
        "hourly_heatmap", "reading_velocity", "format_preferences", "discovery_sources",
        "device_breakdown", "api_usage_breakdown", "endpoint_frequency", "api_timing",
        "library_growth", "library_formats", "series_completion", "publication_years",
        "most_fixed_books", "top_enforced_books", "import_source_flows",
        "data_conversions", "data_imports", "data_epub_fixer", "data_epub_fixer_with_fixes",
        "data_enforcement", "data_enforcement_with_paths",
        "headers_conversion", "headers_import", "headers_epub_fixer",
        "headers_epub_fixer_with_fixes", "headers_enforcement", "headers_enforcement_with_paths",
    ):
        context[key] = []
    context.update(overrides)
    context["stats_locale"] = locale
    if return_app:
        return app
    with app.test_request_context():
        app.jinja_env.globals["url_for"] = lambda endpoint, **kw: "/fixture/" + endpoint
        return render_template("cwa_stats_tabs.html", **context)


@pytest.mark.parametrize("locale", ["ru", "fr", "nl"])
def test_translator_catalog_reaches_all_tabs(tmp_path, locale):
    html = _render(tmp_path, locale)
    assert "Activité des utilisateurs" in html
    assert "Activité du serveur" in html
    assert "Tout le temps" in html


def _execute(html, commands):
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    runner = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const nodes = {}, charts = {};
function node(id) { return nodes[id] ||= {innerHTML:'',textContent:'',style:{},value:'',classList:{add(){},remove(){}}}; }
const sandbox = {URL, URLSearchParams, Intl, Date, Math, __charts:charts, console:{log(){}},
 document:{getElementById:node,addEventListener(){}},
 window:{location:{search:'',href:'http://localhost/cwa-stats-show'},addEventListener(){}},
 echarts:{init(el){return {setOption(value){charts[Object.keys(nodes).find(k=>nodes[k]===el)] = value},resize(){}}},graphic:{LinearGradient:function(){}}}};
vm.createContext(sandbox);
for (const script of input.scripts) vm.runInContext(script,sandbox);
vm.runInContext(input.commands,sandbox);
process.stdout.write(JSON.stringify({nodes,charts}));
"""
    result = subprocess.run([shutil.which("node"), "-e", runner], input=json.dumps({
        "scripts": scripts, "commands": commands,
    }), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_live_api_text_translates_without_renaming_metric_keys(tmp_path):
    html = _render(tmp_path, api_usage_breakdown=[["Email Delivery", 7], ["Kobo Sync", 3]])
    result = _execute(html, "initApiCharts(); toggleApiDemoMode();")
    assert "Données réelles" in result["nodes"]["api-demo-button"]["textContent"]
    initial = _execute(html, """
initApiCharts();
const pie = __charts['api-usage-chart'];
document.getElementById('visible-legend').textContent = pie.legend.formatter('Email Delivery');
document.getElementById('visible-label').textContent = pie.series[0].label.formatter({name:'Email Delivery',percent:70});
document.getElementById('visible-tooltip').innerHTML = pie.tooltip.formatter({name:'Email Delivery',value:7,percent:70});
""")
    assert initial["nodes"]["email-delivery-count"]["textContent"] == 7
    chart = initial["charts"]["api-usage-chart"]
    assert sum(item["value"] for item in chart["series"][0]["data"]) == 10
    assert chart["series"][0]["data"][0]["name"] == "Email Delivery"
    assert "Courriel <livraison>" in initial["nodes"]["visible-legend"]["textContent"]
    assert "Courriel <livraison>" in initial["nodes"]["visible-label"]["textContent"]
    tooltip = initial["nodes"]["visible-tooltip"]["innerHTML"]
    assert "Courriel &lt;livraison&gt;" in tooltip
    assert "</script>" not in tooltip
    empty = _execute(_render(tmp_path / "empty"), "initApiCharts();")
    assert empty["charts"]["api-usage-chart"]["title"]["text"] == "Aucune donnée d’utilisation de l’API"


def test_dynamic_activity_text_cannot_become_html(tmp_path):
    payload = '<img src=x onerror="throw 42">'
    html = _render(tmp_path, failed_logins=[["127.0.0.1", payload, "2026-10-03T12:00:00", 1]])
    result = _execute(html, "updateFailedLogins();")
    content = result["nodes"]["failed-logins-list"]["innerHTML"]
    assert payload not in content
    assert "&lt;img" in content


def test_system_history_preserves_text_without_rendering_html(tmp_path):
    payload = '<img src=x onerror="throw 42">'
    html = _render(tmp_path, data_epub_fixer_with_fixes=[[payload + "\nsecond line"]])
    assert payload not in html
    assert "&lt;img" in html and "second line" in html


@pytest.mark.parametrize("field", ["date_range_label", "headers_conversion"])
def test_translated_all_time_and_headers_follow_request_locale(tmp_path, monkeypatch, field):
    from cps import cwa_functions as stats
    app = _render(tmp_path, return_app=True)
    database = MagicMock()
    database.get_active_users.return_value = []
    database.execute_read.return_value = []
    monkeypatch.setattr(stats, "CWA_DB", lambda: database)
    monkeypatch.setattr(stats, "get_cwa_stats", lambda: {})
    monkeypatch.setattr(stats, "current_user", SimpleNamespace(role_admin=lambda: True))
    monkeypatch.setattr(stats, "render_title_template", lambda template, **context: context)
    with app.test_request_context("/cwa-stats-show?days=all"):
        context = inspect.unwrap(stats.cwa_stats_show)()
        assert context["days"] is None
        if field == "date_range_label":
            assert context[field] == "Tout le temps"
        else:
            assert str(context[field][0]) == "Horodatage"
    assert database.get_dashboard_stats.call_args.kwargs["days"] is None


@pytest.mark.parametrize("locale,heading,real_data,fixes", [
    ("ru", "Панель активности сервера", "Показать реальные данные", "2 исправления"),
    ("fr", "Tableau de bord de l’activité du serveur", "Afficher les données réelles", "2 corrections"),
    ("nl", "Dashboard voor serveractiviteit", "Werkelijke gegevens tonen", "2 reparaties"),
])
def test_shipped_catalog_renders_live_controls_and_counted_fixes(tmp_path, locale, heading, real_data, fixes):
    html = _render(tmp_path, locale, shipped=True, most_fixed_books=[
        ("fixture.epub", 2, 2, "2026-10-03", "/owned/fixture.epub"),
    ])
    assert heading in html and fixes in html
    result = _execute(html, "initApiCharts(); toggleApiDemoMode();")
    assert real_data in result["nodes"]["api-demo-button"]["textContent"]
    app = _render(tmp_path / 'page_title', locale, return_app=True, shipped=True)
    with app.test_request_context():
        assert gettext('Calibre-Web NextGen Stats & Activity') != 'Calibre-Web NextGen Stats & Activity'


def test_client_count_formatter_preserves_reordered_fields_and_single_percent(tmp_path):
    html = _render(tmp_path)
    result = _execute(html, """
libraryFormatsChart = echarts.init(document.getElementById('library-formats-chart'));
currentLibraryData.formats = [['EPUB', 1]];
updateLibraryFormatsChart();
document.getElementById('format-tooltip').textContent = __charts['library-formats-chart'].tooltip.formatter({name:'EPUB',value:1,percent:100});
document.getElementById('reordered-message').textContent = statsFormat('%(total)s / %(successful)s', {successful:1,total:2});
""")
    assert result["nodes"]["format-tooltip"]["textContent"] == "EPUB — books: 1 (100%)"
    assert result["nodes"]["reordered-message"]["textContent"] == "2 / 1"


def test_conversion_chart_keeps_input_and_output_stages_distinct(tmp_path):
    html = _render(tmp_path, import_source_flows=[['PDF', 'MOBI', 7], ['MOBI', 'PDF', 3], ['EPUB', 'EPUB', 2]])
    result = _execute(html, """
importSankeyChart = echarts.init(document.getElementById('import-sankey-chart'));
updateImportSankeyChart();
const graph = __charts['import-sankey-chart'];
const edge = graph.series[0].links[0];
document.getElementById('conversion-tooltip').textContent = graph.tooltip.formatter({dataType:'edge',data:edge});
""")
    graph = result['charts']['import-sankey-chart']['series'][0]
    incoming = {edge['target'] for edge in graph['links']}
    outgoing = {edge['source'] for edge in graph['links']}
    assert incoming.isdisjoint(outgoing), 'conversion stages must form a DAG even with reciprocal/same-format conversions'
    assert sum(edge['value'] for edge in graph['links']) == 12
    assert len(graph['data']) == 6
    assert result['nodes']['conversion-tooltip']['textContent'] == 'PDF → MOBI<br/>Conversions: 7'


@pytest.mark.parametrize('random_value', [0, 0.99])
def test_demo_search_rate_and_summary_share_the_same_counts(tmp_path, random_value):
    html = _render(tmp_path)
    result = _execute(html, f"""
Math.random = () => {random_value};
const demo = generateDemoData();
document.getElementById('demo-consistency').textContent = JSON.stringify([demo.totals.total_searches,demo.searchSuccess]);
""")
    total, search = json.loads(result['nodes']['demo-consistency']['textContent'])
    assert total == search['total_searches']
    assert 0 <= search['successful_searches'] <= total
    assert search['success_rate'] == pytest.approx(round(100 * search['successful_searches'] / total, 1))
