"""Resource regressions must fail even if a process finishes successfully."""

import pytest

from scripts.check_runtime_soak import LIMITS, PHASES, sample_errors


def samples():
    return [dict.fromkeys((*LIMITS, "retained_clients", "owned_tasks", "listeners", "open_clients"), 0)
            | {"phase": phase} for phase in PHASES for _ in range(3)]


def test_stable_measurements_pass_and_missing_phases_fail():
    assert sample_errors(samples()) == []
    assert sample_errors([])


@pytest.mark.parametrize("metric", list(LIMITS))
def test_peak_resource_growth_cannot_hide_behind_a_final_recovery(metric):
    data = samples()
    data[1][metric] = LIMITS[metric] + 1
    assert sample_errors(data)


@pytest.mark.parametrize("metric", ["retained_clients", "owned_tasks", "listeners", "open_clients"])
def test_unload_cannot_leave_resources_alive(metric):
    data = samples()
    data[-1][metric] = 1
    assert sample_errors(data)


def test_unmeasured_resource_is_not_zero():
    data = samples()
    data[0]["fds"] = None
    assert sample_errors(data)
