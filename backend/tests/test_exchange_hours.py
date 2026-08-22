"""Tests unitaires — modules/exchange_hours.py (Upgrade 4, incrément 1)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from modules import exchange_hours as eh

NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")
HK = ZoneInfo("Asia/Hong_Kong")


class TestUS:
    def test_open_during_session(self):
        # Mardi 2026-08-18, 10h00 NY (été, DST actif)
        at = datetime(2026, 8, 18, 10, 0, tzinfo=NY)
        assert eh.is_open("US", at) is True

    def test_closed_before_open(self):
        at = datetime(2026, 8, 18, 9, 0, tzinfo=NY)
        assert eh.is_open("US", at) is False

    def test_closed_after_close(self):
        at = datetime(2026, 8, 18, 16, 30, tzinfo=NY)
        assert eh.is_open("US", at) is False

    def test_closed_weekend(self):
        # Samedi
        at = datetime(2026, 8, 22, 10, 0, tzinfo=NY)
        assert eh.is_open("US", at) is False

    def test_dst_winter_still_closed_offhours(self):
        # Janvier (heure d'hiver EST), même règle horaire locale 09:30-16:00
        at = datetime(2026, 1, 15, 8, 0, tzinfo=NY)
        assert eh.is_open("US", at) is False
        at_open = datetime(2026, 1, 15, 10, 0, tzinfo=NY)
        assert eh.is_open("US", at_open) is True


class TestEuronextParis:
    def test_open_during_session(self):
        at = datetime(2026, 8, 18, 11, 0, tzinfo=PARIS)
        assert eh.is_open("EURONEXT_PARIS", at) is True

    def test_closed_before_open(self):
        at = datetime(2026, 8, 18, 8, 0, tzinfo=PARIS)
        assert eh.is_open("EURONEXT_PARIS", at) is False

    def test_closed_after_close(self):
        at = datetime(2026, 8, 18, 18, 0, tzinfo=PARIS)
        assert eh.is_open("EURONEXT_PARIS", at) is False

    def test_hors_seance_us_matinee_paris_reproduit_bug_cnc(self):
        # Cas réel CNC : 09h50 heure de Paris = hors séance US (marché
        # cible = US, pas Euronext) — le fill CNC était hors séance US.
        at = datetime(2026, 8, 19, 9, 50, tzinfo=PARIS)
        assert eh.is_open("US", at) is False


class TestHKEX:
    def test_open_morning_session(self):
        at = datetime(2026, 8, 18, 10, 0, tzinfo=HK)
        assert eh.is_open("HKEX", at) is True

    def test_open_afternoon_session(self):
        at = datetime(2026, 8, 18, 14, 0, tzinfo=HK)
        assert eh.is_open("HKEX", at) is True

    def test_closed_during_lunch_break(self):
        # Cas limite explicite de la spec : 12h15 HKT doit être "fermé"
        # malgré l'heure diurne, à cause de la pause déjeuner.
        at = datetime(2026, 8, 18, 12, 15, tzinfo=HK)
        assert eh.is_open("HKEX", at) is False

    def test_closed_at_exact_lunch_boundary_start(self):
        at = datetime(2026, 8, 18, 12, 0, tzinfo=HK)
        assert eh.is_open("HKEX", at) is False

    def test_open_at_exact_afternoon_boundary_start(self):
        at = datetime(2026, 8, 18, 13, 0, tzinfo=HK)
        assert eh.is_open("HKEX", at) is True

    def test_closed_weekend(self):
        at = datetime(2026, 8, 22, 10, 0, tzinfo=HK)
        assert eh.is_open("HKEX", at) is False


class TestCrossTimezoneConversion:
    def test_paris_time_converted_to_hkex_local(self):
        # 04h00 heure de Paris (été) = 10h00 HKT (UTC+8, pas de DST HK)
        at = datetime(2026, 8, 18, 4, 0, tzinfo=PARIS)
        assert eh.is_open("HKEX", at) is True

    def test_utc_input_converted_correctly(self):
        from datetime import UTC
        # 2026-08-18 14:00 UTC = 10h00 NY (été, UTC-4)
        at = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        assert eh.is_open("US", at) is True


class TestErrorHandling:
    def test_naive_datetime_rejected(self):
        naive = datetime(2026, 8, 18, 10, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            eh.is_open("US", naive)

    def test_unknown_exchange_rejected(self):
        at = datetime(2026, 8, 18, 10, 0, tzinfo=NY)
        with pytest.raises(ValueError, match="inconnue"):
            eh.is_open("EURONEXT_AMSTERDAM", at)


def test_exchanges_constant_matches_sessions():
    assert eh.EXCHANGES == {"US", "EURONEXT_PARIS", "HKEX"}
