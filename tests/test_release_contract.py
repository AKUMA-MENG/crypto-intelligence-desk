from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_version_and_documented_models(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("gemini-3.1-flash-lite", readme)
        self.assertIn("gemini-3.5-flash-lite", readme)
        self.assertIn("background_worker.py", readme)

    def test_systemd_template_is_isolated_and_has_no_secret(self):
        unit = (ROOT / "deploy" / "crypto-intelligence-desk-worker.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("StateDirectory=crypto-intelligence-desk", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("EnvironmentFile=-/opt/crypto-intelligence-desk/.env", unit)
        self.assertNotIn("ListenStream", unit)
        self.assertNotIn("BARK_PUSH_KEY=", unit)
        self.assertNotIn("GEMINI_API_KEY=", unit)

    def test_env_example_has_empty_secrets(self):
        values = {}
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name] = value
        self.assertEqual(values["BARK_PUSH_KEY"], "")
        self.assertEqual(values["GEMINI_API_KEY"], "")

    def test_proxy_reads_the_release_version_instead_of_hard_coding_it(self):
        proxy_source = (ROOT / "proxy.py").read_text(encoding="utf-8")
        self.assertIn("APP_VERSION", proxy_source)
        self.assertIn('open(os.path.join(BASE, "VERSION")', proxy_source)
        self.assertNotIn('"version":"1.0.1"', proxy_source)

    def test_local_env_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)

    def test_docs_state_cold_start_and_l4_l5_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "使用说明.md").read_text(encoding="utf-8")
        self.assertIn("first configured run creates a baseline", readme)
        self.assertIn("only L4-L5 results are reviewed", readme)
        self.assertIn("第一次有效启动只建立新闻基线", chinese)
        self.assertIn("只有初判达到 L4-L5 才", chinese)

    def test_requirements_remain_standard_library_only(self):
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        declarations = [line for line in lines if line.strip() and not line.startswith("#")]
        self.assertEqual(declarations, [])
        self.assertIn("No third-party Python packages are required", "\n".join(lines))

    def test_release_scanner_explicitly_excludes_dot_env(self):
        scanner = (ROOT / "tools" / "check-release.ps1").read_text(encoding="utf-8")
        self.assertIn("$_.Name -ne '.env'", scanner)

    def test_worker_docs_require_private_env_permissions(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "使用说明.md").read_text(encoding="utf-8")
        self.assertIn("chmod 600 .env", readme)
        self.assertIn("chmod 600 .env", chinese)

    def test_worker_docs_explain_safe_v1_state_rebaseline(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "使用说明.md").read_text(encoding="utf-8")
        self.assertIn("v1 state is quarantined", readme)
        self.assertIn("v1 状态文件会被隔离", chinese)

    def test_release_scanner_includes_service_and_example_artifacts(self):
        scanner = (ROOT / "tools" / "check-release.ps1").read_text(encoding="utf-8")
        self.assertIn("'.service'", scanner)
        self.assertIn("'.example'", scanner)


if __name__ == "__main__":
    unittest.main()
