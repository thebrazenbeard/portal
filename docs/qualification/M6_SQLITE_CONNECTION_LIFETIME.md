# M6 SQLite Connection Lifetime Qualification

Status: SOURCE_CREATED / WINDOWS_PASS / HOSTED_LINUX_PASS / LIVE_READ_PROOFS_PASS / INDEPENDENT_REREVIEW_PENDING

## Exact subjects

Predecessor:
`ef1f665ab16ef3a3426cec3f70900bdc6a98e420`

Successor branch:
`work/m6-sqlite-connection-lifetime`

Frozen RED:
`57e277c8fcf2997df652918f7021a6ff7c29bb60`

Repair head:
`82b1db10c7634aeff8b355bec81dfb0ee239c3bc`

## Defect

Several SQLite store methods used:

`with sqlite3.Connection(...) as conn:`

as if exiting that context closed the database connection.

Python's `sqlite3.Connection` context manager controls transaction
commit/rollback. It does not close the connection.

Linux generally permits unlinking an open SQLite file, so canonical hosted
tests masked the resource-lifetime defect. Windows refuses to remove the
temporary SQLite database while a connection remains open.

On exact predecessor `ef1f665...`, Windows Python 3.12 produced:
- **193 PASS / 5 FAIL** full suite;
- all five failures were `PermissionError [WinError 32]` deleting
  `state.sqlite3` after tests using context-managed budget/lease store paths.

A platform-independent frozen regression then directly observed the leaked
connection handles.

Exact RED `57e277c8...`:
- connection-lifetime regressions: **2 failed / 0 passed**.

## Repair

`runner/sqlite_state.py` now defines an explicit managed connection context:
- open with existing `_connect`;
- preserve the existing connection transaction context;
- always `close()` in `finally`.

All seven prior `with _connect(self.path) as conn:` call sites now use the
explicit closing context.

The existing manually managed transaction paths in `reserve()` and
`claim()` retain their explicit `finally: conn.close()` behavior.

## Local Windows qualification

Environment:
- Windows
- CPython 3.12.10

Focused:
- new connection lifetime regressions + existing SQLite state tests:
  **7/7 PASS**.

Full:
- **200/200 PASS**.
- compileall: PASS.
- git diff --check: PASS.

This specifically closes the five Windows cleanup failures reproduced on the
predecessor.

## Hosted qualification

GitHub Actions push run:
`35474072529`

Job:
`105980147360`

Exact head:
`82b1db10c7634aeff8b355bec81dfb0ee239c3bc`

Hosted Ubuntu / CPython 3.12:
- **200/200 PASS**;
- registry validation: PASS;
- live read-only GitHub backend smoke: PASS;
- M6 recursive restart proof: `COMPLETE`, 3 levels / 2 restart boundaries;
- M6 live HC -> Transcendence proof: `COMPLETE`;
- execution journal: `COMPLETE`;
- independently verified subjects: 2.

## Claim ceiling

This subject establishes explicit closure of context-managed SQLite connections
and cross-platform source/test behavior for the bounded Project Runner M6
candidate.

It does not:
- merge PR #2 or this successor;
- install/deploy Project Runner;
- authorize downstream mutation;
- authenticate reconciliation/provider evidence;
- mutate credentials/providers;
- grant protected-effect authority.

Fresh independent hostile rereview is required for this exact head.
