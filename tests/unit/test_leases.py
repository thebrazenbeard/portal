from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from runner.leases import InMemoryLeaseStore


def test_two_simultaneous_claims_yield_one_owner():
    store = InMemoryLeaseStore()
    barrier = Barrier(2)

    def claim(holder):
        barrier.wait()
        return store.claim("fp-1", holder=holder, now=100.0, ttl=30.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("a", "b")))

    leases = [item for item in results if item is not None]
    assert len(leases) == 1
    assert leases[0].fencing_token == 1


def test_nonexpired_lease_cannot_be_stolen():
    store = InMemoryLeaseStore()
    first = store.claim("fp-1", holder="a", now=100.0, ttl=30.0)
    assert first is not None
    assert store.claim("fp-1", holder="b", now=120.0, ttl=30.0) is None


def test_expired_lease_reclaims_with_higher_fence():
    store = InMemoryLeaseStore()
    first = store.claim("fp-1", holder="a", now=100.0, ttl=10.0)
    second = store.claim("fp-1", holder="b", now=111.0, ttl=10.0)

    assert first is not None and second is not None
    assert second.fencing_token > first.fencing_token
    assert second.holder == "b"


def test_stale_fence_cannot_complete_or_release_reclaimed_work():
    store = InMemoryLeaseStore()
    first = store.claim("fp-1", holder="a", now=100.0, ttl=10.0)
    second = store.claim("fp-1", holder="b", now=111.0, ttl=20.0)

    assert first is not None and second is not None
    assert store.complete(first, now=112.0) is False
    assert store.release(first, now=112.0) is False
    assert store.complete(second, now=112.0) is True


def test_heartbeat_extends_only_current_unexpired_fenced_lease():
    store = InMemoryLeaseStore()
    first = store.claim("fp-1", holder="a", now=100.0, ttl=10.0)
    assert first is not None

    extended = store.heartbeat(first, now=105.0, ttl=20.0)
    assert extended is not None
    assert extended.expires_at == 125.0

    reclaimed = store.claim("fp-1", holder="b", now=126.0, ttl=20.0)
    assert reclaimed is not None
    assert store.heartbeat(extended, now=127.0, ttl=20.0) is None


def test_completed_fingerprint_is_not_claimed_again():
    store = InMemoryLeaseStore()
    lease = store.claim("fp-1", holder="a", now=100.0, ttl=20.0)
    assert lease is not None
    assert store.complete(lease, now=101.0) is True
    assert store.claim("fp-1", holder="b", now=200.0, ttl=20.0) is None
