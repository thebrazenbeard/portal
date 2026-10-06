from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


SOURCE_ROOT = Path(__file__).resolve().parent
PUBLIC_RUNTIME_FILES = (
    "registry/projects.yaml",
    "registry/workers.yaml",
    "policy/scheduling.yaml",
    "topology/dependencies.yaml",
    "portfolio/corpus.public.json",
    "portfolio/advancement_wave.public.json",
    "schemas/project.schema.json",
    "schemas/worker.schema.json",
    "schemas/observation.schema.json",
    "schemas/dependency.schema.json",
    "schemas/frontier.schema.json",
    "schemas/work-unit.schema.json",
    "schemas/portfolio-corpus.schema.json",
    "schemas/portfolio-advancement.schema.json",
    "schemas/p0-project-manifest.schema.json",
)


class PublicResourceBuild(build_py):
    def run(self):
        super().run()
        build_root = Path(self.build_lib).resolve()
        data_root = build_root / "runner" / "data"
        if not data_root.resolve().is_relative_to(build_root):
            raise ValueError("runtime data must remain inside the build directory")
        if data_root.exists():
            shutil.rmtree(data_root)
        for name in PUBLIC_RUNTIME_FILES:
            destination = data_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE_ROOT / name, destination)


setup(cmdclass={"build_py": PublicResourceBuild})
