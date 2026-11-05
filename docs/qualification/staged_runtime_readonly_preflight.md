# Portal staged-runtime read-only preflight — draft, not host release

The installer in Portal #122 distinguishes **logon activation** from **host
startup**. Even with `activate=False`, `install()` clones sources, installs
packages, starts a staged desktop host and runs live qualification. It is not a
no-effect inspection route.

`portal.desktop_preflight` is a separate source-only inspection command for
an **already staged** runtime. It does not call the installer, launch an
application, connect IPC, bind a port, create a scheduled task, write a
manifest, repair a source tree, or clear a runtime halt.

Example (fill all exact inputs from an independent release-owner receipt):

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -m portal.desktop_preflight --runtime-root "<existing-staged-leaf>" --expected-install-id "<exact-install-id>" --expected-spec-sha256 "<external-sha256>" --expected-attestation-sha256 "<external-sha256>"
```

The two expected SHA-256 digests must come from a separately authenticated
release record, **not** from the runtime directory being inspected. The
inspector checks those bytes, validates the versioned install source map and
attestation, rehashes the staged Git source files through the existing
attestation verifier, and compares the installation-result receipt. It checks
the runtime root and each critical staged subdirectory for redirected paths.
On POSIX it rejects group/world-writable staged roots and critical
directories.

On Windows, its `permission_evidence` field deliberately remains
`WINDOWS_DACL_OWNER_REVIEW_REQUIRED`. Python mode bits cannot qualify NTFS
DACL ownership or exclude a second process holding the *same user token*.
Even a successful source-byte preflight is **not** installed-process identity,
user-session authentication, scheduled-task ownership, active service
readiness, rollback-resistant authority, or protected effect permission.
Host-owner ACL and process evidence must be evaluated separately.

The command returns `SOURCE_PREFLIGHT_ONLY` (exit 0) only when its source and
receipt checks pass. It returns `HOLD` (exit 2) on missing, redirected,
mismatched, or broadly writable artifacts. Neither status authorizes an
installation, process restart, cutover, or host recovery. The check is a
point-in-time observation; same-user tampering and time-of-check-to-time-of-use
races cannot be ruled out by this module.

This draft contains synthetic tests. It has **not** been installed or run
against Patrick's current Portal runtime.
