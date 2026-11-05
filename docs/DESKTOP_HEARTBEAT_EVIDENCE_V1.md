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
