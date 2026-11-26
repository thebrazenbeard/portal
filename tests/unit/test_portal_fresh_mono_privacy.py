from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest
from portal import desktop_installer as i
NEW="7efe8ea3e354c7f546b4378b162ea114342054a7"

def test_existing_empty_state_is_not_fresh(tmp_path, monkeypatch):
    state=tmp_path/"state"/"vera"
    state.mkdir(parents=True)
    calls=[]
    monkeypatch.setattr(i, "_run", lambda *a, **kw:calls.append(a))
    spec=SimpleNamespace(source_map=lambda:{"vera-mono":SimpleNamespace(sha=NEW)})
    with pytest.raises(RuntimeError, match="fresh"):
        i._initialize_fresh_mono(Path("fixture-python"), tmp_path, spec)
    assert calls==[]
    assert list(state.iterdir())==[]


def test_nonprivate_root_refused_before_initializer(tmp_path, monkeypatch):
    state=tmp_path/"state"
    state.mkdir()
    module=tmp_path/"sources"/"vera-mono"/"packages"/"vera_core"/"src"/"vera_core"/"__init__.py"
    module.parent.mkdir(parents=True);module.touch()
    spec=SimpleNamespace(source_map=lambda:{"vera-mono":SimpleNamespace(sha=NEW)})
    def fake_run(args, **kwargs):
        assert args[0]=="git", "initializer must not run"
        return SimpleNamespace(stdout=NEW)
    monkeypatch.setattr(i, "_run", fake_run)
    def deny(path):
        raise RuntimeError("nonprivate root ACL")
    monkeypatch.setattr(i, "_verify_private_state_acl", deny)
    with pytest.raises(RuntimeError, match="nonprivate"):
        i._initialize_fresh_mono(Path("fixture-python"), tmp_path, spec, str(module))
    assert not (state/"vera").exists()
