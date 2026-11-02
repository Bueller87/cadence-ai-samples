"""Change the exercise policy here. Keep the Incident Triage recipe unchanged."""

from datetime import timedelta

SCAN_ACTIVITY = {
    "schedule_to_close_timeout": timedelta(minutes=2),
    "heartbeat_timeout": timedelta(seconds=5),
}
