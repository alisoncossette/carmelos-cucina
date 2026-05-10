"""Smoke test for the safety monitor. Run: python test_safety.py"""

from safety import SafetyMonitor


def assert_eq(a, b, msg=""):
    assert a == b, f"{msg}: expected {b}, got {a}"


def base_obs(**kw):
    return {"bread_visible": True, "bread_in_toaster": False,
            "lever_down": False, "toast_popped": False,
            "human_hand_visible": False, "workspace_clear": True,
            "confidence": "high", **kw}


def test_preflight_clean():
    s = SafetyMonitor()
    v = s.preflight("bread_to_toaster", base_obs())
    assert_eq(v, [], "clean preflight")
    assert not s.is_halted()


def test_preflight_blocks_hand():
    s = SafetyMonitor()
    v = s.preflight("bread_to_toaster", base_obs(human_hand_visible=True))
    assert_eq(len(v), 1)
    assert_eq(v[0].code, "HAND_IN_WORKSPACE")
    assert s.is_halted(), "halted after halt-severity violation"


def test_preflight_blocks_hot_toaster_insert():
    s = SafetyMonitor()
    v = s.preflight("bread_to_toaster", base_obs(lever_down=True))
    codes = [x.code for x in v]
    assert "HOT_TOASTER_BREAD_INSERT" in codes


def test_preflight_blocks_press_without_bread():
    s = SafetyMonitor()
    v = s.preflight("lever_down", base_obs(bread_in_toaster=False))
    codes = [x.code for x in v]
    assert "PRESS_WITHOUT_BREAD" in codes


def test_runtime_estop_on_hand_during_skill():
    s = SafetyMonitor()
    v = s.runtime(base_obs(human_hand_visible=True), current_skill="bread_to_toaster")
    assert any(x.code == "HAND_DURING_SKILL" and x.severity == "estop" for x in v)
    assert s.is_halted()


def test_runtime_estop_on_lever_down_during_insert():
    s = SafetyMonitor()
    v = s.runtime(base_obs(lever_down=True), current_skill="bread_to_toaster")
    assert any(x.code == "LEVER_DOWN_DURING_INSERT" and x.severity == "estop" for x in v)


def test_runtime_halts_on_blind_streak():
    s = SafetyMonitor(low_confidence_limit=3)
    for _ in range(2):
        v = s.runtime(base_obs(confidence="low"), current_skill=None)
        assert not any(x.code == "VLM_BLIND" for x in v)
    v = s.runtime(base_obs(confidence="low"), current_skill=None)
    assert any(x.code == "VLM_BLIND" for x in v)
    assert s.is_halted()


def test_reset_clears_halt():
    s = SafetyMonitor()
    s.preflight("bread_to_toaster", base_obs(human_hand_visible=True))
    assert s.is_halted()
    s.reset()
    assert not s.is_halted()
    # log preserved
    assert len(s.log) >= 1


if __name__ == "__main__":
    test_preflight_clean()
    test_preflight_blocks_hand()
    test_preflight_blocks_hot_toaster_insert()
    test_preflight_blocks_press_without_bread()
    test_runtime_estop_on_hand_during_skill()
    test_runtime_estop_on_lever_down_during_insert()
    test_runtime_halts_on_blind_streak()
    test_reset_clears_halt()
    print("OK")
