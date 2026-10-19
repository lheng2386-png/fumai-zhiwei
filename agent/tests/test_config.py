import unittest

from agent.config import configured_model
from agent.model import ChatCompletionsModel, ResponsesModel


class ModelConfigTests(unittest.TestCase):
    def test_unconfigured_rules_mode_and_partial_config_rejection(self):
        self.assertIsNone(configured_model({}))
        for env in ({"AGENT_API_KEY": "test"}, {"AGENT_MODEL": "qwen"}):
            with self.assertRaises(ValueError):
                configured_model(env)

    def test_qwen_api_root_and_key_precedence(self):
        model = configured_model({"AGENT_MODEL_API": "chat_completions", "AGENT_BASE_URL": "https://model.example/v1/",
                                  "AGENT_MODEL": "qwen", "AGENT_API_KEY": "agent-key", "OPENAI_API_KEY": "legacy-key"})
        self.assertIsInstance(model, ChatCompletionsModel)
        self.assertEqual(model.endpoint, "https://model.example/v1/chat/completions")
        self.assertEqual(model.key, "agent-key")

    def test_existing_responses_configuration_is_compatible(self):
        model = configured_model({"OPENAI_API_KEY": "test", "AGENT_MODEL": "legacy-model"})
        self.assertIsInstance(model, ResponsesModel)
        self.assertEqual(model.endpoint, "https://api.openai.com/v1/responses")

    def test_invalid_protocol_and_endpoint_fail_at_startup(self):
        with self.assertRaises(ValueError):
            configured_model({"AGENT_MODEL_API": "typo"})
        for base in ("", "http://model.example/v1", "https://user:secret@model.example/v1",
                     "https://model.example/v1?key=secret", "https://model.example/v1#fragment",
                     "https://model.example/v1/chat/completions"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                configured_model({"AGENT_MODEL_API": "chat_completions", "AGENT_BASE_URL": base,
                                  "AGENT_MODEL": "qwen", "AGENT_API_KEY": "test"})


if __name__ == "__main__":
    unittest.main()
