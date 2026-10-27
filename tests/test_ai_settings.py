"""AI assistant settings: the provider address is taken in the forms people paste it."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp import ai  # noqa: E402
from erp.db import Database  # noqa: E402

SUPER = {"username": "boss", "is_super": True}
CF = "https://api.cloudflare.com/client/v4/accounts/0123abcd4567ef/ai"


class ProviderAddress(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def save(self, **d):
        return ai.save_policy(self.db, SUPER, {"provider": "cloudflare", **d})

    def test_cloudflare_is_offered_with_its_address_pattern(self):
        pr = next(p for p in ai.PROVIDERS if p["id"] == "cloudflare")
        self.assertTrue(pr["base_url"].endswith("/accounts/ACCOUNT_ID/ai/v1"))
        self.assertTrue(pr["help"] and pr["help_ar"])

    def test_the_openai_compatible_address_is_kept(self):
        p = self.save(base_url=CF + "/v1", model="@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        self.assertEqual((p["base_url"], p["model"]), (CF + "/v1", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"))

    def test_the_full_endpoint_pasted_loses_the_part_the_app_adds(self):
        p = self.save(base_url=CF + "/v1/chat/completions/", model="@cf/openai/gpt-oss-120b")
        self.assertEqual(p["base_url"], CF + "/v1")
        p = ai.save_policy(self.db, SUPER, {"provider": "openai", "base_url": "https://api.openai.com/v1/chat/completions", "model": "m-1"})
        self.assertEqual(p["base_url"], "https://api.openai.com/v1")

    def test_the_per_model_address_becomes_the_compatible_one_with_its_model(self):
        p = self.save(base_url=CF + "/run/@cf/meta/llama-3.1-8b-instruct-fp8-fast", model="")
        self.assertEqual((p["base_url"], p["model"]), (CF + "/v1", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"))
        p = self.save(base_url=CF + "/run/@cf/meta/llama-3.1-8b-instruct-fp8-fast", model="@cf/openai/gpt-oss-20b")
        self.assertEqual(p["model"], "@cf/openai/gpt-oss-20b")          # a model typed in the box wins

    def test_the_placeholder_left_in_is_refused_clearly(self):
        with self.assertRaisesRegex(ai.AIError, "Replace ACCOUNT_ID"):
            self.save(base_url="https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1", model="@cf/meta/llama-3.3-70b-instruct-fp8-fast")


if __name__ == "__main__":
    unittest.main()
