import unittest

from core.publishing.scheduler_state import enqueue_managed_job, get_due_jobs, remove_job


class TestPublishingScheduler(unittest.TestCase):
    def test_enqueue_managed_job_rejects_malformed_publish_at(self):
        state = {"jobs": []}

        with self.assertRaises(ValueError):
            enqueue_managed_job(
                state,
                metadata_path="/tmp/video.json",
                platform="douyin",
                publish_at="not-a-timestamp",
            )

    def test_enqueue_managed_job_rejects_naive_publish_at(self):
        state = {"jobs": []}

        with self.assertRaises(ValueError):
            enqueue_managed_job(
                state,
                metadata_path="/tmp/video.json",
                platform="douyin",
                publish_at="2026-04-08T19:05:00",
            )

    def test_enqueue_managed_job_keeps_unique_platform_key(self):
        state = {"jobs": []}

        enqueue_managed_job(
            state,
            metadata_path="/tmp/video.json",
            platform="kuaishou",
            publish_at="2026-04-08T19:05:00+08:00",
        )
        enqueue_managed_job(
            state,
            metadata_path="/tmp/video.json",
            platform="kuaishou",
            publish_at="2026-04-08T19:10:00+08:00",
        )

        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(state["jobs"][0]["publish_at"], "2026-04-08T19:10:00+08:00")

    def test_enqueue_managed_job_dedupes_plain_dict_without_job_key(self):
        state = {
            "jobs": [
                {
                    "metadata_path": "/tmp/video.json",
                    "platform": "kuaishou",
                    "publish_at": "2026-04-08T19:05:00+08:00",
                }
            ]
        }

        enqueue_managed_job(
            state,
            metadata_path="/tmp/video.json",
            platform="kuaishou",
            publish_at="2026-04-08T19:10:00+08:00",
        )

        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(state["jobs"][0]["publish_at"], "2026-04-08T19:10:00+08:00")

    def test_get_due_jobs_returns_only_ready_items(self):
        state = {
            "jobs": [
                {
                    "metadata_path": "/tmp/one.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-08T19:05:00+08:00",
                },
                {
                    "metadata_path": "/tmp/two.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-09T19:05:00+08:00",
                },
            ]
        }

        due = get_due_jobs(state, now_iso="2026-04-08T19:06:00+08:00")

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["metadata_path"], "/tmp/one.json")

    def test_get_due_jobs_skips_malformed_publish_at(self):
        state = {
            "jobs": [
                {
                    "metadata_path": "/tmp/one.json",
                    "platform": "douyin",
                    "publish_at": "not-a-timestamp",
                },
                {
                    "metadata_path": "/tmp/two.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-08T19:05:00+08:00",
                },
            ]
        }

        due = get_due_jobs(state, now_iso="2026-04-08T19:06:00+08:00")

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["metadata_path"], "/tmp/two.json")

    def test_remove_job_deletes_matching_entry(self):
        state = {
            "jobs": [
                {
                    "job_key": "/tmp/one.json:douyin",
                    "metadata_path": "/tmp/one.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-08T19:05:00+08:00",
                },
                {
                    "job_key": "/tmp/two.json:douyin",
                    "metadata_path": "/tmp/two.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-09T19:05:00+08:00",
                },
            ]
        }

        remove_job(state, metadata_path="/tmp/one.json", platform="douyin")

        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(state["jobs"][0]["metadata_path"], "/tmp/two.json")

    def test_remove_job_deletes_plain_dict_without_job_key(self):
        state = {
            "jobs": [
                {
                    "metadata_path": "/tmp/one.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-08T19:05:00+08:00",
                },
                {
                    "metadata_path": "/tmp/two.json",
                    "platform": "douyin",
                    "publish_at": "2026-04-09T19:05:00+08:00",
                },
            ]
        }

        remove_job(state, metadata_path="/tmp/one.json", platform="douyin")

        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(state["jobs"][0]["metadata_path"], "/tmp/two.json")
