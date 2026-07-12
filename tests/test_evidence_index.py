"""M5：主张—证据自动索引。"""

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_evidence_index.py"
SPEC = importlib.util.spec_from_file_location("evidence_index", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_pointer_supports_dicts_and_lists():
    assert MODULE._pointer({"a": [{"b": 3}]}, "a.0.b") == 3


def test_registry_passes_against_real_artifacts():
    result = MODULE.build(MODULE.ROOT / "data/evidence_registry.yaml")
    assert result["passed"] is True
    assert len(result["claims"]) >= 7
    raw = json.dumps(result, ensure_ascii=False)
    assert "gpt-5.6-sol" in raw
