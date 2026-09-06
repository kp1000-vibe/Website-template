"""Chrome under your control, not a fresh headless robot.

Chrome 136 and later refuse remote debugging against the default profile
directory, so the agent uses its own profile at ~/.jobagent/chrome-profile. You
log into LinkedIn, Google and anything else there once, and the sessions persist.
That profile is a real browser you can also use by hand.
"""

from __future__ import annotations

import logging
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAC_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
LINUX_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
WINDOWS_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    system = platform.system()
    if system == "Darwin":
        return next((p for p in MAC_PATHS if Path(p).exists()), None)
    if system == "Windows":
        return next((p for p in WINDOWS_PATHS if Path(p).exists()), None)
    for candidate in LINUX_NAMES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def launch(port: int, profile_dir: Path, binary: str | None = None) -> subprocess.Popen | None:
    """Start Chrome with the debugging port. No op if it is already listening."""
    if port_open(port):
        log.info("chrome already listening on %s", port)
        return None
    chrome = find_chrome(binary)
    if not chrome:
        raise RuntimeError(
            "could not find Chrome. Install it, or set browser.chrome_binary in config.yaml"
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    log.info("launching chrome with profile %s", profile_dir)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if port_open(port):
            return proc
        time.sleep(0.25)
    raise RuntimeError(f"chrome did not open the debugging port {port} in time")


class Browser:
    """Context manager wrapping a CDP connection to your running Chrome."""

    def __init__(self, port: int, profile_dir: Path, binary: str | None = None):
        self.port = port
        self.profile_dir = profile_dir
        self.binary = binary
        self._pw = None
        self.browser = None
        self.context = None

    def __enter__(self) -> "Browser":
        from playwright.sync_api import sync_playwright

        launch(self.port, self.profile_dir, self.binary)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.context = (
            self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        )
        return self

    def new_page(self, url: str):
        page = self.context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return page

    def __exit__(self, *exc) -> None:
        # We never close the browser. The whole point is that the tabs stay open
        # for you to look at and submit.
        if self._pw:
            self._pw.stop()
