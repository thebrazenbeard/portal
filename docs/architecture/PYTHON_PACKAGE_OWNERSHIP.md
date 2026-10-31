# PORTAL / Project Runner Python package ownership

PORTAL distributes the Python project `portal-coordinator`. Its public console
commands are `portal` and `portal-desktop`. Its inherited, PORTAL-specific
execution implementation lives under the **private** `portal._runner` module
namespace. It does not install a top-level `runner` package or the
`project-runner` console command.

Standalone Project Runner distributes `project-runner`, owns the top-level
`runner` namespace and the `project-runner` console command. Both distributions
can be installed in one environment without claiming the same files. Their
models and queue-admission APIs have diverged; they are not aliases or
substitutable imports.

## Migration boundary

Previous PORTAL packaging used the same `project-runner==0.1.0` distribution
name and owned overlapping `runner` modules. An installed legacy environment
can therefore be ambiguous. Rebuild in a clean virtual environment, explicitly
install each intended distribution, and verify its own import and command. Do
not try to repair an ambiguous environment by repeatedly reinstalling one
wheel over the other.

Consumer code that relied on `from runner ...` implicitly coming from PORTAL
must be reviewed. The PORTAL kernel's `portal._runner` path is internal and is
not a stable third-party API. Use standalone Project Runner's documented API
when that is the intended dependency; do not assume PORTAL-specific queue
admission parameters exist there. The `portal` CLI remains the supported
operator seam for PORTAL's own orchestration.

This packaging separation addresses wheel/RECORD ownership. It does **not**
prove that a host has an installed, current, authorized PORTAL service or that
the two applications are integrated by a live adapter. Effect authorization,
replay prevention, source currentness, owner lease and runtime custody remain
independent gates.

## Release checks

1. Build both wheels from exact reviewed commits; their `RECORD` paths must be
   disjoint and each distribution must own only its declared entry points.
2. In isolated environments, test both installation orders and uninstall each
   distribution while the other's imports and command remain available.
3. Run PORTAL and Project Runner's own tests separately; test selected public
   CLI behavior, source-currentness admission, and queue preservation.
4. Review migration of existing environments and external imports explicitly.
5. Retain a merge/deployment hold until independent exact-head checks and host
   authority gates pass. A green wheel test is not host authorization.
