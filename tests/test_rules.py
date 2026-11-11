import pytest

from astra.roles.rules import (
    Membership,
    PermissionDenied,
    Rank,
    check_can_accept,
    check_can_remove,
    check_rank_change,
    desired_role_ids,
    managed_role_ids,
    role_diff,
)

M, E, C, L = Rank.MEMBER, Rank.ELDER, Rank.CO_LEADER, Rank.LEADER

# --- promote / demote -------------------------------------------------------

ALLOWED = [
    # actor, from, to
    (C, M, E),  # co-leader promotes member -> elder
    (C, E, C),  # co-leader promotes elder -> co-leader
    (C, E, M),  # co-leader demotes elder -> member
    (L, M, E),
    (L, E, C),
    (L, C, L),  # leader promotes co-leader -> leader
    (L, C, E),  # leader demotes co-leader
    (L, E, M),
]

DENIED = [
    (None, M, E),  # not in this clan
    (M, M, E),     # members can't promote
    (E, M, E),     # elders can't promote
    (C, C, E),     # co-leader can't demote another co-leader
    (C, C, L),     # co-leader can't promote to leader
    (L, L, C),     # only admins demote a leader
    (L, M, C),     # skipping a rank
]


@pytest.mark.parametrize("actor,frm,to", ALLOWED)
def test_allowed_rank_changes(actor, frm, to):
    check_rank_change(actor, frm, to)  # no exception


@pytest.mark.parametrize("actor,frm,to", DENIED)
def test_denied_rank_changes(actor, frm, to):
    with pytest.raises(PermissionDenied):
        check_rank_change(actor, frm, to)


def test_admin_can_demote_leader():
    check_rank_change(None, L, C, actor_is_admin=True)


def test_admin_can_bootstrap_themselves():
    check_rank_change(None, C, L, actor_is_target=True, actor_is_admin=True)
    check_can_accept(None, actor_is_admin=True)
    check_can_remove(None, L, actor_is_admin=True)


def test_admin_still_moves_one_step():
    with pytest.raises(PermissionDenied):
        check_rank_change(None, M, L, actor_is_admin=True)


def test_cannot_change_own_rank():
    with pytest.raises(PermissionDenied):
        check_rank_change(L, C, L, actor_is_target=True)


# --- accept / remove --------------------------------------------------------

def test_accept_requires_co_leader():
    check_can_accept(C)
    check_can_accept(L)
    for r in (None, M, E):
        with pytest.raises(PermissionDenied):
            check_can_accept(r)


@pytest.mark.parametrize("actor,target,ok", [
    (C, M, True), (C, E, True), (C, C, False), (C, L, False),
    (L, C, True), (L, L, False), (E, M, False),
])
def test_remove(actor, target, ok):
    if ok:
        check_can_remove(actor, target)
    else:
        with pytest.raises(PermissionDenied):
            check_can_remove(actor, target)


# --- role sync --------------------------------------------------------------

LEADER_ROLE = 900
ROLE_MAP = {
    "#PR":  {M: 101, E: 102, C: 103, L: LEADER_ROLE},
    "#DOP": {M: 201, E: 202, C: 203, L: LEADER_ROLE},
}
MANAGED = managed_role_ids(ROLE_MAP)
BOOSTER = 555  # a role the bot must never touch


def test_roles_stack_within_a_clan():
    assert desired_role_ids([Membership("#PR", M)], ROLE_MAP) == {101}
    assert desired_role_ids([Membership("#PR", E)], ROLE_MAP) == {101, 102}
    assert desired_role_ids([Membership("#PR", C)], ROLE_MAP) == {101, 102, 103}
    assert desired_role_ids([Membership("#PR", L)], ROLE_MAP) == {101, 102, 103, LEADER_ROLE}


def test_desired_roles_union_across_clans():
    ms = [Membership("#PR", L), Membership("#DOP", M)]
    assert desired_role_ids(ms, ROLE_MAP) == {101, 102, 103, LEADER_ROLE, 201}


def test_shared_leader_role_kept_while_leading_any_clan():
    # Leads both, then steps down in PR: still a leader via DOP.
    ms = [Membership("#PR", C), Membership("#DOP", L)]
    assert LEADER_ROLE in desired_role_ids(ms, ROLE_MAP)


def test_promotion_adds_next_rank_role():
    current = {101, BOOSTER}                      # PR Member + booster
    desired = desired_role_ids([Membership("#PR", E)], ROLE_MAP)
    to_add, to_remove = role_diff(desired, current, MANAGED)
    assert to_add == {102}
    assert to_remove == set()                     # keeps PR Member, booster untouched


def test_demotion_removes_only_higher_rank_roles():
    current = {101, 102, 103, BOOSTER}            # PR Co-leader
    desired = desired_role_ids([Membership("#PR", E)], ROLE_MAP)
    to_add, to_remove = role_diff(desired, current, MANAGED)
    assert to_add == set() and to_remove == {103}


def test_leaving_last_clan_removes_all_managed_roles():
    current = {201, 202, BOOSTER}
    to_add, to_remove = role_diff(set(), current, MANAGED)
    assert to_add == set() and to_remove == {201, 202}


def test_sync_is_idempotent():
    desired = {101, 102, 201}
    to_add, to_remove = role_diff(desired, desired | {BOOSTER}, MANAGED)
    assert to_add == set() and to_remove == set()


def test_unknown_clan_is_ignored():
    assert desired_role_ids([Membership("#GONE", M)], ROLE_MAP) == set()


def test_common_role_only_with_a_configured_clan():
    assert desired_role_ids([Membership("#PR", M)], ROLE_MAP, common_role_id=777) == {101, 777}
    assert desired_role_ids([], ROLE_MAP, common_role_id=777) == set()
    assert desired_role_ids([Membership("#GONE", M)], ROLE_MAP, common_role_id=777) == set()
