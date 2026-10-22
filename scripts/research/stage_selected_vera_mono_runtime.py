import hashlib, json, os, pathlib, subprocess, sys, time, tempfile, threading, queue, sqlite3, ctypes
from ctypes import wintypes

ROOT=pathlib.Path(os.environ.get("VERA_QVR_GATE_ROOT", r"D:\VERA\.scratch\qvr-selected-install-20261021"))
INSTALL=ROOT/"installed"
STATE=ROOT/"gate-state-synthetic"
ROUTE=ROOT/"selected-route-lab.json"
SOURCE_SHA="48e00014b89adeb392102ef45849fbd49fc9dc29"
COMMIT="16a2828dfa7fb7e6c0e19004f014bc68fc47badf"

def gitblob(p):
    data=p.read_bytes()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def fingerprint(path):
    lines=[]
    for p in sorted(path.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts: continue
        rel=p.relative_to(path).as_posix()
        lines.append(rel+" "+hashlib.sha256(p.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(),len(lines)

def get_exe(pid):
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    k.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    k.OpenProcess.restype=wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes=(wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
    k.QueryFullProcessImageNameW.restype=wintypes.BOOL
    k.CloseHandle.argtypes=(wintypes.HANDLE,)
    h=k.OpenProcess(0x1000,False,pid)
    if not h:raise OSError("cannot inspect process object")
    try:
        size=wintypes.DWORD(4096)
        buf=ctypes.create_unicode_buffer(4096)
        if not k.QueryFullProcessImageNameW(h,0,buf,ctypes.byref(size)):raise OSError("QueryFullProcessImageNameW failed")
        return buf.value
    finally:k.CloseHandle(h)

CHILD=r"""
import os,sys,json,pathlib,time,uuid,hashlib,inspect
route_path=pathlib.Path(sys.argv[1]);phase=sys.argv[2]
data=route_path.read_bytes();route=json.loads(data);initial=hashlib.sha256(data).hexdigest()
if route.get("schema")!="SCRATCH_QVR_SELECTED_ROUTE_V1" or route.get("selected") is not True or route.get("target")!="vera-mono-0.1.0-QVR" or route.get("mode")!="LAB_ONLY":
    raise RuntimeError("no exact selected lab route")
install=pathlib.Path(route["install_root"])
if not install.is_dir() or install.is_symlink():raise RuntimeError("selected installation missing")
lines=[]
for file in sorted(install.rglob("*")):
    if not file.is_file() or "__pycache__" in file.parts:continue
    lines.append(file.relative_to(install).as_posix()+" "+hashlib.sha256(file.read_bytes()).hexdigest())
observed_install_digest=hashlib.sha256("\n".join(lines).encode()).hexdigest()
if observed_install_digest != route.get("installed_files_sha256"):
    raise RuntimeError("installed artifact digest does not match selected route")
sys.path.insert(0,str(install))
from vera_core import QualifiedVeraRuntime,VeraStateDirectory
exact=pathlib.Path(inspect.getfile(QualifiedVeraRuntime)).resolve()
if exact!=(install/"vera_core/qualified_runtime.py").resolve():raise RuntimeError("wrong loaded QVR source")
blob=exact.read_bytes()
if hashlib.sha1(b"blob "+str(len(blob)).encode()+b"\0"+blob).hexdigest()!=route["source_git_blob"]:
    raise RuntimeError("wrong QVR source blob")
s=VeraStateDirectory(route["state_root"],project_id="scratch-selected-route",identity_id="scratch-vera")
rt=QualifiedVeraRuntime.from_state_directory(s)
if rt.pc_execution_transport is not None or rt.provider_execution_transports or rt.source_mutation_transports or rt.outbound_trust.generation!=0 or rt.audit.context()["effect_count"]!=0:
    raise RuntimeError("unexpected runtime effects or trust")
if phase=="first":
    from vera_memory import AdmissionRequest,MemoryClass
    request=AdmissionRequest(record_id="synthetic-route-1",text="TEST-ONLY: no autobiographical data",memory_class=MemoryClass.WORKING_PROJECT,source_actor="host-test",authority_ref="test-not-real-authority",privacy_ref="synthetic-data",provenance_refs=("test:source",),operation_id="host-test-1",project_id="scratch-selected-route",governed_identity_id="scratch-vera")
    added=rt.lifecycle.memory.admit(request,expected_head=rt.lifecycle.memory.current_head)
    receipt=rt.lifecycle.checkpoint(checkpoint_id="route-cp-1",runtime_id="test-resident",expected_memory_head=added["store_head"],expected_checkpoint_head=rt.lifecycle.checkpoints.current_head,expected_currentness_generation=None,commitments=("runtime-restart",))
    digest=receipt.recovery_checkpoint_digest;status="INITIAL_READY"
elif phase=="second":
    resume=rt.resume_context();digest=resume["accepted_checkpoint_digest"];status=resume["status"]
    if status!="ACCEPTED_CURRENT" or resume["commitments"]!=["runtime-restart"]:raise RuntimeError("restart state not accepted")
else:raise RuntimeError("bad phase")
stop=route_path.parent/(phase+".stop")
heartbeat=route_path.parent/(phase+".heartbeat.json")
start=time.monotonic();seq=0
while time.monotonic()-start<14:
    if hashlib.sha256(route_path.read_bytes()).hexdigest()!=initial:raise RuntimeError("selected route mutated")
    seq+=1
    obj={"schema":"SCRATCH_RESIDENT_OBSERVATION_V1","phase":phase,"pid":os.getpid(),"sequence":seq,"route_digest":initial,"loaded_qvr":str(exact),"checkpoint_digest":digest,"state_status":status,"effect_count":0,"provider_count":0}
    temp=heartbeat.with_suffix(".tmp")
    with temp.open("w",encoding="utf-8") as f:
        json.dump(obj,f,sort_keys=True);f.flush();os.fsync(f.fileno())
    os.replace(temp,heartbeat)
    if seq==1:print(json.dumps({"started":True,"pid":os.getpid(),"phase":phase,"route_digest":initial}),flush=True)
    if stop.exists():print("STOP_ACK",flush=True);break
    time.sleep(.2)
else:raise RuntimeError("resident max time exceeded")
"""

def must(cond,reason):
    if not cond:raise RuntimeError(reason)

def spawn(phase,expected):
    hp=ROOT/(phase+".heartbeat.json");sp=ROOT/(phase+".stop")
    must(not hp.exists() and not sp.exists(),"scratch phase artifacts must be absent")
    env=dict(os.environ)
    env.pop("PYTHONPATH",None)
    process=subprocess.Popen([sys.executable,"-B","-u","-c",CHILD,str(ROUTE),phase],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        limit=time.monotonic()+8
        last=None
        while time.monotonic()<limit:
            if process.poll() is not None:break
            if hp.exists():
                last=json.loads(hp.read_text(encoding="utf-8"))
                if last["sequence"]>=2:break
            time.sleep(.08)
        if process.poll() is not None:
            err=process.stderr.read() if process.stderr else ""
            raise RuntimeError("resident exited before observation: "+err[-1400:])
        must(last is not None and last["sequence"]>=2,"heartbeat missing")
        exe=get_exe(process.pid)
        must(os.path.normcase(os.path.realpath(exe))==os.path.normcase(os.path.realpath(sys.executable)),"OS selected wrong interpreter")
        must(last["pid"]==process.pid,"heartbeat forged pid")
        must(last["route_digest"]==expected,"not consuming route")
        must(last["loaded_qvr"]==str(INSTALL/"vera_core/qualified_runtime.py"),"not consuming installed package")
        must(last["effect_count"]==0 and last["provider_count"]==0,"external effect unexpectedly bound")
        digest=last["checkpoint_digest"]
        first_seq=last["sequence"]
        time.sleep(.3)
        second=json.loads(hp.read_text(encoding="utf-8"))
        must(second["sequence"]>first_seq and second["pid"]==process.pid,"resident did not remain alive across separate OS reads")
        sp.write_text("STOP",encoding="ascii")
        output,errors=process.communicate(timeout=7)
        must(process.returncode==0,"resident failed: "+errors[-1200:])
        must("STOP_ACK" in output,"no shutdown acknowledgement")
        return {"pid_os":process.pid,"executable":exe,"heartbeat_seq":second["sequence"],"checkpoint_digest":digest,"state_status":second["state_status"],"route_digest":expected,"clean_shutdown":True}
    finally:
        if process.poll() is None:
            process.kill();process.communicate(timeout=4)

must(INSTALL.is_dir(),"isolated package not installed")
must(not STATE.exists() and not ROUTE.exists(),"test scratch state or route exists; refuse collision")
must(gitblob(INSTALL/"vera_core/qualified_runtime.py")==SOURCE_SHA,"installed QVR bytes mismatch")
digest,count=fingerprint(INSTALL)
route={"schema":"SCRATCH_QVR_SELECTED_ROUTE_V1","selected":True,"mode":"LAB_ONLY","target":"vera-mono-0.1.0-QVR","source_commit":COMMIT,"source_git_blob":SOURCE_SHA,"install_root":str(INSTALL),"state_root":str(STATE),"installed_files_sha256":digest}
ROOT.mkdir(parents=True,exist_ok=True)
with ROUTE.open("x",encoding="utf-8") as f:json.dump(route,f,sort_keys=True);f.flush();os.fsync(f.fileno())
rd=hashlib.sha256(ROUTE.read_bytes()).hexdigest()
first=spawn("first",rd)
must(fingerprint(INSTALL)==(digest,count),"artifact changed during first run")
second=spawn("second",rd)
must(first["pid_os"]!=second["pid_os"],"pid reused; cannot establish separate process")
must(first["checkpoint_digest"]==second["checkpoint_digest"],"restart checkpoint mismatch")
must(second["state_status"]=="ACCEPTED_CURRENT","reopen not accepted")
must(fingerprint(INSTALL)==(digest,count),"artifact changed across restart")
with sqlite3.connect((STATE/"recovery/checkpoints.sqlite").as_uri()+"?mode=ro",uri=True) as db:
    rows=db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
must(rows==1,"checkpoint count not one")
print("GATE_REPORT",json.dumps({"claim_ceiling":"SCRATCH_INSTALLED_ARTIFACT_SELECTED_ROUTE_OBSERVED_RESIDENT_COLD_RESTART","source_commit":COMMIT,"installed_files":count,"installed_tree_digest":digest,"installed_qvr_blob":SOURCE_SHA,"route_digest":rd,"first":first,"second":second,"checkpoints":rows,"production_route_selected":False,"production_service_active":False,"local_inference_consumed":False,"independent_reviewed":False,"staged_test_route":str(ROUTE),"stopped_cleanly":True},sort_keys=True),flush=True)