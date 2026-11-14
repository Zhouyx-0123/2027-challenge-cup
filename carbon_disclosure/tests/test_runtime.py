import http.client
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest

from carbon_disclosure import DIST, build_html
from carbon_disclosure.server import create_server

ROOT = Path(__file__).resolve().parents[2]


class RuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_home_assets_and_pdf_are_served(self):
        status, _, html = self.request("/")
        self.assertEqual(status, 200)
        assets = re.findall(r'(?:src|href)="([^"#]+\.(?:js|css))"', html.decode("utf-8"))
        self.assertEqual(len(assets), 11)
        for name in assets + ["sample.csv", "methodology.pdf"]:
            status, headers, content = self.request("/" + name)
            self.assertEqual(status, 200, name)
            self.assertEqual(content, (DIST / name).read_bytes())
        self.assertEqual(headers["Content-Type"], "application/pdf")

    def test_head_and_health(self):
        self.assertIn(b'"status":"ok"', self.request("/healthz")[2])
        status, headers, content = self.request("/methodology.pdf", "HEAD")
        self.assertEqual(status, 200)
        self.assertGreater(int(headers["Content-Length"]), 1000)
        self.assertEqual(content, b"")

    def test_only_static_files_are_exposed(self):
        for path in ["/../package.json", "/%2e%2e%5c__init__.py", "/server.py", "/not-found", "/"]:
            status, _, _ = self.request(path)
            self.assertIn(status, [200] if path == "/" else [403, 404])
        self.assertEqual(self.request("/", "POST")[0], 405)

    def test_portable_page_matches_source(self):
        html = build_html()
        self.assertEqual((ROOT / "打开碳账.html").read_text(encoding="utf-8"), html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('href="styles.css"', html)
        self.assertEqual(len(re.findall(r"<script>", html)), 9)
        self.assertIn("LEDGER_MANUAL_PDF", html)

    def test_command_line_handles_invalid_port(self):
        result = subprocess.run([sys.executable, "-m", "carbon_disclosure", "--port", "99999"],
                                cwd=ROOT, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"port must be", result.stderr)


if __name__ == "__main__":
    unittest.main()
