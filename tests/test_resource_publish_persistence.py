import os
import tempfile
import unittest
from unittest import mock

from core.publishing.models import PublishResult
from core.publishing.records import update_publish_record


class TestResourcePublishPersistence(unittest.TestCase):
    def test_bilibili_publish_record_stores_resource_links_in_kb(self):
        metadata = {
            "title": "Demo",
            "resource_links": [{"url": "https://github.com/example/repo"}],
        }
        result = PublishResult(
            platform="bilibili",
            status="published",
            platform_content_id="BV1resource",
            platform_url="https://www.bilibili.com/video/BV1resource",
            native_schedule=False,
            scheduled_by="",
            scheduled_for="",
            published_at="",
            last_error="",
            extra={"aid": 123},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "resources.sqlite3")
            with mock.patch(
                "core.kb.store.get_default_db_path",
                return_value=db_path,
            ):
                update_publish_record(metadata, result)
                from core.kb.store import ResourceLinkStore

                row = ResourceLinkStore(db_path).get("BV1resource")

        self.assertIsNotNone(row)
        self.assertEqual(row["video_title"], "Demo")


if __name__ == "__main__":
    unittest.main()
