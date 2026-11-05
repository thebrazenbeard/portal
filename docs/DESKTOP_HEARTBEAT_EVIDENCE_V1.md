# Desktop heartbeat evidence (source-only)

`python -m portal.desktop_heartbeat_evidence --runtime-root <installed-runtime-root>`

This command reads at most 65,537 bytes from `bridge/heartbeat.json` and
reports one of `MISSING`, `EMPTY`, `ZERO_FILLED`, `OVERSIZED`,
`SYMLINK_UNTRUSTED`, `UNREADABLE`, `MALFORMED_JSON`, `JSON_NON_OBJECT`, or
`JSON_OBJECT`. It also reports whether PID and launch-reservation *files*
exist, without trusting their contents. It creates no files, launches no
processes, changes no schedules or HALT latch, and does not contact financial
providers.

`JSON_OBJECT` means only that the bounded bytes decode to a JSON object. It
does **not** mean the desktop supervisor accepts the fields, the process
is alive, the singleton is owned, or a recovery operation is permitted.
The output deliberately fixes `live_singleton_owner=UNVERIFIED` and
`recovery_authorized=false` for every classification.

The `heartbeat.path_custody` field distinguishes the read mechanism:

- `NOFOLLOW_COMPONENT_HANDLES`: on supported POSIX systems, every directory
  component and the final file is opened through a directory descriptor with
  `O_NOFOLLOW`; symlinked parents and final-path swaps are rejected.
- `BEST_EFFORT_UNVERIFIED`: on Windows and other platforms without that
  handle mechanism, symlink and (where supported) junction metadata are
  checked before opening. **A subsequent reparse-point swap is not ruled
  out.** The classification must never be used as a security or recovery
  grant, even when it returns `JSON_OBJECT` or `ZERO_FILLED`.

`SYMLINK_UNTRUSTED` is an intentionally conservative rejection and can also
cover a non-directory path component. Related PID and reservation hints are
reported `null` when the heartbeat path itself is rejected.

## Incident reproduction

The November 5 inspection of Lappy's installed Portal source found a
17,978-byte all-zero heartbeat and the supervisor returned
`BLOCKED / heartbeat_invalid`. The source-level regression reproduces this
input, including a stale PID hint, and asserts that `ensure_started()` does
not invoke its launcher. **The origin of the zeroed bytes is unknown.**

## Recovery boundary

Neither a zero-filled heartbeat nor dead PID and launch-reservation hints
authorize renaming, deleting, rewriting, or ignoring those files. The host
owner must independently prove that no live OS-owned singleton can still
write them, preserve the evidence, and obtain the applicable host-recovery
authority before a supervised restart. A fresh heartbeat plus IPC readback
is required after recovery; a source-only test is not runtime qualification.
