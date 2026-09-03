import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLUEPRINTS = ROOT / "site-deploy/component-library/composition-blueprints.js"
CONFIGURATOR = ROOT / "site-deploy/component-library/evalcall-style-configurator.js"
PAGE = ROOT / "site-deploy/evalcall-style-configurator.html"


def load_examples():
    script = (
        "global.window={};"
        f"require({json.dumps(str(BLUEPRINTS))});"
        "process.stdout.write(JSON.stringify(window.COMPOSITION_BLUEPRINTS));"
    )
    return json.loads(subprocess.check_output(["node", "-e", script], text=True))


def test_blueprint_library_exposes_three_distinct_projects():
    library = load_examples()
    assert library["schema"] == "design-composition/v1"
    assert {item["id"] for item in library["examples"]} == {
        "evalcall",
        "approval",
        "analytics",
    }


def test_blueprint_examples_have_valid_region_relationships():
    allowed = {"stack", "flow", "cards", "table", "form", "timeline"}
    for blueprint in load_examples()["examples"]:
        assert blueprint["schema"] == "design-composition/v1"
        assert blueprint["layout"]["direction"] in {"columns", "rows"}
        assert len(blueprint["layout"]["tracks"]) == len(blueprint["regions"])
        assert all(region["type"] in allowed for region in blueprint["regions"])
        assert len({region["id"] for region in blueprint["regions"]}) == len(
            blueprint["regions"]
        )


def test_configurator_loads_blueprint_data_before_renderer():
    html = PAGE.read_text()
    assert html.index("composition-blueprints.js") < html.index(
        "evalcall-style-configurator.js"
    )


def test_configurator_supports_import_validation_and_dynamic_rendering():
    javascript = CONFIGURATOR.read_text()
    for feature in (
        "blueprintValidation",
        "renderBlueprint",
        "blueprintFile",
        "applyBlueprintJson",
        "composition_blueprint:activeBlueprint",
    ):
        assert feature in javascript
