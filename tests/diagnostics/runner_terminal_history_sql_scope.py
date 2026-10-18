"""Executable SQLite-level falsifier for Runner's terminal-history candidate scan.
Source-query experiment only; NOT the live Runner or native integration suite.
"""
import sqlite3
from tempfile import TemporaryDirectory
from pathlib import Path

TERMINAL = ("COMPLETE", "FAILED_DETERMINISTIC", "SUPERSEDED")
OLD_SQL = """SELECT DISTINCT r.work_fingerprint,l.holder FROM recursive_work_state r
    LEFT JOIN leases l ON l.work_fingerprint=r.work_fingerprint
    WHERE r.work_fingerprint<>?
      AND r.status NOT IN ('COMPLETE','FAILED_DETERMINISTIC','SUPERSEDED')"""
NEW_SQL = """SELECT DISTINCT r.work_fingerprint,l.holder,r.status FROM recursive_work_state r
    LEFT JOIN leases l ON l.work_fingerprint=r.work_fingerprint
    WHERE r.work_fingerprint<>?"""


def run() -> None:
    for status in TERMINAL:
        with TemporaryDirectory(prefix="portal-terminal-history-sql-") as temp:
            con = sqlite3.connect(Path(temp) / "runner.sqlite3")
            try:
                con.executescript("""CREATE TABLE recursive_work_state(work_fingerprint TEXT,status TEXT);
                    CREATE TABLE leases(work_fingerprint TEXT,holder TEXT);
                    CREATE TABLE execution_attempts(work_fingerprint TEXT,fencing_token INTEGER);""")
                con.execute("INSERT INTO recursive_work_state VALUES(?,?)", ("A", status))
                con.execute("INSERT INTO leases VALUES(?,?)", ("A", None))
                con.execute("INSERT INTO execution_attempts VALUES(?,?)", ("A", 1))
                old = con.execute(OLD_SQL, ("B",)).fetchall()
                current = con.execute(NEW_SQL, ("B",)).fetchall()
                unresolved = con.execute(
                    "SELECT EXISTS(SELECT 1 FROM execution_attempts WHERE work_fingerprint=?)",
                    ("A",),
                ).fetchone()[0]
                keys = {"A": {"K1", "K2"}, "B": {"K2"}, "C": {"K3"}}
                for candidate, expected in (("B", True), ("C", False)):
                    blocked = any(bool(keys[owner] & keys[candidate]) and unresolved
                                  for owner, _, _ in current)
                    assert blocked is expected, (status, candidate, blocked)
                assert old == [] and current == [("A", None, status)]
                print(f"{status}: OLD_SCAN=[] NEW_SCAN=[A] overlap=BLOCKED disjoint=ALLOWED")
            finally:
                con.close()


if __name__ == "__main__":
    run()
