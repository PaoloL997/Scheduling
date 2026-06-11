"""Tests for the working-day calendar."""

from __future__ import annotations

from datetime import date

from src.optimization.calendar import (
    WorkingCalendar,
    count_working_days,
    working_days_in_calendar_span,
)


def test_index_zero_is_first_working_day_on_or_after_start():
    # 2026-01-01 is a Thursday.
    cal = WorkingCalendar(date(2026, 1, 1))
    assert cal.working_index_to_date(0) == date(2026, 1, 1)


def test_index_skips_weekend():
    # Thursday(0), Friday(1), then jump to Monday(2).
    cal = WorkingCalendar(date(2026, 1, 1))
    assert cal.working_index_to_date(1) == date(2026, 1, 2)  # Friday
    assert cal.working_index_to_date(2) == date(2026, 1, 5)  # Monday
    assert cal.working_index_to_date(6) == date(2026, 1, 9)  # next Friday


def test_start_on_weekend_advances_to_monday():
    # 2026-01-03 is a Saturday -> index 0 is Monday 2026-01-05.
    cal = WorkingCalendar(date(2026, 1, 3))
    assert cal.working_index_to_date(0) == date(2026, 1, 5)


def test_date_to_working_index_roundtrip():
    cal = WorkingCalendar(date(2026, 1, 1))
    for i in range(0, 15):
        d = cal.working_index_to_date(i)
        assert cal.date_to_working_index(d) == i


def test_date_to_working_index_weekend_rounds_up():
    cal = WorkingCalendar(date(2026, 1, 1))
    # Saturday 2026-01-03 maps to the next working index (Monday = index 2).
    assert cal.date_to_working_index(date(2026, 1, 3)) == 2


def test_completion_date_spans_weekend():
    cal = WorkingCalendar(date(2026, 1, 1))
    # Start Thursday, duration 3 working days -> Thu, Fri, Mon -> finishes Mon.
    assert cal.completion_date(0, 3) == date(2026, 1, 5)


def test_calendar_delay_days():
    cal = WorkingCalendar(date(2026, 1, 1))
    assert cal.calendar_delay_days(date(2026, 1, 10), date(2026, 1, 5)) == 5
    assert cal.calendar_delay_days(date(2026, 1, 3), date(2026, 1, 5)) == 0


def test_calendar_offsets_match_individual_lookups():
    cal = WorkingCalendar(date(2026, 1, 1))
    offsets = cal.calendar_offsets(10)
    assert offsets[0] == 0
    assert offsets[2] == (date(2026, 1, 5) - date(2026, 1, 1)).days  # 4


def test_count_working_days_half_open():
    assert count_working_days(date(2026, 1, 1), date(2026, 1, 1)) == 0
    # Thu..next Thu exclusive: Thu, Fri, Mon, Tue, Wed = 5.
    assert count_working_days(date(2026, 1, 1), date(2026, 1, 8)) == 5


def test_working_days_in_calendar_span_inclusive():
    # Mon–Fri same week.
    assert working_days_in_calendar_span(date(2026, 6, 1), date(2026, 6, 5)) == 5
    # Jul 30–Aug 19 2026: 15 working days in 21 calendar days.
    assert working_days_in_calendar_span(date(2026, 7, 30), date(2026, 8, 19)) == 15
