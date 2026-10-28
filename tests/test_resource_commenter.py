import unittest

from core.modules.commenter import _build_comment_plan


class TestResourceCommenter(unittest.TestCase):
    def test_resource_links_force_dm_notice_as_primary_comment(self):
        metadata = {
            "title": "UE5 Project Files",
            "description": "Demo",
            "resource_links": [
                {
                    "url": "https://github.com/example/project",
                    "classification": "video_required",
                }
            ],
        }

        main, subs = _build_comment_plan({"comment_rules": []}, metadata)

        self.assertIn("私信", main)
        self.assertIn("资源", main)
        self.assertIn("UE5 Project Files", main)
        self.assertEqual(subs, [])


if __name__ == "__main__":
    unittest.main()
