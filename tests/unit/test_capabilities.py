from runner.capabilities import narrow_capabilities


def test_child_capabilities_are_intersection_only():
    assert narrow_capabilities(
        parent_capabilities={"read", "analyze", "write_branch"},
        target_capabilities={"read", "analyze", "merge"},
    ) == ("analyze", "read")


def test_parent_cannot_delegate_capability_it_lacks():
    assert "merge" not in narrow_capabilities(
        parent_capabilities={"read", "analyze"},
        target_capabilities={"read", "analyze", "merge"},
    )


def test_target_policy_can_narrow_parent():
    assert narrow_capabilities(
        parent_capabilities={"read", "analyze", "write_branch"},
        target_capabilities={"read"},
    ) == ("read",)


def test_empty_intersection_is_explicit():
    assert narrow_capabilities(
        parent_capabilities={"analyze"},
        target_capabilities={"write_branch"},
    ) == ()
