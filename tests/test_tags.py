import unittest

from core.modules.fetcher import generate_ai_tags


class TestTags(unittest.TestCase):
    def test_fitness_tags_present(self):
        tags = generate_ai_tags(
            "20 min HIIT workout",
            "Full body cardio training for fat burn"
        )
        self.assertIn("健身", tags)

    def test_non_fitness_tags_not_forced(self):
        tags = generate_ai_tags(
            "NVIDIA DLSS 5 Just Changed Unreal Engine Forever",
            "Unreal Engine 5 and real-time rendering news"
        )
        self.assertNotIn("健身", tags)
        self.assertNotIn("运动", tags)


if __name__ == "__main__":
    unittest.main()
