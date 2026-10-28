from datetime import datetime


def _job_key(metadata_path: str, platform: str) -> str:
    return f"{metadata_path}:{platform}"


def _job_identity_matches(job: dict, key: str, metadata_path: str, platform: str) -> bool:
    job_key = job.get("job_key")
    if job_key == key:
        return True
    if not job_key and job.get("metadata_path") == metadata_path and job.get("platform") == platform:
        return True
    return False


def _parse_publish_at(publish_at: str) -> datetime:
    if not isinstance(publish_at, str):
        raise ValueError("publish_at must be a string")

    normalized = publish_at.strip()
    if not normalized:
        raise ValueError("publish_at must be a timezone-aware ISO timestamp")

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("publish_at must be a timezone-aware ISO timestamp") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("publish_at must be timezone-aware")

    return parsed


def enqueue_managed_job(state: dict, metadata_path: str, platform: str, publish_at: str) -> dict:
    jobs = state.setdefault("jobs", [])
    key = _job_key(metadata_path, platform)
    normalized_publish_at = _parse_publish_at(publish_at).isoformat()

    for job in jobs:
        if _job_identity_matches(job, key, metadata_path, platform):
            job["job_key"] = key
            job["metadata_path"] = metadata_path
            job["platform"] = platform
            job["publish_at"] = normalized_publish_at
            return job

    job = {
        "job_key": key,
        "metadata_path": metadata_path,
        "platform": platform,
        "publish_at": normalized_publish_at,
    }
    jobs.append(job)
    return job


def get_due_jobs(state: dict, now_iso: str) -> list[dict]:
    now = datetime.fromisoformat(now_iso)
    due_jobs = []

    for job in state.get("jobs", []):
        try:
            publish_at = _parse_publish_at(job["publish_at"])
        except (KeyError, TypeError, ValueError):
            continue

        if publish_at <= now:
            due_jobs.append(job)

    return due_jobs


def remove_job(state: dict, metadata_path: str, platform: str) -> None:
    key = _job_key(metadata_path, platform)
    state["jobs"] = [
        job
        for job in state.get("jobs", [])
        if not _job_identity_matches(job, key, metadata_path, platform)
    ]
