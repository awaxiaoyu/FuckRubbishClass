"""Shared data contracts used by platform adapters and the desktop UI."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Course:
    """A normalized course independent of the source platform."""

    platform: str
    course_id: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    """A runnable unit in a course."""

    platform: str
    course_id: str
    task_id: str
    name: str
    kind: str = "course"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgressEvent:
    """A platform-neutral progress message for the UI."""

    message: str
    current: float | None = None
    total: float | None = None
    level: str = "info"
    created_at: datetime = field(default_factory=datetime.now)
    qr_image: bytes | None = None


class AdapterError(RuntimeError):
    """A user-facing platform adapter failure."""


class AdapterUnavailable(AdapterError):
    """The adapter cannot run in the current installation."""
