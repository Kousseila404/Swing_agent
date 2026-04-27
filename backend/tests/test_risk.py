"""Tests unitaires modules.risk — position sizing, drawdown, circuit breaker."""
from __future__ import annotations

import pytest

from modules.risk import (
    DrawdownCircuitBreaker,
    apply_adtv_cap,
    calculate_drawdown,
    calculate_position_size,
    check_sector_concentration,
    regime_adjusted_risk,
)


class TestPositionSize:
    def test_nominal_case(self):
        assert calculate_position_size(150.0, 147.0, 0.0025, 100_000) == 83

    def test_penny_stock(self):
        assert calculate_position_size(5.0, 4.5, 0.0025, 100_000) == 500

    def test_expensive_stock(self):
        assert calculate_position_size(1000.0, 990.0, 0.0025, 100_000) == 25

    def test_tight_sl_capped_at_equity(self):
        # SL à 1 cent → raw_size énorme, doit être cappé à equity/price = 1000
        assert calculate_position_size(100.0, 99.99, 0.0025, 100_000) == 1000

    def test_large_sl(self):
        assert calculate_position_size(200.0, 180.0, 0.0025, 100_000) == 12

    def test_minimum_size_is_one(self):
        # risk = 250$, distance = 999$ → raw = 0.25 → floor à 1
        assert calculate_position_size(1000.0, 1.0, 0.0025, 100_000) == 1

    def test_long_short_symmetric(self):
        long_size = calculate_position_size(150.0, 147.0, 0.0025, 100_000)
        short_size = calculate_position_size(147.0, 150.0, 0.0025, 100_000)
        assert long_size == short_size

    def test_sl_equals_entry_raises(self):
        with pytest.raises(ValueError, match="distance_sl"):
            calculate_position_size(150.0, 150.0, 0.0025, 100_000)

    def test_zero_equity_raises(self):
        with pytest.raises(ValueError, match="current_equity"):
            calculate_position_size(150.0, 147.0, 0.0025, 0)

    def test_negative_equity_raises(self):
        with pytest.raises(ValueError):
            calculate_position_size(150.0, 147.0, 0.0025, -1000)

    def test_risk_pct_out_of_bounds(self):
        with pytest.raises(ValueError, match="risk_pct"):
            calculate_position_size(150.0, 147.0, 0.0, 100_000)
        with pytest.raises(ValueError, match="risk_pct"):
            calculate_position_size(150.0, 147.0, 1.5, 100_000)

    def test_negative_entry_raises(self):
        with pytest.raises(ValueError, match="entry"):
            calculate_position_size(-10.0, -12.0, 0.0025, 100_000)


class TestDrawdown:
    def test_loss_4pct(self):
        assert calculate_drawdown(96_000, 100_000) == -4.0

    def test_gain_1_5pct(self):
        assert calculate_drawdown(101_500, 100_000) == 1.5

    def test_flat_is_zero(self):
        assert calculate_drawdown(100_000, 100_000) == 0.0

    def test_zero_starting_raises(self):
        with pytest.raises(ValueError):
            calculate_drawdown(100_000, 0)

    def test_negative_starting_raises(self):
        with pytest.raises(ValueError):
            calculate_drawdown(100_000, -1)


class TestDrawdownCircuitBreaker:
    def test_full_size_at_peak(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000)
        assert cb.get_size_multiplier(100_000) == 1.0
        assert not cb.is_paused()

    def test_reduced_size_at_minus_2pct(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000)
        assert cb.get_size_multiplier(98_000) == 0.75

    def test_half_size_at_minus_3pct(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000)
        assert cb.get_size_multiplier(97_000) == 0.5

    def test_pause_triggered_at_minus_4pct(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000, pause_days=5)
        mult = cb.get_size_multiplier(96_000)
        assert mult == 0.0
        assert cb.is_paused()

    def test_pause_decrements(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000, pause_days=2)
        cb.get_size_multiplier(96_000)  # déclenche pause
        assert cb.is_paused()
        cb.update(100_000)
        cb.update(100_000)
        assert not cb.is_paused()

    def test_peak_updates_on_new_high(self):
        cb = DrawdownCircuitBreaker(peak_equity=100_000)
        cb.update(105_000)
        assert cb.peak_equity == 105_000


class TestRegimeAdjustedRisk:
    def test_crash_panic_zeroes_risk(self):
        assert regime_adjusted_risk(0.0025, "CRASH_PANIC") == 0.0

    def test_bear_halves_risk(self):
        assert regime_adjusted_risk(0.01, "BEAR_MARKET") == 0.005

    def test_bull_calm_full_risk(self):
        assert regime_adjusted_risk(0.0025, "BULL_MARKET", vix=15.0) == 0.0025

    def test_bull_high_vix_scales_down(self):
        assert regime_adjusted_risk(0.01, "BULL_MARKET", vix=27.0) == 0.0075

    def test_bull_moderate_vix(self):
        assert regime_adjusted_risk(0.01, "BULL_MARKET", vix=22.0) == pytest.approx(0.009)


class TestAdtvCap:
    def test_no_cap_when_adtv_large(self):
        # ADTV 10M$ × 5% = 500k$ max, position 100 shares × 100$ = 10k$
        assert apply_adtv_cap(100, 100.0, 100_000) == 100

    def test_caps_when_exceeds(self):
        # ADTV 100k shares × 100$ = 10M$ × 5% = 500k$ / 100$ = 5000 shares cap
        assert apply_adtv_cap(10_000, 100.0, 100_000) == 5000

    def test_returns_input_if_zero_volume(self):
        assert apply_adtv_cap(100, 100.0, 0) == 100

    def test_returns_input_if_zero_size(self):
        assert apply_adtv_cap(0, 100.0, 1_000_000) == 0


class TestSectorConcentration:
    def test_empty_open_list_allows_entry(self):
        assert check_sector_concentration("NVDA", []) is True

    def test_unknown_ticker_fail_open(self):
        assert check_sector_concentration("ZZZFAKE", ["NVDA", "AMD"]) is True

    def test_blocks_when_sector_full(self):
        # NVDA et AMD sont TECH, max=2 → bloque un 3ème TECH
        assert check_sector_concentration("MU", ["NVDA", "AMD"]) is False

    def test_allows_different_sector(self):
        assert check_sector_concentration("XOM", ["NVDA", "AMD"]) is True

    def test_custom_max_per_sector(self):
        assert check_sector_concentration("MU", ["NVDA"], max_per_sector=1) is False
        assert check_sector_concentration("MU", ["NVDA"], max_per_sector=2) is True
