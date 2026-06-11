"""Working-day calendar.

Durations and scheduling positions are expressed in *working-day indices*
(0-based), where only Monday-Friday advance the work. Saturdays and Sundays do
not progress the job and public holidays are ignored.

Tardiness, on the other hand, is measured on the *real calendar*: the completion
date of a job (the calendar date of its last working day) is compared with the
``due_date`` in calendar days.
"""

from __future__ import annotations

from datetime import date, timedelta

_WORKING_DAYS_PER_WEEK = 5


def _is_working_day(day: date) -> bool:
    """Return True for Monday-Friday."""
    return day.weekday() < _WORKING_DAYS_PER_WEEK


def _next_working_day(day: date) -> date:
    """Return ``day`` itself if it is a working day, otherwise the next one."""
    while not _is_working_day(day):
        day += timedelta(days=1)
    return day


def count_working_days(start: date, end: date) -> int:
    """Count working days in the half-open interval ``[start, end)``.

    Returns 0 when ``end <= start``.
    """
    if end <= start:
        return 0
    total_days = (end - start).days
    full_weeks, remainder = divmod(total_days, 7)
    working = full_weeks * _WORKING_DAYS_PER_WEEK
    for offset in range(remainder):
        if _is_working_day(start + timedelta(days=offset)):
            working += 1
    return working


def working_days_in_calendar_span(start: date, end: date) -> int:
    """Count working days in the inclusive calendar range ``[start, end]``."""
    if end < start:
        return 1
    return max(1, count_working_days(start, end + timedelta(days=1)))


class WorkingCalendar:
    """
    Maps working-day indices to/from real calendar dates.

    Index 0 corresponds to the first working day on or after ``horizon_start``.
    """

    def __init__(self, horizon_start: date) -> None:
        self.horizon_start = horizon_start
        self._first_working_day = _next_working_day(horizon_start)
        self._first_weekday = self._first_working_day.weekday()

    def working_index_to_date(self, index: int) -> date:
        """Return the calendar date of the ``index``-th working day."""
        if index < 0:
            raise ValueError(f"Working-day index must be non-negative, got {index}.")
        full_weeks, remainder = divmod(index, _WORKING_DAYS_PER_WEEK)
        day = self._first_working_day + timedelta(weeks=full_weeks)
        # ``day`` is a weekday with the same weekday as the first working day.
        if self._first_weekday + remainder <= 4:
            return day + timedelta(days=remainder)
        # Step over the intervening weekend.
        return day + timedelta(days=remainder + 2)

    def date_to_working_index(self, day: date) -> int:
        """Return the smallest working-day index whose date is ``>= day``.

        Used to convert an ``earliest_start`` date into a lower bound on the
        activity start index.
        """
        if day <= self._first_working_day:
            return 0
        return count_working_days(self._first_working_day, day)

    def completion_date(self, start_index: int, duration_days: int) -> date:
        """Calendar date on which an activity finishes.

        The activity occupies working indices ``[start, start + duration)``;
        it finishes at the end of its last working day.
        """
        if duration_days <= 0:
            raise ValueError("duration_days must be positive.")
        last_working_index = start_index + duration_days - 1
        return self.working_index_to_date(last_working_index)

    def calendar_delay_days(self, completion: date, due_date: date) -> int:
        """Calendar-day tardiness: ``max(0, completion - due_date)``."""
        return max(0, (completion - due_date).days)

    def calendar_offsets(self, count: int) -> list[int]:
        """Calendar-day offset from ``horizon_start`` for indices ``0..count-1``.

        ``offsets[i]`` is the number of calendar days between ``horizon_start``
        and the date of working index ``i``. Useful to feed CP-SAT element
        constraints that turn a working-day index variable into a calendar-day
        value.
        """
        return [
            (self.working_index_to_date(i) - self.horizon_start).days
            for i in range(count)
        ]
