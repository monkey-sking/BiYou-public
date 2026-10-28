from dataclasses import dataclass, field
from typing import Any, Literal

ScheduleMode = Literal["publish_now", "publish_at"]
PublishResultStatus = Literal[
    "prepared",
    "scheduled",
    "publishing",
    "published",
    "failed_retryable",
    "failed_manual",
]

_SCHEDULE_INTENT_MODES = {"publish_now", "publish_at"}
_PUBLISH_RESULT_STATUSES = {
    "prepared",
    "scheduled",
    "publishing",
    "published",
    "preview",
    "failed_retryable",
    "failed_manual",
}


@dataclass(frozen=True)
class ScheduleIntent:
    mode: ScheduleMode = "publish_now"
    publish_at: str = ""

    def __post_init__(self) -> None:
        if self.mode not in _SCHEDULE_INTENT_MODES:
            raise ValueError(f"Invalid schedule intent mode: {self.mode}")
        if self.mode == "publish_at" and not isinstance(self.publish_at, str):
            raise ValueError("publish_at requires a string")
        if self.mode == "publish_at" and not self.publish_at.strip():
            raise ValueError("publish_at requires publish_at")
        if self.mode == "publish_now" and self.publish_at:
            raise ValueError("publish_now must not set publish_at")


@dataclass
class PublishJob:
    platform_targets: list[str]
    video_path: str
    title: str
    description: str
    cover_path: str
    tags: list[str]
    schedule_intent: ScheduleIntent = field(default_factory=ScheduleIntent)
    metadata_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    platform_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterCapabilities:
    supports_native_schedule: bool
    supports_cover: bool
    supports_tags: bool
    supports_description: bool


@dataclass
class PublishResult:
    platform: str
    status: PublishResultStatus
    platform_content_id: str
    platform_url: str
    native_schedule: bool
    scheduled_by: str
    scheduled_for: str
    published_at: str
    last_error: str
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _PUBLISH_RESULT_STATUSES:
            raise ValueError(f"Invalid publish result status: {self.status}")
