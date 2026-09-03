from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site-deploy"
APP = SITE / "app.html"
SHELL = SITE / "product-shell.js"
STYLE = SITE / "product-shell.css"
CANONICAL = SITE / "component-library" / "b2b-operations-patterns.json"


class _AppContractParser(HTMLParser):
    """Collect durable product contracts without depending on CSS layout markup."""

    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.product_views: dict[str, dict[str, str]] = {}
        self.primary_nav_elements: list[tuple[str, dict[str, str]]] = []
        self.dashboard_create_actions: list[tuple[str, dict[str, str]]] = []
        self.task_rows: list[dict[str, object]] = []
        self.search_inputs: list[dict[str, str]] = []
        self.label_for_ids: set[str] = set()
        self.operator_view_classes: set[str] = set()
        self.viewport_content = ""
        self.html_lang = ""
        self._current_view: str | None = None
        self._primary_nav_depth = 0
        self._task_row_depth = 0
        self._stack: list[tuple[str, str | None, bool, bool]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        self.elements.append((tag, attrs))
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "html":
            self.html_lang = attrs.get("lang", "")
        if tag == "meta" and attrs.get("name", "").casefold() == "viewport":
            self.viewport_content = attrs.get("content", "")
        if tag == "input" and attrs.get("type", "text").casefold() == "search":
            self.search_inputs.append(attrs)
        if tag == "label" and attrs.get("for"):
            self.label_for_ids.add(attrs["for"])

        previous_view = self._current_view
        entered_primary_nav = False
        entered_task_row = False
        if attrs.get("data-product-view"):
            self._current_view = attrs["data-product-view"]
            self.product_views[self._current_view] = attrs
        if self._current_view in {"dashboard", "create", "results", "review"}:
            self.operator_view_classes.update(attrs.get("class", "").split())
        nav_classes = set(attrs.get("class", "").split())
        if tag == "nav" and (
            "主导航" in attrs.get("aria-label", "")
            or nav_classes.intersection({"ops-nav", "product-nav"})
        ):
            self._primary_nav_depth += 1
            entered_primary_nav = True

        destination = attrs.get("data-product-nav")
        if destination and self._primary_nav_depth:
            self.primary_nav_elements.append((tag, attrs))
        if destination == "create" and self._current_view == "dashboard":
            self.dashboard_create_actions.append((tag, attrs))
        if "data-task-row" in attrs or attrs.get("data-testid") == "task-row":
            self.task_rows.append({"tag": tag, "attrs": attrs, "has_interactive": False})
            self._task_row_depth += 1
            entered_task_row = True
        elif self._task_row_depth and (
            tag in {"a", "button"}
            or (attrs.get("role") in {"link", "button"} and attrs.get("tabindex") == "0")
        ):
            self.task_rows[-1]["has_interactive"] = True

        if tag not in self._VOID_TAGS:
            self._stack.append((tag, previous_view, entered_primary_nav, entered_task_row))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        # The checked HTML is authored markup, but tolerate a mismatched close tag
        # so one unrelated legacy section cannot corrupt the product-view audit.
        index = next(
            (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i][0] == tag),
            -1,
        )
        if index < 0:
            return
        closed = self._stack[index:]
        self._stack = self._stack[:index]
        _, previous_view, _, _ = closed[0]
        self._current_view = previous_view
        for _, _, entered_primary_nav, entered_task_row in closed:
            if entered_primary_nav:
                self._primary_nav_depth = max(0, self._primary_nav_depth - 1)
            if entered_task_row:
                self._task_row_depth = max(0, self._task_row_depth - 1)


def _load_canonical() -> dict:
    return json.loads(CANONICAL.read_text(encoding="utf-8"))


def _parse_app() -> _AppContractParser:
    parser = _AppContractParser()
    parser.feed(APP.read_text(encoding="utf-8"))
    return parser


