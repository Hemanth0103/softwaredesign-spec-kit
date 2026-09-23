"""Check Compose's resolved configuration without starting containers.

Run: python3 -m unittest discover -s infra/tests -v
Requires Docker Compose 2.24.4+. COMPOSE_COMMAND can select a standalone binary.
Only the committed placeholder environment is used; no real secrets are needed.
"""

import json
import os
import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def configuration(override=None):
    command = shlex.split(os.environ.get("COMPOSE_COMMAND", "docker compose"))
    command += ["--env-file", str(ROOT / ".env.example"), "-f", "compose.yaml"]
    if override:
        command += ["-f", override]
    command += ["--profile", "tools", "config", "--format", "json"]
    environment = dict(
        os.environ,
        APP_ENV_FILE=str(ROOT / ".env.example"),
        POSTGRES_PASSWORD="configuration-test-only",
        PUBLIC_DOMAIN="chat.example.edu",
    )
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = configuration()
        cls.dev = configuration("compose.dev.yaml")
        cls.test = configuration("compose.test.yaml")
        cls.production = configuration("compose.production.yaml")

    def test_database_is_private_in_every_mode(self):
        for config in (self.base, self.dev, self.test, self.production):
            self.assertTrue(config["networks"]["database"]["internal"])
            self.assertFalse(config["services"]["db"].get("ports"))
            self.assertFalse(config["services"]["api"].get("ports"))
            self.assertNotIn("database", config["services"]["web"]["networks"])
            self.assertEqual(set(config["services"]["db"]["networks"]), {"database"})

    def test_startup_waits_for_health(self):
        for config in (self.base, self.dev, self.production):
            services = config["services"]
            self.assertEqual(
                services["api"]["depends_on"]["db"]["condition"], "service_healthy"
            )
            self.assertEqual(
                services["web"]["depends_on"]["api"]["condition"], "service_healthy"
            )
            self.assertIn("healthcheck", services["api"])
            self.assertIn("healthcheck", services["db"])

    def test_persistent_database_and_one_off_migration(self):
        services = self.base["services"]
        self.assertTrue(
            any(
                v["type"] == "volume" and v["source"] == "postgres_data"
                for v in services["db"]["volumes"]
            )
        )
        self.assertEqual(services["migrate"]["profiles"], ["tools"])
        self.assertEqual(services["migrate"]["restart"], "no")
        self.assertEqual(
            services["migrate"]["command"], ["sh", "/usr/local/bin/migrate"]
        )
        self.assertEqual(services["migrate"]["build"], services["api"]["build"])

    def test_frontend_does_not_receive_backend_secrets(self):
        for config in (self.base, self.dev, self.test, self.production):
            web = config["services"]["web"]
            self.assertNotIn("AI_API_KEY", web.get("environment", {}))
            self.assertNotIn("DATABASE_URL", web.get("environment", {}))
            self.assertFalse(web["build"].get("args"))

    def test_local_and_production_ports(self):
        local = self.base["services"]["web"]["ports"]
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["host_ip"], "127.0.0.1")
        self.assertEqual(local[0]["published"], "8080")
        production = self.production["services"]["web"]
        self.assertEqual({p["published"] for p in production["ports"]}, {"80", "443"})
        self.assertEqual(production["environment"]["SITE_ADDRESS"], "chat.example.edu")

    def test_dev_and_test_targets_replace_runtime(self):
        for name in ("api", "web"):
            self.assertEqual(
                self.dev["services"][name]["build"]["target"], "development"
            )
            self.assertEqual(self.test["services"][name]["build"]["target"], "test")
        web = self.test["services"]["web"]
        self.assertFalse(web.get("ports"))
        self.assertFalse(web.get("depends_on"))
        self.assertFalse(web.get("volumes"))


if __name__ == "__main__":
    unittest.main()
