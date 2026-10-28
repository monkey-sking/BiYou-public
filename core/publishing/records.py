from core.publishing.models import PublishResult


def get_publish_record(metadata: dict, platform: str) -> dict:
    return (metadata.get("publish_records") or {}).get(platform, {})


def update_publish_record(metadata: dict, result: PublishResult) -> dict:
    publish_records = metadata.setdefault("publish_records", {})
    publish_records[result.platform] = {
        "status": result.status,
        "platform_content_id": result.platform_content_id,
        "platform_url": result.platform_url,
        "native_schedule": result.native_schedule,
        "scheduled_by": result.scheduled_by,
        "scheduled_for": result.scheduled_for,
        "published_at": result.published_at,
        "last_error": result.last_error,
        **result.extra,
    }

    if result.platform == "bilibili" and result.status in {"published", "scheduled"}:
        if result.platform_content_id:
            metadata["uploaded_bvid"] = result.platform_content_id
        aid = result.extra.get("aid")
        if aid is not None:
            metadata["uploaded_aid"] = aid
        try:
            from core.kb.store import store_resource_links_from_metadata
            store_resource_links_from_metadata(metadata)
        except Exception:
            pass

    return publish_records[result.platform]


def get_bilibili_publish_ids(metadata: dict) -> tuple[str, int]:
    record = get_publish_record(metadata, "bilibili")
    if record.get("status") == "published":
        bvid = record.get("platform_content_id") or metadata.get("uploaded_bvid", "")
        aid = record.get("aid") if record.get("aid") is not None else metadata.get("uploaded_aid")
        return bvid, aid

    bvid = metadata.get("uploaded_bvid", "")
    aid = metadata.get("uploaded_aid")
    if aid is None:
        aid = 0
    return bvid, aid