def test_canonical_json_is_a_versioned_b2b_operations_contract() -> None:
    library = _load_canonical()

    assert library["schema"] == "b2b-operations-design/v1"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", library["version"])
    assert library["subject"]["product"] == "EvalCall"
    assert library["subject"]["dashboard_job"]
    assert "状态" in library["subject"]["interaction_model"]
    assert "动作" in library["subject"]["interaction_model"]

    principles = {item["id"]: item for item in library["design_principles"]}
    assert {
        "work_before_explanation",
        "one_object_one_status_one_action",
        "queue_is_primary",
        "progressive_disclosure",
        "single_primary_action",
        "semantic_color_only",
        "stable_shell",
        "audit_is_context",
    } <= principles.keys()
    for principle in principles.values():
        assert all(principle.get(key) for key in ("name", "rule", "test"))

    anti_patterns = {item["id"] for item in library["anti_patterns"]}
    assert {
        "marketing_hero",
        "decorative_eyebrow",
        "duplicate_cta",
        "kpi_card_wall",
        "process_as_homepage",
    } <= anti_patterns


def test_canonical_references_are_real_product_ui_with_reproducible_geometry() -> None:
    library = _load_canonical()
    references = {item["id"]: item for item in library["reference_sources"]}
    expected = {
        "linear_my_issues": ROOT / "design-reference/product-ui/linear-my-issues.png",
        "vanta_risk_register": ROOT / "design-reference/product-ui/vanta-risk-register.png",
        "retool_admin_panel": ROOT / "design-reference/product-ui/retool-admin-panel.png",
    }

    assert references.keys() == expected.keys()
    for reference_id, source_image in expected.items():
        reference = references[reference_id]
        assert reference["reference_kind"] == "authenticated_product_ui"
        assert reference["source_url"].startswith("https://")
        assert len(reference["captured_size"]) == 2
        assert all(isinstance(size, int) and size >= 800 for size in reference["captured_size"])
        assert reference["geometry"] and reference["tokens"]
        assert reference["composition"] and reference["logic"]
        assert reference["best_for"] and reference["not_for"]
        assert source_image.is_file(), f"missing captured source image: {source_image}"

    validation = library["visual_validation"]
    assert "1440×900" in validation["viewport_policy"]
    assert "390×844" in validation["viewport_policy"]
    assert set(validation["replica_status"]) == set(expected)
    assert all(status == "validated" for status in validation["replica_status"].values())


def test_page_compositions_cover_the_complete_operator_workflow() -> None:
    library = _load_canonical()
    compositions = {item["id"]: item for item in library["page_compositions"]}
    assert {
        "evalcall_task_center",
        "evalcall_create_evaluation",
        "evalcall_result",
        "evalcall_review",
    } <= compositions.keys()

    task_center = compositions["evalcall_task_center"]
    assert task_center["primary_action"] == "创建评测"
    assert any("work_queue" in region for region in task_center["structure"])
    assert "营销 Hero" in task_center["remove"]
    assert "重复创建入口" in task_center["remove"]

    component_ids = {item["id"] for item in library["component_patterns"]}
    assert {
        "shell_navigation",
        "page_header",
        "summary_strip",
        "work_queue",
        "filter_toolbar",
        "status_language",
        "review_layout",
    } <= component_ids
    assert set(library["default_selection"]) == {"task_center", "create", "result", "review"}


def test_app_removes_marketing_hero_and_reasoning_copy() -> None:
    html = APP.read_text(encoding="utf-8")
    javascript = SHELL.read_text(encoding="utf-8")
    rendered_copy = f"{html}\n{javascript}".casefold()

    forbidden_copy = (
        "先统一测试条件",
        "再讨论模型",
        "今日评测运行正常",
        "evaluation command center",
        "human review queue",
        "model evaluation control plane",
    )
    for phrase in forbidden_copy:
        assert phrase.casefold() not in rendered_copy

    forbidden_structures = {
        "overview-hero",
        "hero-copy",
        "hero-actions",
        "hero-kicker",
        "eyebrow",
        "summary-grid",
        "summary-card",
    }
    assert forbidden_structures.isdisjoint(_parse_app().operator_view_classes)


