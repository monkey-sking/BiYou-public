import json
from typing import Optional

from core.publishing.models import PublishResult
from core.publishing.records import update_publish_record
from core.publishing.scheduler_state import enqueue_managed_job
from core.utils.logger import log


class PublishManager:
    def __init__(self, adapters: dict, scheduler_state: Optional[dict] = None):
        self.adapters = adapters
        self.scheduler_state = scheduler_state if scheduler_state is not None else {"jobs": []}

    def execute(self, job):
        # Preprocess metadata before publishing to any platform:
        # translate English titles, descriptions, tags; fix categories; etc.
        try:
            from core.modules.uploader import preprocess_metadata
            job.metadata = preprocess_metadata(job.metadata)
        except Exception as e:
            log.warning(f"⚠️ Metadata preprocessing failed: {e}")

        # Rebuild platform payloads AFTER preprocessing so that translated
        # titles/descriptions/tags propagate to each platform's publish payload.
        # Without this, payloads retain the original (possibly English) content.
        try:
            from core.publishing.platform_payloads import build_platform_payloads
            job.platform_payloads = build_platform_payloads(
                job.metadata, job.platform_targets
            )
        except Exception as e:
            log.warning(f"⚠️ Platform payload rebuild failed: {e}")

        # Sync top-level PublishJob fields with preprocessed metadata so that
        # adapter fallbacks (e.g. payload.get("title", job.title)) use the
        # translated values instead of the original English text.
        job.title = job.metadata.get("title", job.title)
        job.description = job.metadata.get("description", job.description)
        job.tags = job.metadata.get("tags", job.tags)

        resolved_targets = []

        for platform in job.platform_targets:
            adapter = self.adapters[platform]
            adapter.validate(job)
            resolved_targets.append((platform, adapter))

        for platform, adapter in resolved_targets:
            try:
                if job.schedule_intent.mode == "publish_at":
                    publish_at = job.schedule_intent.publish_at
                    if adapter.capabilities.supports_native_schedule:
                        raw_result = adapter.schedule(job, publish_at)
                    else:
                        enqueue_managed_job(self.scheduler_state, job.metadata_path, platform, publish_at)
                        raw_result = {
                            "platform": platform,
                            "status": "scheduled",
                            "platform_content_id": "",
                            "platform_url": "",
                            "native_schedule": False,
                            "scheduled_by": "biyou",
                            "scheduled_for": publish_at,
                            "published_at": "",
                            "last_error": "",
                            "extra": {},
                        }
                else:
                    raw_result = adapter.publish(job)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                raw_result = {
                    "platform": platform,
                    "status": "failed_manual",
                    "platform_content_id": "",
                    "platform_url": "",
                    "native_schedule": False,
                    "scheduled_by": "biyou",
                    "scheduled_for": job.schedule_intent.publish_at if job.schedule_intent.mode == "publish_at" else "",
                    "published_at": "",
                    "last_error": str(exc).strip() or f"{platform} publish failed",
                    "extra": {},
                }

            result = PublishResult(**raw_result)
            update_publish_record(job.metadata, result)
            if job.metadata_path:
                with open(job.metadata_path, "w", encoding="utf-8") as f:
                    json.dump(job.metadata, f, ensure_ascii=False, indent=4)

        return job.metadata
