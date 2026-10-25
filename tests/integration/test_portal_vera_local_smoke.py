"""Opt-in cross-repository source smoke for PORTAL and Vera Mono.

Set VERA_MONO_SOURCE_ROOT to an exact, independently verified checkout.  The
fixture never talks to a real Ollama daemon or modifies a user runtime root.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType

import pytest

from portal.desktop_cognition import discover_cognition_routes
from portal.desktop_runtime import CognitionLedger, CognitionRequestEnvelope, ResidentCognitionEngine
from portal.desktop_vera_acceptance import VeraRuntimeAcceptance


def _assert_vera_source_closure(source_packages: dict[str, Path]) -> None:
    """Reject a mixed import graph, including preloaded transitive packages.

    An imported source package can already be cached before sys.path changes.
    Check every loaded module under the declared monorepo top-level names,
    including module files, package/namespace paths and loader origins.
    This checks resolved local import paths, not signed source provenance.
    """
    for module_name, module in tuple(sys.modules.items()):
        root = source_packages.get(module_name.partition(".")[0])
        if root is None:
            continue
        if module is None:
            raise RuntimeError(f"Vera Mono source closure mismatch: {module_name} is blocked")
        paths: list[object] = []
        file_path = getattr(module, "__file__", None)
        if file_path is not None:
            paths.append(file_path)
        namespace_paths = getattr(module, "__path__", None)
        if namespace_paths is not None:
            if isinstance(namespace_paths, (str, bytes)):
                raise RuntimeError(f"Vera Mono source closure mismatch: {module_name} path malformed")
            paths.extend(namespace_paths)
        spec = getattr(module, "__spec__", None)
        if spec is not None:
            origin = getattr(spec, "origin", None)
            if origin not in (None, "built-in", "frozen", "namespace"):
                paths.append(origin)
            locations = getattr(spec, "submodule_search_locations", None)
            if locations is not None:
                paths.extend(locations)
        if not paths:
            raise RuntimeError(f"Vera Mono source closure mismatch: {module_name} has no source path")
        try:
            resolved = [Path(path).resolve(strict=True) for path in paths]
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Vera Mono source closure mismatch: {module_name} has an invalid source path"
            ) from exc
        if any(not path.is_relative_to(root) for path in resolved):
            raise RuntimeError(f"Vera Mono source closure mismatch: {module_name} imports another checkout")


def _qualified_vera_classes(monkeypatch):
    configured = os.environ.get("VERA_MONO_SOURCE_ROOT")
    if not configured:
        pytest.skip("set VERA_MONO_SOURCE_ROOT for opt-in cross-repository source qualification")
    source_root = Path(configured).resolve(strict=True)
    packages = source_root / "packages"
    roots = sorted(packages.glob("*/src"))
    if not roots or not (packages / "vera_core" / "src" / "vera_core").is_dir():
        pytest.fail("VERA_MONO_SOURCE_ROOT does not contain Vera Mono source packages")
    source_packages: dict[str, Path] = {}
    for root in roots:
        for package in root.iterdir():
            if package.is_dir() and (package / "__init__.py").is_file():
                resolved = package.resolve(strict=True)
                if not resolved.is_relative_to(source_root) or package.name in source_packages:
                    pytest.fail("Vera Mono source tree has external or duplicate packages")
                source_packages[package.name] = resolved
    for root in roots:
        monkeypatch.syspath_prepend(str(root))
    verifier = lambda: _assert_vera_source_closure(source_packages)
    # Reject a previously loaded foreign module *before* importing Vera core,
    # since its import graph may execute transitive module-level code.
    verifier()
    vera_core = importlib.import_module("vera_core")
    if not Path(vera_core.__file__).resolve().is_relative_to(source_root):
        pytest.fail("loaded Vera Mono code is not from VERA_MONO_SOURCE_ROOT")
    verifier()
    return vera_core.VeraStateDirectory, vera_core.QualifiedVeraRuntime, verifier


@pytest.mark.parametrize(
    ("poisoned_module", "namespace_only"),
    [
        ("vera_memory", False),
        ("vera_memory.ledger", False),
        ("coordination_bus", True),
    ],
)
def test_crossrepo_smoke_rejects_transitive_module_from_another_checkout(
    tmp_path: Path, monkeypatch, poisoned_module: str, namespace_only: bool,
) -> None:
    _qualified_vera_classes(monkeypatch)
    foreign_root = tmp_path / "other-vera-checkout" / "packages" / "alien" / "src"
    foreign_module = ModuleType(poisoned_module)
    if namespace_only:
        (foreign_root / "coordination_bus").mkdir(parents=True)
        foreign_module.__file__ = None
        foreign_module.__path__ = [str(foreign_root / "coordination_bus")]
    else:
        parts = poisoned_module.split(".")
        foreign_file = (
            foreign_root / parts[0] / "__init__.py"
            if len(parts) == 1 else foreign_root / parts[0] / f"{parts[1]}.py"
        )
        foreign_file.parent.mkdir(parents=True)
        foreign_file.write_text("# synthetic foreign source\n", encoding="utf-8")
        foreign_module.__file__ = str(foreign_file)
    monkeypatch.setitem(sys.modules, poisoned_module, foreign_module)
    original_import = importlib.import_module
    vera_import_calls: list[str] = []

    def tracked_import(name: str, *args):
        if name == "vera_core":
            vera_import_calls.append(name)
        return original_import(name, *args)

    monkeypatch.setattr(importlib, "import_module", tracked_import)
    with pytest.raises(RuntimeError, match="Vera Mono source closure mismatch"):
        _qualified_vera_classes(monkeypatch)
    assert vera_import_calls == [], "source closure must fail before importing Vera core"


def test_portal_local_ollama_claim_enters_real_vera_context_and_reopens_durably(
    tmp_path: Path, monkeypatch,
) -> None:
    VeraStateDirectory, QualifiedVeraRuntime, verify_source_closure = _qualified_vera_classes(monkeypatch)

    received: list[dict[str, object]] = []

    class SyntheticOllama(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/api/generate"
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            received.append(payload)
            result = json.dumps({
                "model": "vera-local:latest", "response": "local synthesis 🧪",
                "done": True,
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticOllama)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        import portal.desktop_runtime as desktop_runtime

        original_connection = desktop_runtime._DeadlineHTTPConnection

        def local_test_connection(host: str, port: int, *, timeout: float):
            assert (host, port) == ("127.0.0.1", 11434)
            return original_connection(host, server.server_port, timeout=timeout)

        monkeypatch.setattr(desktop_runtime, "_DeadlineHTTPConnection", local_test_connection)
        routes = discover_cognition_routes(
            command_probe=lambda *_: None,
            ollama_tags=lambda: {"models": [{"name": "vera-local:latest"}]},
            pre_active_models=lambda: None,
        )
        assert len(routes) == 1
        assert routes[0].effect_authority_ceiling == "COGNITION_ONLY_NO_PROTECTED_EFFECT"

        vera_root = tmp_path / "vera"
        cognition_path = tmp_path / "portal" / "cognition.sqlite3"

        def open_vera():
            state = VeraStateDirectory(
                vera_root, project_id="VERA_MONO",
                identity_id="VERA_PROJECT_IDENTITY_V1",
            )
            return QualifiedVeraRuntime.from_state_directory(state)

        request = CognitionRequestEnvelope(
            request_id="cross-repo-smoke-1", source="HUMAN", reason="synthetic-smoke",
            task="What is the next bounded step?", created_at=100.0,
        )
        vera = open_vera()
        verify_source_closure()
        ledger = CognitionLedger(cognition_path)
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: routes,
            accept_result=VeraRuntimeAcceptance(vera, runtime_id="synthetic-host"),
        )
        first = engine.process(request)
        assert first.state == "COMPLETED"
        assert first.response_text == "local synthesis 🧪"
        assert received == [{
            "model": "vera-local:latest", "prompt": request.task, "stream": False,
        }]
        first_row = ledger.get(request.request_id)
        assert first_row is not None
        acceptance = json.loads(str(first_row["acceptance_json"]))
        assert acceptance["status"] == "ACCEPTED_HOST_OBSERVATION"
        assert acceptance["canonical_memory_write"] is False
        assert acceptance["protected_effect_authority"] is False
        assert len(acceptance["context_digest"]) == 64
        ledger.close()

        # Recreate both durable consumers from the same temporary source-bound
        # roots. The second request must replay the stored receipt, not invoke
        # the synthetic model or confer any additional effect authority.
        reopened_vera = open_vera()
        verify_source_closure()
        assert reopened_vera.resume_context()["outbound_effect_integrity"]["fence_effect_count"] == 0
        reopened_ledger = CognitionLedger(cognition_path)
        try:
            reopened_engine = ResidentCognitionEngine(
                ledger=reopened_ledger, discover_routes=lambda: routes,
                accept_result=VeraRuntimeAcceptance(
                    reopened_vera, runtime_id="synthetic-host",
                ),
            )
            replay = reopened_engine.process(request)
            assert replay == first
            assert len(received) == 1
            assert json.loads(str(reopened_ledger.get(request.request_id)["acceptance_json"])) == acceptance
        finally:
            reopened_ledger.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
