"""Tests modules/factor_grades.py."""
from __future__ import annotations

from modules.factor_grades import (
    compute_factor_grades,
    factor_grade,
    quant_rating_letter,
)


def test_factor_grade_mapping():
    assert factor_grade(95) == "A+"
    assert factor_grade(85) == "A"
    assert factor_grade(75) == "B+"
    assert factor_grade(65) == "B"
    assert factor_grade(55) == "C+"
    assert factor_grade(45) == "C"
    assert factor_grade(30) == "D"
    assert factor_grade(10) == "F"
    assert factor_grade(None) == "N/A"
    assert factor_grade(float("nan")) == "N/A"


def test_quant_rating_letter():
    assert quant_rating_letter(85) == "STRONG_BUY"
    assert quant_rating_letter(70) == "BUY"
    assert quant_rating_letter(50) == "HOLD"
    assert quant_rating_letter(35) == "SELL"
    assert quant_rating_letter(20) == "STRONG_SELL"
    assert quant_rating_letter(None) == "N/A"


def test_compute_factor_grades_keys():
    row = {
        "quality_score": 80, "value_score": 70, "risk_score": 65,
        "momentum_score": 90, "piotroski_score": 75, "growth_score": 60,
        "revisions_score": 55, "insider_score": 80, "sentiment_score": 50,
    }
    g = compute_factor_grades(row)
    expected = {"Quality", "Value", "Risk", "Momentum",
                "Piotroski", "Growth", "Revisions", "Insider", "Sentiment"}
    assert set(g.keys()) == expected
    assert g["Quality"]["grade"] == "A"
    assert g["Momentum"]["grade"] == "A+"
    assert g["Insider"]["grade"] == "A"


def test_compute_factor_grades_handles_missing():
    g = compute_factor_grades({})
    assert all(v["grade"] == "N/A" for v in g.values())
