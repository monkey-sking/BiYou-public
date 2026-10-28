from abc import ABC, abstractmethod
import inspect
from typing import Literal

from core.publishing.models import AdapterCapabilities, PublishJob, PublishResult

StabilityTier = Literal["stable", "beta"]
ExecutionMode = Literal["api", "automation"]

_STABILITY_TIERS = {"stable", "beta"}
_EXECUTION_MODES = {"api", "automation"}


class BasePublishingAdapter(ABC):
    platform_name: str = ""
    stability_tier: StabilityTier = "stable"
    execution_mode: ExecutionMode = "api"
    capabilities: AdapterCapabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.stability_tier not in _STABILITY_TIERS:
            raise ValueError(f"Invalid stability_tier: {cls.stability_tier}")
        if cls.execution_mode not in _EXECUTION_MODES:
            raise ValueError(f"Invalid execution_mode: {cls.execution_mode}")
        platform_name = getattr(cls, "platform_name", "")
        if not inspect.isabstract(cls) and (
            not isinstance(platform_name, str) or not platform_name.strip()
        ):
            raise ValueError("platform_name must not be empty")
        if not inspect.isabstract(cls) and not isinstance(
            getattr(cls, "capabilities", None), AdapterCapabilities
        ):
            raise ValueError("capabilities must be an AdapterCapabilities instance")
        if not inspect.isabstract(cls):
            cls._validate_required_method("validate", ("self", "job"))
            cls._validate_required_method("publish", ("self", "job"))
            cls._validate_required_method("schedule", ("self", "job", "publish_at"))

    @classmethod
    def _validate_required_method(cls, method_name: str, expected_params: tuple[str, ...]) -> None:
        method = inspect.getattr_static(cls, method_name)
        if isinstance(method, (staticmethod, classmethod)):
            method = method.__func__
        if not callable(method):
            raise ValueError(f"Invalid {method_name} override: not callable")
        parameters = tuple(inspect.signature(method).parameters)
        if parameters != expected_params:
            raise ValueError(f"Invalid {method_name} signature: {parameters}")

    @abstractmethod
    def validate(self, job: PublishJob) -> None:
        raise NotImplementedError

    @abstractmethod
    def publish(self, job: PublishJob) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def schedule(self, job: PublishJob, publish_at: str) -> PublishResult:
        raise NotImplementedError
