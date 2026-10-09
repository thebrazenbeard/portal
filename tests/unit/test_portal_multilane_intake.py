from pathlib import Path
from portal.registered_checkout_intake import RegisteredCheckout
from portal.discovery import RepositoryInventoryItem
from portal.multilane_intake import plan_local_portfolio_lanes

def case(names, *, private=(), archived=(), stale=(), wrong_ref=()):
    local = tuple(RegisteredCheckout(
        repository=f"thebrazenbeard/{name}", subject_id=name,
        checkout=Path("C:/checkout") / name, head="a" * 40,
        source_ref="topic" if name in wrong_ref else "main",
        already_observed=False) for name in names)
    inventory = tuple(RepositoryInventoryItem(
        name=name, full_name=f"thebrazenbeard/{name}",
        private=name in private, archived=name in archived,
        default_branch="main") for name in names)
    remote = {f"thebrazenbeard/{name}":
              "b" * 40 if name in stale else "a" * 40 for name in names}
    return local, inventory, remote

def test_single_slot_has_twelve_explicit_deferrals():
    local, inventory, remote = case(tuple(f"repo{i:02}" for i in range(13)))
    rows = plan_local_portfolio_lanes(
        registered=local, owned_inventory=inventory, remote_heads=remote,
        inventory_authenticated=True, observed_subject_ids=set(),
        delegated_subject_ids=set(), capacity=1)
    assert len(rows) == 13
    assert sum(x.disposition=="CANDIDATE_FOR_SCHEDULER" for x in rows)==1
    assert sum(x.disposition=="DEFERRED_CAPACITY" for x in rows)==12
def test_held_and_delegated_do_not_requeue():
    local, inventory, remote = case(("firesafe", "blotter", "meso-crct"))
    rows = plan_local_portfolio_lanes(
        registered=local, owned_inventory=inventory, remote_heads=remote,
        inventory_authenticated=True, observed_subject_ids={"firesafe"},
        delegated_subject_ids={"blotter"}, capacity=3)
    assert [(x.subject_id, x.disposition) for x in rows] == [
        ("blotter", "HOLD_DELEGATED"),
        ("firesafe", "HOLD_PREVIOUSLY_OBSERVED"),
        ("meso-crct", "CANDIDATE_FOR_SCHEDULER")]

def test_stale_archived_wrong_ref_private_are_held():
    local, inventory, remote = case(
        ("archived", "old", "topic", "private"),
        private={"private"}, archived={"archived"},
        stale={"old"}, wrong_ref={"topic"})
    rows = plan_local_portfolio_lanes(
        registered=local, owned_inventory=inventory, remote_heads=remote,
        inventory_authenticated=False, observed_subject_ids=set(),
        delegated_subject_ids=set(), capacity=8)
    assert {x.subject_id:x.disposition for x in rows} == {
        "archived":"HOLD_ARCHIVED", "old":"HOLD_STALE_HEAD",
        "topic":"HOLD_WRONG_BRANCH", "private":"HOLD_UNAUTHENTICATED_PRIVATE"}
def test_local_origin_is_not_proof_of_owned_membership():
    local, inventory, remote = case(("fake",))
    rows = plan_local_portfolio_lanes(
        registered=local, owned_inventory=(), remote_heads=remote,
        inventory_authenticated=True, observed_subject_ids=set(),
        delegated_subject_ids=set(), capacity=1)
    assert rows[0].disposition == "HOLD_NOT_OWNED"

def test_no_capacity_and_missing_remote_head():
    local, inventory, remote = case(("one", "two"))
    rows = plan_local_portfolio_lanes(
        registered=local, owned_inventory=inventory,
        remote_heads={"thebrazenbeard/one":"a"*40},
        inventory_authenticated=True, observed_subject_ids=set(),
        delegated_subject_ids=set(), capacity=0)
    assert [(x.subject_id, x.disposition) for x in rows] == [
        ("one","DEFERRED_CAPACITY"), ("two","HOLD_REMOTE_UNVERIFIED")]
def test_branch_case_is_not_normalized_across_git_refs():
    local, inventory, remote = case(("sensitive",))
    original = local[0]
    altered = RegisteredCheckout(
        repository=original.repository, subject_id=original.subject_id,
        checkout=original.checkout, head=original.head, source_ref="Main",
        already_observed=False)
    rows = plan_local_portfolio_lanes(
        registered=(altered,), owned_inventory=inventory, remote_heads=remote,
        inventory_authenticated=True, observed_subject_ids=set(),
        delegated_subject_ids=set(), capacity=1)
    assert rows[0].disposition == "HOLD_WRONG_BRANCH"
