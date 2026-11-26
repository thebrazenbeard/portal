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

def _synthetic_initializer(tmp_path, monkeypatch, deny=None):
    state=tmp_path/"state"/"vera"
    state.parent.mkdir()
    module=tmp_path/"sources"/"vera-mono"/"packages"/"vera_core"/"src"/"vera_core"/"__init__.py"
    module.parent.mkdir(parents=True);module.touch()
    spec=SimpleNamespace(source_map=lambda:{"vera-mono":SimpleNamespace(sha=NEW)})
    seen=[]
    def check(path):
        seen.append(("acl",path))
        if path==deny:
            raise RuntimeError("synthetic foreign allow ACE")
    def run(args, **kwargs):
        if args[0]=="git":
            return SimpleNamespace(stdout=NEW)
        seen.append(("initialize",state))
        memory=state/"memory"/"memory.sqlite"
        memory.parent.mkdir()
        memory.write_bytes(b"synthetic")
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(i,"_verify_private_state_acl",check)
    monkeypatch.setattr(i,"_run",run)
    return state,module,spec,seen

@pytest.mark.skipif(__import__("os").name!="nt",reason="Windows mkdir semantics")
def test_windows_fresh_leaf_inherits_verified_parent(tmp_path,monkeypatch):
    state,module,spec,seen=_synthetic_initializer(tmp_path,monkeypatch)
    mkdir=Path.mkdir
    def capture(self,mode=0o777,parents=False,exist_ok=False):
        if self==state:
            seen.append(("mkdir",mode))
        return mkdir(self,mode=mode,parents=parents,exist_ok=exist_ok)
    monkeypatch.setattr(Path,"mkdir",capture)
    i._initialize_fresh_mono(Path("fixture-python"),tmp_path,spec,str(module))
    assert seen.index(("acl",state.parent))<seen.index(("mkdir",0o777))<seen.index(("acl",state))<seen.index(("initialize",state))
    assert ("acl",state/"memory") in seen
    assert ("acl",state/"memory"/"memory.sqlite") in seen

@pytest.mark.skipif(__import__("os").name!="nt",reason="Windows descendant ACL verification")
def test_windows_descendant_custody_failure_refuses_initializer(tmp_path,monkeypatch):
    denied=tmp_path/"state"/"vera"/"memory"/"memory.sqlite"
    state,module,spec,seen=_synthetic_initializer(tmp_path,monkeypatch,deny=denied)
    with pytest.raises(RuntimeError,match="foreign allow ACE"):
        i._initialize_fresh_mono(Path("fixture-python"),tmp_path,spec,str(module))
    assert ("initialize",state) in seen and ("acl",denied) in seen

@pytest.mark.skipif(__import__("os").name!="nt",reason="Windows leaf ACL verification")
def test_windows_leaf_custody_failure_refuses_before_initializer(tmp_path,monkeypatch):
    state,module,spec,seen=_synthetic_initializer(tmp_path,monkeypatch,deny=tmp_path/"state"/"vera")
    with pytest.raises(RuntimeError,match="foreign allow ACE"):
        i._initialize_fresh_mono(Path("fixture-python"),tmp_path,spec,str(module))
    assert ("acl",state) in seen and ("initialize",state) not in seen

@pytest.mark.skipif(__import__("os").name!="nt",reason="Windows descendant custody")
@pytest.mark.parametrize("failure",["root","nested","partial","disappeared"])
def test_windows_descendant_enumeration_failure_refuses(tmp_path,monkeypatch,failure):
    import os
    state,module,spec,seen=_synthetic_initializer(tmp_path,monkeypatch)
    denied=state if failure=="root" else state/"memory"
    original=os.scandir
    def scan(path):
        if Path(path)==denied and failure!="disappeared":
            if failure=="partial":
                class Partial:
                    def __enter__(self):
                        self.entries=original(path)
                        self.first=True
                        return self
                    def __exit__(self,*args):self.entries.close()
                    def __iter__(self):return self
                    def __next__(self):
                        if self.first:
                            self.first=False
                            return next(self.entries)
                        raise PermissionError("synthetic interrupted enumeration")
                return Partial()
            raise PermissionError("synthetic inaccessible directory")
        return original(path)
    monkeypatch.setattr(os,"scandir",scan)
    if failure=="disappeared":
        original_lstat=Path.lstat
        def lstat(self):
            if self==state/"memory"/"memory.sqlite" and ("initialize",state) in seen:
                raise FileNotFoundError("synthetic vanished descendant")
            return original_lstat(self)
        monkeypatch.setattr(Path,"lstat",lstat)
    with pytest.raises(OSError,match="synthetic"):
        i._initialize_fresh_mono(Path("fixture-python"),tmp_path,spec,str(module))
    assert ("initialize",state) in seen
