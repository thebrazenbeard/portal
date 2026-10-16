from pathlib import Path

from portal.node_registry import load_execution_nodes

ROOT = Path(__file__).resolve().parents[2]

def test_staged_vn1_is_disabled_and_one_slot():
    nodes = load_execution_nodes(ROOT / 'topology' / 'vn1.staged.yaml')
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_id == 'vn1'
    assert node.max_parallel == 1
    assert node.enabled is False
    assert not node.allows_lane('verification')
    assert not node.allows_lane('semantic_work')

def test_vn1_readiness_source_is_observation_only():
    source = (ROOT / 'scripts' / 'vn1_worker_readiness.py').read_text(encoding='utf-8')
    assert '"route_attached": False' in source
    assert '"authorized_effects": []' in source
    assert '"occupancy": "NOT_OBSERVED"' in source
    assert '"qualified_for_protected_effects": False' in source

def test_readiness_requires_source_bound_suite_receipt():
    source = (ROOT / "scripts" / "vn1_worker_readiness.py").read_text(encoding="utf-8")
    assert "ready = ready and suite_qualified" in source
    assert "qualification.get(\"source_commit\") == observed" in source
    assert "qualification.get(\"pytest_log_sha256\") == log_hash" in source
