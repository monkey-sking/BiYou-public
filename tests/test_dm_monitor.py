import unittest

from core.kb.dm_monitor import ResourceDMMonitor


class FakeStore:
    def search(self, query, limit=5):
        if "demo" in query.lower():
            return [
                {
                    "bvid": "BV1demo",
                    "video_title": "Demo Video",
                    "links": [
                        {
                            "url": "https://github.com/example/demo",
                            "label": "Project files",
                            "classification": "video_required",
                        }
                    ],
                }
            ]
        return []


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    def fetch_new_messages(self):
        return self.messages

    def send_text(self, uid, content):
        self.sent.append((uid, content))


class TestDMMonitor(unittest.TestCase):
    def test_monitor_replies_when_keyword_matches_kb_entry(self):
        client = FakeClient([{"uid": 42, "content": "资源 demo"}])
        monitor = ResourceDMMonitor(client=client, store=FakeStore())

        count = monitor.poll_once()

        self.assertEqual(count, 1)
        self.assertEqual(client.sent[0][0], 42)
        self.assertIn("Demo Video", client.sent[0][1])
        self.assertIn("https://github.com/example/demo", client.sent[0][1])

    def test_monitor_ignores_non_resource_messages(self):
        client = FakeClient([{"uid": 42, "content": "你好"}])
        monitor = ResourceDMMonitor(client=client, store=FakeStore())

        self.assertEqual(monitor.poll_once(), 0)
        self.assertEqual(client.sent, [])


if __name__ == "__main__":
    unittest.main()
