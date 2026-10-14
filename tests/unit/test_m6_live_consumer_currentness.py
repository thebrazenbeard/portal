from dataclasses import replace

import pytest

from scripts import m6_hc_transcendence_live_proof as proof


class Reader:
    def __init__(self, observed):
        self.observed = observed
        self.requested = []

    def read(self, subject):
        self.requested.append(subject)
        return self.observed


def test_consumer_ref_is_read_live_and_bound_to_observed_exact_head():
    old = proof.TRANSCENDENCE_SUBJECT
    current = replace(old, commit="1" * 40)
    reader = Reader(current)
    selected = proof._current_transcendence_subject(reader)
    assert reader.requested == [old]
    assert selected == current
    assert selected.commit != old.commit


@pytest.mark.parametrize("observed", [None, replace(proof.TRANSCENDENCE_SUBJECT, commit=None),
    replace(proof.TRANSCENDENCE_SUBJECT, repository="thebrazenbeard/other"),
    replace(proof.TRANSCENDENCE_SUBJECT, ref="main")])
def test_consumer_live_currentness_fails_closed_when_missing_or_misbound(observed):
    with pytest.raises(RuntimeError, match="live Transcendence currentness"):
        proof._current_transcendence_subject(Reader(observed))