def test_global_navigation_has_stable_operator_information_architecture() -> None:
    parser = _parse_app()
    workflow_views = {"dashboard", "create", "results", "review", "engine"}
    nav_destinations = [attrs["data-product-nav"] for _, attrs in parser.primary_nav_elements]

    assert workflow_views <= set(parser.product_views)
    assert {"dashboard", "review"} <= set(nav_destinations)
    assert set(nav_destinations) <= set(parser.product_views), (
        "Every data-product-nav destination in the global navigation needs a reachable "
        "data-product-view. Use a normal href for external destinations."
    )
    assert len(nav_destinations) == len(set(nav_destinations)), (
        "The primary navigation should expose each product area once; contextual actions belong "
        "inside the active work area."
    )
    for view, attrs in parser.product_views.items():
        label_id = attrs.get("aria-labelledby")
        assert label_id, f"{view} needs aria-labelledby"
        assert label_id in parser.ids, f"{view} references missing heading #{label_id}"

    # The dashboard may contain one visual create action; the global nav already provides
    # orientation and must not be repeated again in a Hero or table toolbar.
    assert len(parser.dashboard_create_actions) == 1


def test_status_vocabulary_is_factual_and_the_unresolved_result_is_not_overstated() -> None:
    library = _load_canonical()
    status_pattern = next(
        item for item in library["component_patterns"] if item["id"] == "status_language"
    )
    rules = status_pattern["rules"]
    categories = ("neutral", "active", "success", "danger")
    statuses = [status for category in categories for status in rules[category]]
    assert len(statuses) == len(set(statuses)), "a status must have one semantic color category"
    assert "待复核" in rules["active"]
    assert "通过门禁" in rules["success"]
    assert "已打回" in rules["danger"]
    assert "事实" in rules["copy_rule"]

    rendered = APP.read_text(encoding="utf-8") + "\n" + SHELL.read_text(encoding="utf-8")
    assert "条件通过" in rendered
    assert "待复核" in rendered
    for overstatement in ("建议灰度上线", "候选模型通过门禁", "裁判 B 的证据链更完整"):
        assert overstatement not in rendered

    # Counts from different scopes are allowed to differ, but the scope must be visible.
    assert "当前任务" in rendered
    assert "全部任务" in rendered or "全局" in rendered


def test_reference_replicas_and_selectable_workbench_are_packaged() -> None:
    expected_files = (
        SITE / "reference-replicas.html",
        SITE / "component-library/reference-replicas.css",
        SITE / "component-library/reference-replicas.js",
        SITE / "b2b-pattern-workbench.html",
        SITE / "component-library/b2b-pattern-workbench.css",
        SITE / "component-library/b2b-pattern-workbench.js",
        ROOT / "scripts/verify_reference_replicas.mjs",
    )
    for file in expected_files:
        assert file.is_file(), f"missing B2B design deliverable: {file}"

    replicas_html = (SITE / "reference-replicas.html").read_text(encoding="utf-8")
    replicas_js = (SITE / "component-library/reference-replicas.js").read_text(encoding="utf-8")
    workbench_html = (SITE / "b2b-pattern-workbench.html").read_text(encoding="utf-8")
    replica_validator = (ROOT / "scripts/verify_reference_replicas.mjs").read_text(encoding="utf-8")

    assert "width=device-width" in replicas_html
    assert "reference-replicas.css" in replicas_html
    assert "reference-replicas.js" in replicas_html
    for view in ("linear", "vanta", "retool"):
        assert view in replicas_js
        assert f'value="{view}"' in workbench_html
        assert f'view: "{view}"' in replica_validator
    for width, height in ((1920, 868), (1888, 980), (1920, 1148)):
        assert f"width: {width}, height: {height}" in replica_validator
    assert "page.screenshot" in replica_validator
    assert "reference-replica-validation" in replica_validator
    assert "b2b-operations-patterns.json" in workbench_html
    assert "reference-replicas.html" in (
        workbench_html + (SITE / "component-library/b2b-pattern-workbench.js").read_text(encoding="utf-8")
    )


def test_competition_gateway_publishes_the_b2b_acceptance_surface() -> None:
    gateway = (ROOT / "scripts/start_competition_gateway.py").read_text(encoding="utf-8")
    for asset in (
        "b2b-pattern-workbench.html",
        "reference-replicas.html",
        "b2b-operations-patterns.json",
        "b2b-pattern-workbench.css",
        "b2b-pattern-workbench.js",
        "reference-replicas.css",
        "reference-replicas.js",
    ):
        assert asset in gateway, f"competition gateway does not publish {asset}"


