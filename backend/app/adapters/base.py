"""Adapter contract for all supported learning platforms."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import Event

from app.models import Course, ProgressEvent


ProgressCallback = Callable[[ProgressEvent], None]


class PlatformAdapter(ABC):
    """Small contract that keeps UI code independent from platform APIs."""

    platform_id: str
    display_name: str
    supports_course_listing = True
    supports_course_execution = True

    @abstractmethod
    def login(self, progress: ProgressCallback | None = None) -> bool | None:
        """Authenticate the adapter's session."""

    @abstractmethod
    def list_courses(self) -> list[Course]:
        """Return normalized courses visible to the current account."""

    @abstractmethod
    def run_course(
        self,
        course: Course,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        """Run one course while reporting progress to the caller."""

    def close(self) -> None:
        """Release browser/session resources when the UI exits."""
