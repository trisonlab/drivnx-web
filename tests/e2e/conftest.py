import shutil
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        # The system browser when there is one, otherwise the one `playwright install` fetched.
        browser = playwright.chromium.launch(
            executable_path=shutil.which("chromium"), args=["--no-sandbox"]
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    yield context.new_page()
    context.close()
