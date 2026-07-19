"""
Regression tests for clean_sog (src/ais_shader/preprocessing.py).

Bug: a prior version thresholded any value >= 102.3 as invalid, assuming the
column was already in knots. Applied to a column still in raw AIS units
(0.1-knot steps, 0-1022), this wrongly NaNs legitimate speeds like 150
(actually 15.0 knots). The fix requires the caller to state which encoding
the column is in explicitly (raw_units=True/False), rather than guessing from
the data's magnitude.
"""
import math

import numpy as np
import pytest

from ais_shader.preprocessing import clean_sog, SOG_CAP_KNOTS


def test_raw_units_rescales_legitimate_high_speed():
    # 150 in raw 0.1-knot units is a legitimate 15.0 knots -- must not be NaN'd.
    result = clean_sog([150.0], raw_units=True)
    assert result[0] == pytest.approx(15.0)


def test_raw_units_not_available_sentinel_becomes_nan():
    result = clean_sog([1023.0], raw_units=True)
    assert math.isnan(result[0])


def test_raw_units_cap_sentinel():
    result = clean_sog([1022.0], raw_units=True)
    assert result[0] == pytest.approx(SOG_CAP_KNOTS)


def test_knots_units_legitimate_speed_untouched():
    result = clean_sog([46.5], raw_units=False)
    assert result[0] == pytest.approx(46.5)


def test_knots_units_not_available_sentinel_becomes_nan():
    result = clean_sog([102.3], raw_units=False)
    assert math.isnan(result[0])


def test_knots_units_cap_sentinel():
    result = clean_sog([102.2], raw_units=False)
    assert result[0] == pytest.approx(SOG_CAP_KNOTS)


def test_same_raw_value_interpreted_differently_by_flag():
    # The crux of the bug: identical input, different correct output,
    # depending only on the explicitly-declared unit -- never inferred.
    raw_interpreted = clean_sog([150.0], raw_units=True)
    knots_interpreted = clean_sog([150.0], raw_units=False)
    assert raw_interpreted[0] == pytest.approx(15.0)
    assert math.isnan(knots_interpreted[0])  # 150 knots is not a valid AIS SOG


def test_realistic_rws_column_already_in_knots():
    # Matches real brienenoord data: values up to 102.3, in 0.1-knot steps.
    values = [0.0, 5.7, 9.1, 46.2, 64.0, 102.2, 102.3]
    result = clean_sog(values, raw_units=False)
    np.testing.assert_allclose(result[:5], [0.0, 5.7, 9.1, 46.2, 64.0])
    assert result[5] == pytest.approx(SOG_CAP_KNOTS)
    assert math.isnan(result[6])


def test_warns_when_knots_assumption_rejects_too_many_values(caplog):
    # If raw_units=False is declared but most values are in raw-AIS range
    # (>102.3), that's implausible for real vessel speeds -- should warn,
    # but still just NaN them rather than auto-correcting.
    values = [150.0, 300.0, 450.0, 600.0, 5.0]
    with caplog.at_level("WARNING"):
        result = clean_sog(values, raw_units=False)
    assert sum(math.isnan(v) for v in result) == 4
    assert any("raw_units" in r.message or "sog-raw-units" in r.message for r in caplog.records)


def test_warns_when_raw_units_assumption_yields_implausibly_slow_fleet(caplog):
    # If raw_units=True is declared but the data was already in knots, dividing
    # by 10 produces implausibly slow speeds -- should warn.
    values = [5.7, 9.1, 4.6, 6.4]  # already-knots values misinterpreted as raw
    with caplog.at_level("WARNING"):
        clean_sog(values, raw_units=True)
    assert any("already have been in knots" in r.message for r in caplog.records)


def test_no_warning_for_plausible_data(caplog):
    values = [0.0, 5.7, 9.1, 46.2, 64.0]
    with caplog.at_level("WARNING"):
        clean_sog(values, raw_units=False)
    assert not caplog.records
