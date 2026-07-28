from __future__ import annotations

from datetime import datetime, timedelta

# Each grid cell is left-justified to this width so the weekday columns line up
# regardless of how long a `<ticket> (<count>)` line is.
CALENDAR_CELL_WIDTH = 18


def render_calendar_grid(month: str, days: dict[str, dict[str, int]]) -> None:
    """Month calendar grid: weeks as rows, Mon-Fri as columns.

    `days` maps a date to its per-ticket commit counts (weekend commits already
    folded into the preceding Friday by the runner). Each active day's cell shows
    one `<ticket> (<count>)` line per ticket, stacked below the date header.
    """
    cells = {
        day: [f"{label} ({count})" for label, count in counts.items()]
        for day, counts in days.items()
    }
    width = CALENDAR_CELL_WIDTH
    first = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
    curr = first - timedelta(days=first.weekday())
    while True:
        week_days = [(curr + timedelta(days=index)).isoformat() for index in range(5)]
        curr += timedelta(days=7)
        if not any(day.startswith(month) for day in week_days):
            if curr.month != first.month and curr > first:
                break
            continue
        print(" | ".join(
            (day if day.startswith(month) else "").ljust(width) for day in week_days
        ))
        week_cells = [cells.get(day, []) for day in week_days]
        rows = max((len(lines) for lines in week_cells), default=0)
        for row_index in range(rows):
            print(" | ".join(
                (lines[row_index] if row_index < len(lines) else "").ljust(width)
                for lines in week_cells
            ))
        print()
