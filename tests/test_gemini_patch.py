import unittest
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from core.utils import gemini_patch


class DummyPayload:
    def __init__(self, rpcid):
        self.rpcid = rpcid


class TestGeminiPatch(unittest.TestCase):
    def setUp(self):
        constants = ModuleType("gemini_webapi.constants")
        constants.GRPC = SimpleNamespace(BARD_ACTIVITY="activity", LIST_CHATS="chats")
        self.constants = constants
        context = patch.dict(sys.modules, {"gemini_webapi.constants": constants})
        context.start()
        self.addCleanup(context.stop)

    def test_should_ignore_bard_activity_400(self):
        try:
            from gemini_webapi.constants import GRPC
        except Exception:
            self.skipTest("gemini_webapi not available")
        payloads = [DummyPayload(GRPC.BARD_ACTIVITY)]
        err = Exception("Batch execution failed with status code 400")
        self.assertTrue(gemini_patch._should_ignore_batch_error(payloads, err))

    def test_should_not_ignore_other_rpc(self):
        try:
            from gemini_webapi.constants import GRPC
        except Exception:
            self.skipTest("gemini_webapi not available")
        payloads = [DummyPayload(GRPC.LIST_CHATS)]
        err = Exception("Batch execution failed with status code 400")
        self.assertFalse(gemini_patch._should_ignore_batch_error(payloads, err))

    def test_should_not_ignore_non_4xx(self):
        try:
            from gemini_webapi.constants import GRPC
        except Exception:
            self.skipTest("gemini_webapi not available")
        payloads = [DummyPayload(GRPC.BARD_ACTIVITY)]
        err = Exception("Batch execution failed with status code 500")
        self.assertFalse(gemini_patch._should_ignore_batch_error(payloads, err))

    def test_batch_execute_patch_skips_missing_method(self):
        class ClientWithoutBatch:
            pass

        class Endpoint:
            BATCH_EXEC = "batch"

        def running(**kwargs):
            def decorator(func):
                return func
            return decorator

        self.assertFalse(
            gemini_patch._patch_batch_execute_if_available(
                ClientWithoutBatch,
                Exception,
                running,
                Endpoint,
            )
        )


    def test_missing_optional_activity_rpc_does_not_swallow_error(self):
        del self.constants.GRPC.BARD_ACTIVITY
        self.assertFalse(gemini_patch._should_ignore_batch_error(
            [DummyPayload("activity")], Exception("status code 400")
        ))


if __name__ == "__main__":
    unittest.main()
