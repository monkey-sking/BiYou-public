import unittest
from unittest import mock

from core.kb import resource_links


class TestResourceLinks(unittest.TestCase):
    def test_fallback_extracts_and_classifies_required_links(self):
        description = """
        Project files: https://github.com/example/tool
        Join Discord: https://discord.gg/demo
        Sponsor: https://sponsor.example.com/deal
        """

        links = resource_links.extract_links_fallback(description)

        by_url = {item["url"]: item for item in links}
        self.assertEqual(
            by_url["https://github.com/example/tool"]["classification"],
            "video_required",
        )
        self.assertEqual(
            by_url["https://discord.gg/demo"]["classification"],
            "video_required",
        )
        self.assertEqual(
            by_url["https://sponsor.example.com/deal"]["classification"],
            "promotional",
        )

    def test_extract_resource_links_prefers_hf_json_response(self):
        response = [
            {
                "url": "https://assetstore.unity.com/packages/tools/demo",
                "label": "Unity asset",
                "classification": "video_required",
                "reason": "core tutorial asset",
            }
        ]

        with mock.patch.object(
            resource_links,
            "_extract_with_hf_share",
            return_value=response,
        ) as hf:
            links = resource_links.extract_resource_links(
                "Title",
                "Use https://assetstore.unity.com/packages/tools/demo",
                config={"resource_link_extraction": {"enabled": True}},
            )

        hf.assert_called_once()
        self.assertEqual(links, response)

    def test_metadata_links_are_added_without_live_hf_when_disabled(self):
        metadata = {"original_title": "Demo", "original_description": "Code: https://github.com/example/repo"}

        updated = resource_links.attach_resource_links(
            metadata,
            config={"resource_link_extraction": {"enabled": False}},
        )

        self.assertEqual(updated["resource_links"][0]["classification"], "video_required")


if __name__ == "__main__":
    unittest.main()
