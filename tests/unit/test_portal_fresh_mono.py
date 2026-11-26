import ast, pathlib, sys, tempfile, types, unittest, subprocess, json
from pathlib import Path
from unittest.mock import patch
from portal import desktop_installer
NEW='7efe8ea3e354c7f546b4378b162ea114342054a7'; OLD='e5af8cb740267bb5674864571e915842bf5e6677';BASE='49a779017ded184c22712ec1a0dc7a8b558faf71'
class HostBoundary(Exception):pass
class Spec:
 def __init__(self,sha):self.sha=sha
 def validate(self):pass
 def source_map(self):return {'vera-mono':types.SimpleNamespace(sha=self.sha)}
 def write(self,p):p.write_text('{}')
class Tests(unittest.TestCase):
 def run_case(self,sha,*,fail=False,preexisting=False,shadowed=False):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d)/'stage';events=[]
   def prepare(p,s):p.mkdir();(p/'state').mkdir();return p
   def verify(p,r):events.append('imports');return {'vera_core':str(r/'sources'/'vera-mono'/'packages'/'vera_core'/'src'/'vera_core'/'__init__.py')}
   def install_sources(r,s):
    for rel in ('sources/portal/portal/__init__.py','sources/vera-mono/packages/vera_core/src/vera_core/__init__.py','sources/vera-mono/packages/vera_core/src/vera_core/state.py','sources/vera-mono/packages/vera_memory/src/vera_memory/ledger.py'):
     f=r/rel;f.parent.mkdir(parents=True,exist_ok=True);f.touch()
    if preexisting:
     (r/'state'/'vera').mkdir();(r/'state'/'vera'/'sentinel').write_text('keep')
    return Path(sys.executable)
   def run(args,**kw):
    if args[0]=='git':return types.SimpleNamespace(stdout=sha+'\n')
    self.assertEqual(args[:4],[sys.executable,'-I','-B','-c']);self.assertEqual(Path(args[-1]),r/'state'/'vera');self.assertEqual(kw.get('cwd'),r);self.assertEqual(kw.get('timeout'),60)
    self.assertIn('.open()',args[4]);self.assertIn('initialize=True',args[4]);events.append('init-start')
    if fail:raise subprocess.CalledProcessError(3,args[:1])
    fixture=r"""
import sys,types,pathlib
root=pathlib.Path(sys.argv[2]); stage=root.parents[1]
source=stage/'sources'/'vera-mono'
p=types.ModuleType('portal');p.__file__=str(stage/'sources'/'portal'/'portal'/'__init__.py')
m=types.ModuleType('vera_core');m.__file__=str(source/'packages'/'vera_core'/'src'/'vera_core'/'__init__.py');m.__path__=[]
sm=types.ModuleType('vera_core.state');sm.__file__=str(source/'packages'/'vera_core'/'src'/'vera_core'/'state.py')
mem=types.ModuleType('vera_memory');mem.__path__=[]
lm=types.ModuleType('vera_memory.ledger');lm.__file__=str(source/'packages'/'vera_memory'/'src'/'vera_memory'/'ledger.py')
class VeraStateDirectory:
 def __init__(self,root,*,project_id,identity_id,initialize=False):
  assert project_id=='VERA_MONO' and identity_id=='VERA_PROJECT_IDENTITY_V1' and initialize is True
  self.root=pathlib.Path(root)
 def open(self):
  (self.root/'opened').write_text('fresh')
  path=self.root/'memory'/'memory.sqlite';path.parent.mkdir();path.write_bytes(b'fixture')
m.VeraStateDirectory=sm.VeraStateDirectory=VeraStateDirectory
sys.modules.update({'portal':p,'vera_core':m,'vera_core.state':sm,'vera_memory':mem,'vera_memory.ledger':lm})
exec(sys.argv[1].replace('sys.argv[1]', 'sys.argv[2]'))
"""
    if shadowed:fixture=fixture.replace("p.__file__=str(stage/'sources'/'portal'/'portal'/'__init__.py')","p.__file__=str(stage/'shadow.py')")
    subprocess.run([sys.executable,'-I','-B','-c',fixture,args[4],args[-1]],check=True);self.assertEqual((r/'state'/'vera'/'opened').read_text(),'fresh');events.append('init-exit')
   def start(*a):events.append('host');raise HostBoundary()
   with patch.multiple(desktop_installer, _require_fresh_runtime_leaf=lambda p:None,_venv_python=lambda p:Path(sys.executable),launcher_text=lambda *a:'',_assert_fresh_component_destinations=lambda *a:None,prepare_stage_root=prepare,_install_sources=install_sources,_verify_imports=verify,_start_host=start,_run=run,_reject_reparse_ancestors=lambda p:None,_verify_private_state_acl=lambda p:None,write_attestation=lambda *a:{}):
    try:desktop_installer.install(runtime_root=r,spec=Spec(sha),activate=False)
    except HostBoundary:pass
    except (RuntimeError,subprocess.CalledProcessError):events.append('refused')
   if preexisting:self.assertEqual((r/'state'/'vera'/'sentinel').read_text(),'keep')
   return events
 def test_new(self):self.assertEqual(self.run_case(NEW),['imports','init-start','init-exit','host'])
 def test_legacy(self):
  for sha in (OLD,BASE):self.assertEqual(self.run_case(sha),['imports','host'])
 def test_failure(self):self.assertEqual(self.run_case(NEW,fail=True),['imports','init-start','refused'])
 def test_unknown(self):self.assertEqual(self.run_case('f'*40),['imports','refused'])
 def test_nonempty(self):self.assertEqual(self.run_case(NEW,preexisting=True),['imports','refused'])
 def test_shadowed(self):self.assertEqual(self.run_case(NEW,shadowed=True),['imports','init-start','refused'])
if __name__ == "__main__":
 unittest.main(verbosity=2)
