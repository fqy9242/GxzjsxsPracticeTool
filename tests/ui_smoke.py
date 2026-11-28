"""Optional browser smoke test: install Playwright + Chromium, then run this file."""

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from practice_tool.web import open_web_server


def main():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "PRACTICE_ADMIN_USERNAME": "admin", "PRACTICE_ADMIN_PASSWORD": "ui-test-password-123",
        "PRACTICE_ADMIN_PASSWORD_FILE": "", "PRACTICE_PUBLIC_ORIGIN": "",
    }), patch("practice_tool.client.PracticeClient._post", side_effect=AssertionError("Offline UI test")):
        with open_web_server("127.0.0.1", 0, data_dir=Path(directory), server_mode=True) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        context = browser.new_context(http_credentials={"username": "admin", "password": "ui-test-password-123"})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.wait_for_function("document.querySelector('#service').textContent === '调度运行中'")
                        page.locator("#add-account").click()
                        page.locator("#account-name").fill("UI test")
                        page.locator("#account-username").fill("offline-test")
                        page.locator("#account-password").fill("offline-password")
                        assert page.locator("#location-mode").input_value() == "site"
                        assert page.locator("#location-mode option[value=current]").is_disabled()
                        assert page.locator("#schedule-days option").count() == 6
                        page.locator("#site-latitude").fill("22.817")
                        page.locator("#site-longitude").fill("108.366")
                        page.locator("#schedule-days").select_option("holidays")
                        page.locator("#time-mode").select_option("random")
                        page.locator("#schedule-time").fill("09:00")
                        page.locator("#schedule-time-end").fill("10:30")
                        page.locator("#save-account").click()
                        page.wait_for_function("!document.querySelector('#account-dialog').open")
                        saved = server.accounts.overview()["accounts"][0]
                        assert saved["schedule_mode"] == "holidays" and saved["time_mode"] == "random"
                        assert saved["check_time_end"] == "10:30"
                        assert saved["check_location"]["latitude"] == 22.817
                        assert not saved["enabled"], "UI test must not enable check-in"
                        page.get_by_role("button", name="编辑", exact=True).click()
                        assert page.locator("#time-mode").input_value() == "random"
                        assert page.locator("#schedule-days").input_value() == "holidays"
                        assert page.locator("#schedule-time-end").input_value() == "10:30"
                        page.locator("#schedule-time-end").fill("08:00")
                        page.locator("#schedule-time-end").blur()
                        assert not page.locator("#account-form").evaluate("form => form.checkValidity()")
                        assert not errors, errors
                        print("Browser smoke test passed: login, Linux defaults, date rules, random-window save/reopen and validation.")
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    main()
