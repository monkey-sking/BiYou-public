import unittest


class BiliActivityImportTests(unittest.TestCase):
    def test_imports_under_python311_compatible_annotations(self):
        from core.services import bili_activity

        self.assertTrue(callable(bili_activity.should_refresh_existing_mission))


if __name__ == "__main__":
    unittest.main()
