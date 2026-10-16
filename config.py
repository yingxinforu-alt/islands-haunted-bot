"""ISLANDS Halloween Discord Bot — adjustable activity settings.

Keep campaign-specific values here. The bot can be fully tested before dates are set.
"""
from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# The 2026 campaign runs from October 16 through October 31, inclusive,
# using Beijing time for daily resets and activity availability.
START_DATE: str | None = "2026-10-16"
END_DATE: str | None = "2026-10-31"


def activity_is_open(day: str | None = None) -> bool:
    """Return True when scoring is open. Blank dates mean test/open mode."""
    day = day or datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    if START_DATE and day < START_DATE:
        return False
    if END_DATE and day > END_DATE:
        return False
    return True


# ---- Personal points ----
PTS_CHECKIN = 1
PTS_QUIZ = 3
PTS_UGC = 5
PTS_VOTE = 1

# Check-in milestones are cumulative, not consecutive.
MILESTONES = {3: 3, 7: 5, 14: 10}

# Lightweight onboarding / exploration bonuses.
# First Haunt fires once on the user's first valid activity of any type.
PTS_FIRST_HAUNT = 2
# Halloween Explorer fires once after the user completes 3 distinct activity types.
PTS_EXPLORER = 3
EXPLORER_TASK_TYPES = 3

# ---- Community contribution weights ----
# Community progress is intentionally separate from personal points.
COMM_CHECKIN = 1
COMM_QUIZ = 2
COMM_UGC = 8
COMM_VOTE = 1
# First Haunt / Explorer / check-in milestones / UGC vote bonuses do NOT add community points.

# ---- UGC ----
UGC_TOTAL_CAP = 5          # Max scoring Halloween entries per user for the campaign.
UGC_DAILY_CAP = 1         # Max new scoring Halloween entry per user per day.
VOTE_DAILY_SCORE_CAP = 3  # More votes are allowed; only first 3/day earn voter points.

# Votes RECEIVED by one UGC post -> extra author points at milestones.
# Cumulative maximum bonus per post = 15.
UGC_VOTE_BONUSES = {5: 2, 10: 3, 20: 5, 30: 5}

# ---- Community progress ----
# Targeted for roughly 30-50 daily active participants over the 16-day campaign.
PROGRESS_TOTAL = 2000
NODES = [
    (0.30, "30% Community Reward"),
    (0.50, "50% Community Reward"),
    (1.00, "100% Final Community Reward"),
]

# ---- Quiz ----
QUIZ_OPEN_HOURS = 2/60

# ---- Storage / UI ----
DB_PATH = os.getenv("DB_PATH", "data/haunted.db")
LEADERBOARD_SIZE = 10
