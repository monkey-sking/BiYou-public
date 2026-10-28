import unittest

import core.services.bili_activity as bili_activity


AFTER_EFFECTS_METADATA = {
    "title": "AE地图动画教程，做出 Netflix 纪录片风格镜头",
    "description": "After Effects 地图动画、路径动画、镜头运动、纪录片风格包装教程。",
    "original_title": "How To Make Map Animation Like Netflix Documentaries in After Effects (NO PLUGINS)",
    "original_description": "Learn how to create map animation in After Effects with no plugins.",
    "tags": ["After Effects", "动画教程", "地图动画", "motion design"],
}

GENERIC_TECH_METADATA = {
    "title": "UE5 城市场景技术演示",
    "description": "讲解 Nanite、Lumen 和大规模城市建模。",
    "original_title": "This Developer Recreated San Francisco 1:1 in Unreal Engine 5",
    "original_description": "A UE5 city building breakdown.",
    "tags": ["UE5", "Nanite", "Lumen", "城市建模", "游戏开发"],
}


class TestBiliActivitySelection(unittest.TestCase):
    def test_existing_irrelevant_mission_should_be_recomputed(self):
        self.assertTrue(hasattr(bili_activity, "should_refresh_existing_mission"))
        existing_metadata = {
            **AFTER_EFFECTS_METADATA,
            "mission_id": 4062400,
            "mission_name": "友望清洁暖科技",
            "topic_keyword": "科技",
            "category": "知识-科学科普",
        }
        self.assertTrue(bili_activity.should_refresh_existing_mission(existing_metadata))

    def test_generic_tech_keyword_does_not_reuse_cache_blindly(self):
        self.assertTrue(hasattr(bili_activity, "should_reuse_cached_mission"))
        self.assertFalse(
            bili_activity.should_reuse_cached_mission("科技", AFTER_EFFECTS_METADATA)
        )

    def test_brand_marketing_topic_is_rejected_for_after_effects_tutorial(self):
        self.assertFalse(
            bili_activity.is_activity_relevant(
                "友望清洁暖科技",
                keyword="科技",
                category="知识-科学科普",
                metadata=AFTER_EFFECTS_METADATA,
            )
        )

    def test_choose_best_activity_prefers_domain_match_over_generic_brand_topic(self):
        self.assertTrue(hasattr(bili_activity, "choose_best_activity"))
        candidates = [
            {"id": 4062400, "title": "友望清洁暖科技", "matched_keyword": "科技"},
            {"id": 4054933, "title": "技术UP主养成计划", "matched_keyword": "技术"},
            {"id": 29418, "title": "虚幻引擎", "matched_keyword": "UE5"},
        ]

        chosen = bili_activity.choose_best_activity(
            candidates,
            keyword="科技",
            category="知识-计算机技术",
            metadata=GENERIC_TECH_METADATA,
        )

        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["id"], 29418)


if __name__ == "__main__":
    unittest.main()
