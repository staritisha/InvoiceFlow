from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock


@dataclass
class AppState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduler_running: bool = False
    redis_connected: bool = False
    ai_warmed_up: bool = False
    ai_requests_today: int = 0
    reminders_sent: int = 0
    workflows_executed: int = 0
    events_broadcasted: int = 0
    active_teams: set[str] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_ai_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self.ai_requests_today += 1

    def record_reminder(self) -> None:
        with self._lock:
            self.reminders_sent += 1

    def record_workflow(self) -> None:
        with self._lock:
            self.workflows_executed += 1

    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


app_state = AppState()
