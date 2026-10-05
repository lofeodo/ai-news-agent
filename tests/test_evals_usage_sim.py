from evals.run_usage_drift_sim import flag_rate


def test_quiet_weeks_rarely_flag_and_big_jumps_always_do():
    assert flag_rate(1_000_000, 0.05, 50, seed=1) == 0
    assert flag_rate(1_000_000, 0.05, 50, seed=1, jump=1.0) == 50


def test_simulation_is_seeded():
    assert flag_rate(1_000_000, 0.3, 40, seed=7) == flag_rate(1_000_000, 0.3, 40, seed=7)
