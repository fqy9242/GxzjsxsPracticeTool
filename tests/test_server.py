import base64
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from practice_tool.secrets import SecretError, SecretStore
from practice_tool.web import WebError, open_web_server


@unittest.skipIf(sys.platform == "win32", "Linux credential format")
class PortableCredentialTests(unittest.TestCase):
    def test_ciphertext_and_key_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = SecretStore(root)
            token = first.seal(b"private-password")
            self.assertNotIn(b"private-password", token)
            self.assertEqual(SecretStore(root).unseal(token), b"private-password")
            self.assertEqual((root / "secret.key").stat().st_mode & 0o777, 0o600)

    def test_tampered_tokens_wrong_keys_and_windows_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            store = SecretStore(Path(one))
            token = store.seal(b"private")
            for value in (token[:-5] + b"12345", b"DPAPI bytes"):
                with self.subTest(value=value), self.assertRaises(SecretError):
                    store.unseal(value)
            with self.assertRaisesRegex(SecretError, "secret.key"):
                SecretStore(Path(two)).unseal(token)

    def test_windows_still_uses_existing_dpapi_functions(self):
        with tempfile.TemporaryDirectory() as directory, patch("practice_tool.secrets.sys.platform", "win32"):
            with patch("practice_tool.secrets._crypt", return_value=b"DPAPI") as crypt:
                store = SecretStore(Path(directory))
                self.assertEqual(store.seal(b"password"), b"DPAPI")
                crypt.assert_called_once_with(b"password", protect=True)
                store.unseal(b"existing-windows-credential")
                crypt.assert_called_with(b"existing-windows-credential", protect=False)
            self.assertFalse((Path(directory) / "secret.key").exists())


class ServerTests(unittest.TestCase):
    password = "test-admin-password-123"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        patcher = patch.dict(os.environ, {"PRACTICE_ADMIN_USERNAME": "admin", "PRACTICE_ADMIN_PASSWORD": self.password,
                                        "PRACTICE_ADMIN_PASSWORD_FILE": "", "PRACTICE_PUBLIC_ORIGIN": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("practice_tool.client.PracticeClient._post", side_effect=AssertionError("Offline test"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.context = open_web_server("127.0.0.1", 0, data_dir=self.directory, server_mode=True)
        self.server = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(self.server.shutdown)

    def request(self, path="/api/dashboard", *, method="GET", payload=None, credentials=True, origin=None, host=None, extra=None):
        headers = {}
        if credentials:
            raw = f"admin:{self.password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        if extra:
            headers.update(extra)
        body = json.dumps(payload) if payload is not None else None
        if body:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_health_is_available_but_all_application_routes_require_login(self):
        self.assertEqual(self.request("/healthz", credentials=False)[0], 200)
        for route in ("/", "/schedule", "/dashboard.js", "/api/dashboard", "/api/mail-settings"):
            with self.subTest(route=route):
                status, headers, body = self.request(route, credentials=False)
                self.assertEqual(status, 401)
                self.assertIn("WWW-Authenticate", headers)

    def test_valid_credentials_open_dashboard_from_remote_host(self):
        status, _, body = self.request("/", host="server.example:8765")
        self.assertEqual(status, 200)
        self.assertIn(b'account-form', body)
        status, _, body = self.request(host="server.example:8765")
        self.assertEqual(status, 200)
        self.assertIn("holidays", json.loads(body)["schedule_modes"])

    def test_invalid_credentials_do_not_expose_data(self):
        for auth in ("Basic invalid%%%", "Bearer test", "Basic " + base64.b64encode(b"admin:wrong").decode()):
            with self.subTest(auth=auth):
                self.assertEqual(self.request(extra={"Authorization": auth})[0], 401)

    def test_same_origin_account_save_encryption_and_restart(self):
        payload = {"name": "Integration", "username": "student", "password": "student-password",
                   "enabled": False, "schedule_mode": "holidays", "time_mode": "random",
                   "check_time": "09:00", "check_time_end": "10:00",
                   "check_location": {"latitude": 22.817, "longitude": 108.366, "radius": 100}}
        host = "server.example:8765"
        status, _, body = self.request("/api/accounts", method="POST", payload=payload, host=host,
                                       origin="http://" + host)
        self.assertEqual(status, 200, body)
        details = json.loads(self.request()[2])["accounts"][0]
        self.assertEqual(details["schedule_mode"], "holidays")
        self.assertEqual(details["time_mode"], "random")
        self.assertNotIn("password", details)
        with sqlite3.connect(self.directory / "practice.sqlite3") as db:
            encrypted = db.execute("SELECT password FROM accounts").fetchone()[0]
        self.assertNotIn(b"student-password", encrypted)
        self.assertEqual(SecretStore(self.directory).unseal(encrypted), b"student-password")

    def test_authenticated_cross_origin_or_missing_origin_posts_are_rejected(self):
        for origin in ("https://attacker.example", None):
            with self.subTest(origin=origin):
                status, _, _ = self.request("/api/accounts/delete", method="POST", payload={"id": 1}, origin=origin)
                self.assertEqual(status, 403)
        self.assertEqual(self.request(extra={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_server_mode_requires_a_password_and_local_default_stays_restricted(self):
        with patch.dict(os.environ, {"PRACTICE_ADMIN_PASSWORD": ""}):
            with self.assertRaisesRegex(WebError, "PRACTICE_ADMIN_PASSWORD"):
                with open_web_server("0.0.0.0", 0, data_dir=self.directory, server_mode=True):
                    pass
        with self.assertRaisesRegex(WebError, "HTTPS"):
            with open_web_server("0.0.0.0", 0, data_dir=self.directory):
                pass

    def test_proxy_origin_is_explicit_and_forwarded_headers_are_not_trusted(self):
        self.server.access.origin = "https://practice.example"
        self.server.access.host = "practice.example"
        self.assertEqual(self.request(host="practice.example", origin="https://practice.example")[0], 200)
        self.assertEqual(self.request(host="practice.example", origin="https://attacker.example",
                                      extra={"X-Forwarded-Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request(host="attacker.example")[0], 403)


@unittest.skipIf(sys.platform == "win32", "Linux service lifecycle")
class LifecycleTests(unittest.TestCase):
    def test_sigterm_stops_server_cleanly_and_retains_data(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "PRACTICE_ADMIN_PASSWORD": "lifecycle-test-password", "PRACTICE_ADMIN_PASSWORD_FILE": "",
                   "PRACTICE_PUBLIC_ORIGIN": ""}
            process = subprocess.Popen([sys.executable, "-m", "practice_tool", "web", "--server", "--port", "0",
                                        "--data-dir", directory], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env=env)
            try:
                started = process.stdout.readline()
                self.assertIn("PracticeTool 已监听", started)
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue((Path(directory) / "secret.key").exists())
                with sqlite3.connect(Path(directory) / "practice.sqlite3") as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
