"""round_to_step must floor in decimal, not float.

``math.floor(85721.9 / 0.1) * 0.1`` is 85721.8: the old float version dropped a whole tick (price) or lot
(quantity) off values that were already aligned -- found on the first real-money stop of the crypto strategy
on 2026-10-17.  These tests compare against exact Decimal arithmetic over whole ranges, not a few examples.
"""
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pytest

from binance_trading_toolkit import round_to_step, round_up_to_step


def _exact(value: str, step: str) -> float:
    return float((Decimal(value) / Decimal(step)).to_integral_value(rounding=ROUND_FLOOR) * Decimal(step))


def test_the_value_that_was_wrong_in_production():
    assert round_to_step(85721.9, 0.1) == 85721.9
    assert round_to_step(81639.9, 0.1) == 81639.9
    assert round_to_step(0.043, 0.001) == 0.043
    assert round_to_step(0.0589, 0.0001) == 0.0589


def test_every_aligned_btc_price_survives_a_tick_of_0_1():
    for k in range(300000, 1500000):          # 30,000.0 .. 150,000.0 in ticks of 0.1
        price = float(f"{k / 10:.1f}")
        assert round_to_step(price, 0.1) == price, price


def test_every_aligned_quantity_survives_steps_of_0_001_and_0_0001():
    for k in range(1, 20000):
        qty = float(f"{k / 1000:.3f}")
        assert round_to_step(qty, 0.001) == qty, qty
    for k in range(1, 20000):
        qty = float(f"{k / 10000:.4f}")
        assert round_to_step(qty, 0.0001) == qty, qty


@pytest.mark.parametrize("value,step", [("85721.95", "0.1"), ("85721.99", "0.1"), ("0.0439", "0.001"),
                                        ("1.23456", "0.001"), ("0.00109", "0.0001"), ("5.5", "1"), ("6.999", "1")])
def test_unaligned_values_floor_down_to_the_step(value, step):
    assert round_to_step(float(value), float(step)) == _exact(value, step)
    assert round_to_step(float(value), float(step)) <= float(value)


def test_never_rounds_up_and_never_drops_more_than_one_step():
    step = 0.001
    for k in range(1, 5000):
        qty = k / 1000 + 0.0004                  # always strictly between two lots
        out = round_to_step(qty, step)
        assert out <= qty and qty - out < step + 1e-12


def test_no_step_passes_through():
    assert round_to_step(1.2345, None) == 1.2345
    assert round_to_step(1.2345, 0) == 1.2345
    assert round_to_step(1.2345, -0.1) == 1.2345


@pytest.mark.parametrize("quantity,step", [(float("nan"), 0.1), (float("inf"), 0.1), (1.0, float("inf")), (1.0, float("nan"))])
def test_non_finite_input_is_an_error_not_a_silent_nan(quantity, step):
    with pytest.raises(ValueError):
        round_to_step(quantity, step)


# --- round_up_to_step: the same bug in the other direction (gate1 sized its test order with math.ceil) ----------------

def _exact_up(value: str, step: str) -> float:
    return float((Decimal(value) / Decimal(step)).to_integral_value(rounding=ROUND_CEILING) * Decimal(step))


def test_an_aligned_quantity_is_not_rounded_up_a_lot():
    # math.ceil(4.001 / 0.001) * 0.001 is 4.002: one whole lot too many
    assert round_up_to_step(4.001, 0.001) == 4.001
    assert round_up_to_step(4.009, 0.001) == 4.009


def test_every_aligned_quantity_survives_round_up_for_steps_of_0_001_and_0_0001():
    for k in range(1, 20000):
        qty = float(f"{k / 1000:.3f}")
        assert round_up_to_step(qty, 0.001) == qty, qty
    for k in range(1, 20000):
        qty = float(f"{k / 10000:.4f}")
        assert round_up_to_step(qty, 0.0001) == qty, qty


@pytest.mark.parametrize("value,step", [("85721.95", "0.1"), ("0.0439", "0.001"), ("1.23456", "0.001"),
                                        ("0.00109", "0.0001"), ("5.5", "1"), ("6.001", "1")])
def test_unaligned_values_round_up_to_the_step(value, step):
    assert round_up_to_step(float(value), float(step)) == _exact_up(value, step)
    assert round_up_to_step(float(value), float(step)) >= float(value)


def test_round_up_never_drops_and_never_adds_more_than_one_step():
    step = 0.001
    for k in range(1, 5000):
        qty = k / 1000 + 0.0004
        out = round_up_to_step(qty, step)
        assert out >= qty and out - qty < step + 1e-12


def test_round_up_without_a_step_passes_through_and_rejects_non_finite():
    assert round_up_to_step(1.2345, None) == 1.2345
    assert round_up_to_step(1.2345, 0) == 1.2345
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            round_up_to_step(bad, 0.1)