def test_app_and_design_workbench_keep_core_accessibility_and_viewport_contracts() -> None:
    parser = _parse_app()
    css = STYLE.read_text(encoding="utf-8")
    html = APP.read_text(encoding="utf-8")
    javascript = SHELL.read_text(encoding="utf-8")

    assert parser.html_lang == "zh-CN"
    normalized_viewport = parser.viewport_content.replace(" ", "").casefold()
    assert "width=device-width" in normalized_viewport
    assert "initial-scale=1" in normalized_viewport
    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in parser.elements)
    assert parser.search_inputs
    for search in parser.search_inputs:
        assert (
            search.get("aria-label")
            or search.get("aria-labelledby")
            or search.get("id") in parser.label_for_ids
        ), (
            "search inputs need an explicit accessible name; placeholder text is not a label"
        )

    assert parser.task_rows, "the work queue needs at least one keyboard-reachable task"
    for task_row in parser.task_rows:
        tag = str(task_row["tag"])
        attrs = task_row["attrs"]
        assert isinstance(attrs, dict)
        is_native = tag in {"a", "button"}
        is_keyboard_proxy = (
            attrs.get("role") in {"link", "button"} and attrs.get("tabindex") == "0"
        )
        assert is_native or is_keyboard_proxy or task_row["has_interactive"], (
            "task rows must contain a link/button, or expose role plus tabindex=0 for keyboard users"
        )

    has_initial_current_page = any(
        attrs.get("aria-current") == "page" for _, attrs in parser.primary_nav_elements
    )
    has_dynamic_current_page = bool(
        re.search(r"(?:setAttribute\s*\(\s*['\"]aria-current|\.ariaCurrent\s*=)", javascript)
    )
    assert has_initial_current_page or has_dynamic_current_page
    assert "focus-visible" in html + css
    assert "prefers-reduced-motion" in css
    assert re.search(r"@media\s*\([^)]*max-width\s*:", css)

    workbench = (SITE / "b2b-pattern-workbench.html").read_text(encoding="utf-8")
    assert 'title="参考界面复刻"' in workbench
    assert 'role="status"' in workbench
    assert 'aria-live="polite"' in workbench


def test_product_navigation_preserves_browser_history() -> None:
    javascript = SHELL.read_text(encoding="utf-8")

    assert "history.pushState" in javascript
    assert "popstate" in javascript


def test_workbench_theme_application_is_consumed_by_the_real_product() -> None:
    app_html = APP.read_text(encoding="utf-8")
    app_css = STYLE.read_text(encoding="utf-8")
    app_js = SHELL.read_text(encoding="utf-8")
    workbench_js = (SITE / "component-library/b2b-pattern-workbench.js").read_text(encoding="utf-8")
    library = json.loads((SITE / "component-library/b2b-operations-patterns.json").read_text(encoding="utf-8"))

    assert 'schema: "evalcall-b2b-theme/v3"' in workbench_js
    assert "active_page: state.page" in workbench_js
    assert "shell_layout: state.shell_layout" in workbench_js
    assert 'target.searchParams.set("ui"' in workbench_js
    assert "window.location.assign" in workbench_js

    assert "themeFromUrl" in app_js
    assert "candidate.shell_layout" in app_js
    assert "document.body.dataset.uiLayout" in app_js
    assert 'window.addEventListener("storage"' in app_js
    assert "PAGE_TO_VIEW" in app_js
    assert "data-theme-apply-status" in app_html
    assert workbench_js.count('const layoutNames = { linear: "列表优先" }') == 1
    workbench_html = (SITE / "b2b-pattern-workbench.html").read_text(encoding="utf-8")
    assert workbench_html.count('name="layout"') == 1

    assert 'data-ui-layout="linear"' in app_html
    assert ".ops-sidebar" in app_css
    assert "--ops-sidebar" in app_css

    contract = library["application_contract"]
    assert contract["schema"] == "evalcall-b2b-theme/v3"
    assert contract["allowed_shell_layouts"] == ["linear"]
    assert contract["page_route_mapping"]["review"] == "review"
    assert set(contract["consumed_fields"]) >= {"active_page", "shell_layout", "density", "color"}
    assert "v1/v2" in contract["migration"] and "linear" in contract["migration"]
