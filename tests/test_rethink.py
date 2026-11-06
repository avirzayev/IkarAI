from datetime import datetime, timedelta, timezone

import metrics
import rethink

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _todays_state():
    """The real 2026-11-06 empire, trimmed to the fields metrics reads."""
    def city(name, pop, max_pop, wood, wine, marble, crystal, sulfur, cap, prod):
        return {
            "name": name, "population": pop, "max_population": max_pop,
            "resources": {"wood": wood, "wine": wine, "marble": marble, "crystal": crystal, "sulfur": sulfur},
            "max_resources": cap,
            "production_per_hour": {"wood": prod, "tradegood": 100},
        }
    return {
        "gold": 320848, "gold_per_hour": 2897, "transporters": {"free": 0, "max": 12},
        "cities": [
            city("Kernel", 868, 868, 971774, 152410, 6, 180, 669, 973693, 2192),
            city("Daemon", 472, 1026, 3493, 4, 716356, 0, 345, 881684, 366),
            city("Syscall", 329, 722, 207195, 16, 12, 232963, 2900, 785056, 370),
            city("Async", 319, 630, 126345, 64, 49, 47, 150710, 785056, 224),
        ],
    }


def _healthy_state(growth=1.0):
    def city(name):
        return {
            "name": name,
            "population": 800 * growth,
            "max_population": 1000,
            "resources": {"wood": 50000, "marble": 50000},
            "max_resources": 500000,
            "production_per_hour": {"wood": 1000 * growth, "tradegood": 500 * growth},
        }
    return {"gold": 1000, "gold_per_hour": 10, "transporters": {"max": 12}, "cities": [city("A"), city("B")]}


def _series(state_for_hours_ago, hours=60, step=6, wake=None):
    lines = []
    for ago in range(hours, -1, -step):
        moment = NOW - timedelta(hours=ago)
        lines.append(metrics.build_line(state_for_hours_ago(ago), wake or [], moment))
    return lines


def _fresh_session(tmp_path):
    (tmp_path / "last_rethink.txt").write_text(_stamp(NOW - timedelta(hours=1)))
    (tmp_path / "last_model_success.txt").write_text(_stamp(NOW - timedelta(hours=1)))
    return tmp_path


def test_replay_of_todays_state_fires_the_three_expected_triggers(tmp_path):
    reasons = rethink.evaluate(_series(lambda ago: _todays_state()), _fresh_session(tmp_path), NOW)
    joined = "\n".join(reasons)
    assert "stranded stock: marble in Daemon" in joined
    assert "wasted cap: Kernel wood" in joined
    assert "empty city: Daemon" in joined
    assert "empty city: Kernel" not in joined


def test_healthy_growing_series_fires_only_daily(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_rethink.txt").write_text(_stamp(NOW - timedelta(hours=25)))
    lines = _series(lambda ago: _healthy_state(growth=1.0 - ago * 0.001))
    assert rethink.evaluate(lines, session, NOW) == ["daily review due"]


def test_daily_fires_when_never_run(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_rethink.txt").unlink()
    assert "daily review due" in rethink.evaluate([], session, NOW)


def test_stagnant_needs_48_hours_of_data(tmp_path):
    short = _series(lambda ago: _healthy_state(), hours=47)
    assert not any("stagnant" in r for r in rethink.evaluate(short, _fresh_session(tmp_path), NOW))


def test_stagnant_fires_on_flat_series(tmp_path):
    lines = _series(lambda ago: _healthy_state(), hours=60)
    assert any("stagnant" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stagnant_does_not_fire_if_either_measure_grew(tmp_path):
    def state(ago):
        result = _healthy_state()
        if ago == 0:
            result["cities"][0]["production_per_hour"]["wood"] = 2000
        return result
    lines = _series(state, hours=60)
    assert not any("stagnant" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_fires_on_six_identical_wakes(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=30, wake=["heavy: city A idle"])
    assert any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_ignores_ticks_without_wakes_and_needs_six(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=24, wake=["heavy: city A idle"])
    assert len(lines) == 5
    assert not any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_does_not_fire_when_reasons_differ(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=30, wake=["heavy: city A idle"])
    lines[-1]["wake"] = ["heavy: city B idle"]
    assert not any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stranded_needs_a_fleet(tmp_path):
    state = _todays_state()
    state["transporters"]["max"] = 0
    lines = [metrics.build_line(state, [], NOW)]
    assert not any("stranded" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_wasted_cap_needs_24_continuous_hours(tmp_path):
    def state(ago):
        result = _healthy_state(growth=1 - ago * 0.001)
        if ago <= 12:
            result["cities"][0]["resources"]["wood"] = 499000
        return result
    lines = _series(state, hours=60)
    assert not any("wasted cap" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_empty_city_needs_history_covering_24_hours(tmp_path):
    lines = _series(lambda ago: _todays_state(), hours=23, step=6)
    assert not any("empty city" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_outage_fires_after_12_hours_without_success(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_model_success.txt").write_text(_stamp(NOW - timedelta(hours=13)))
    assert any("return from outage" in r for r in rethink.evaluate([], session, NOW))


def test_outage_stays_quiet_when_success_file_is_missing(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_model_success.txt").unlink()
    assert not any("outage" in r for r in rethink.evaluate([], session, NOW))


def test_requested_fires_with_reason_text(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "rethink_request.txt").write_text("plan looks wrong\n")
    assert "requested: plan looks wrong" in rethink.evaluate([], session, NOW)


def test_mode_defaults_to_log_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("IKARAI_RETHINK_MODE", raising=False)
    assert rethink.mode() == "log"
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "LIVE")
    assert rethink.mode() == "live"
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "banana")
    assert rethink.mode() == "log"
