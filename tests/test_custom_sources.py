"""Legacy custom-source digit-slot decoding (no Earth Engine)."""

import pytest

from deforestation_alerts.scripts.custom_sources import slot_value


def test_slot_values_follow_the_ten_thousands_digit():
    assert [slot_value(s) for s in range(5)] == [10**4, 10**5, 10**6, 10**7, 10**8]


@pytest.mark.parametrize("bad", [-1, 5, 1.0, None])
def test_slot_value_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        slot_value(bad)
