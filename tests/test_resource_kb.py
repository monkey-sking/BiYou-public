import json
import os
import tempfile
import unittest

from core.kb.store import ResourceLinkStore


class TestResourceLinkStore(unittest.TestCase):
    def test_upsert_and_search_by_title_keyword(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "resources.sqlite3")
            store = ResourceLinkStore(db_path)
            links = [
                {
                    "url": "https://github.com/example/project",
                    "label": "Project files",
                    "classification": "video_required",
                }
            ]

            store.upsert(
                bvid="BV1abc",
                video_title="UE5 Project Files",
                channel="Demo Channel",
                links=links,
            )

            row = store.get("BV1abc")
            self.assertEqual(row["video_title"], "UE5 Project Files")
            self.assertEqual(json.loads(row["links_json"]), links)

            matches = store.search("project files")
            self.assertEqual(matches[0]["bvid"], "BV1abc")

    def test_store_from_metadata_requires_bvid_and_resource_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceLinkStore(os.path.join(tmpdir, "resources.sqlite3"))

            self.assertFalse(store.store_from_metadata({"resource_links": [{"url": "https://x.test"}]}))
            self.assertTrue(
                store.store_from_metadata(
                    {
                        "title": "Demo",
                        "uploader": "Channel",
                        "uploaded_bvid": "BV1demo",
                        "resource_links": [{"url": "https://github.com/example/repo"}],
                    }
                )
            )
            self.assertEqual(store.get("BV1demo")["channel"], "Channel")


if __name__ == "__main__":
    unittest.main()
