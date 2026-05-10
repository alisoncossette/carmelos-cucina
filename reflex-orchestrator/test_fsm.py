"""Smoke test for the FSM debouncer. Run with: python test_fsm.py"""

from fsm import FSM, ActionKind, State


def assert_eq(a, b, msg=""):
    assert a == b, f"{msg}: expected {b}, got {a}"


def make_obs(**kw):
    return {"bread_visible": False, "bread_in_toaster": False,
            "lever_down": False, "toast_popped": False, **kw}


def test_full_arc():
    fsm = FSM(debounce_ticks=2)
    skill = False

    # IDLE → bread appears, debounces, dispatches Policy A
    fsm.tick(make_obs(bread_visible=True), skill)
    a = fsm.tick(make_obs(bread_visible=True), skill)
    assert_eq(fsm.state, State.PLACING, "after debounce")
    assert_eq(a.kind, ActionKind.DISPATCH, "should dispatch")
    assert_eq(a.skill, "bread_to_toaster", "policy A")

    # Skill in flight → WAIT
    skill = True
    a = fsm.tick(make_obs(bread_visible=True), skill)
    assert_eq(a.kind, ActionKind.WAIT, "skill running")

    # Skill ends + bread now in toaster → PLACED → PRESSING
    skill = False
    fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True), skill)
    a = fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True), skill)
    assert_eq(fsm.state, State.PLACED, "placed")
    fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True), skill)
    a = fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True), skill)
    assert_eq(fsm.state, State.PRESSING, "pressing")
    assert_eq(a.skill, "lever_down", "policy B")

    # Lever down → TOASTING
    skill = False
    fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True, lever_down=True), skill)
    fsm.tick(make_obs(bread_visible=True, bread_in_toaster=True, lever_down=True), skill)
    assert_eq(fsm.state, State.TOASTING, "toasting")

    # Pop → DONE
    fsm.tick(make_obs(toast_popped=True), skill)
    a = fsm.tick(make_obs(toast_popped=True), skill)
    assert_eq(fsm.state, State.DONE, "done")
    assert_eq(a.kind, ActionKind.DONE, "DONE action")


def test_hallucinated_frame_does_not_fire():
    fsm = FSM(debounce_ticks=3)
    # Three IDLE ticks
    for _ in range(3):
        a = fsm.tick(make_obs(), skill_running=False)
        assert_eq(fsm.state, State.IDLE, "idle stays")
        assert_eq(a.kind, ActionKind.WAIT, "no dispatch")

    # One hallucinated bread frame
    a = fsm.tick(make_obs(bread_visible=True), skill_running=False)
    assert_eq(fsm.state, State.IDLE, "still idle after one frame")
    assert_eq(a.kind, ActionKind.WAIT, "still waiting")

    # Goes back to nothing — candidate resets, no dispatch
    a = fsm.tick(make_obs(), skill_running=False)
    assert_eq(fsm.state, State.IDLE, "still idle")
    assert_eq(a.kind, ActionKind.WAIT, "still waiting")


if __name__ == "__main__":
    test_full_arc()
    test_hallucinated_frame_does_not_fire()
    print("OK")
