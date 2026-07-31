import logging
import asyncio
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Known ad-tech domains observed in icelanders.st-style embed pages.
# Blocking these speeds up page load and avoids wasted script execution.
AD_BLOCK_DOMAINS = [
    "tabretwicht.com", "effectivecpmnetwork.com", "cleverwebserver.com",
    "adsboosters.xyz", "histats.com", "aclib", "cobnutscopsole.com",
]


class BrowserPool:
    """Maintains a single persistent headless Chromium instance for the
    lifetime of the app, avoiding per-request browser launch overhead.
    Only lightweight browser contexts are created/destroyed per extraction.
    """
    _playwright = None
    _browser = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_browser(cls):
        async with cls._lock:
            if cls._browser is None or not cls._browser.is_connected():
                logger.info("BrowserPool: launching persistent Chromium instance")
                cls._playwright = await async_playwright().start()
                cls._browser = await cls._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-gpu",
                    ],
                )
        return cls._browser

    @classmethod
    async def close(cls):
        if cls._browser:
            try:
                await cls._browser.close()
            except Exception:
                pass
            cls._browser = None
        if cls._playwright:
            try:
                await cls._playwright.stop()
            except Exception:
                pass
            cls._playwright = None


async def extract_via_browser_capture(iframe_url: str, referer: str, timeout: int = 12) -> dict | None:
    """Loads iframe_url in a shared headless browser context and captures
    the first .m3u8 network request (URL + request headers).

    Used as a fallback for JS-heavy embed pages (e.g. icelanders.st) where
    the stream URL is not present in static HTML/JS and only appears after
    the page's own JavaScript executes and fires a network request.
    """
    browser = await BrowserPool.get_browser()
    captured: dict = {}
    done = asyncio.Event()

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        ignore_https_errors=True,
        extra_http_headers={"Referer": referer},
    )
    page = await context.new_page()

    async def route_filter(route):
        req = route.request
        if req.resource_type in ("image", "font", "stylesheet", "media"):
            await route.abort()
        elif any(d in req.url for d in AD_BLOCK_DOMAINS):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", route_filter)

    def on_request(request):
        if ".m3u8" in request.url and not captured:
            captured["url"] = request.url
            captured["headers"] = dict(request.headers)
            done.set()

    page.on("request", on_request)

    try:
        await page.goto(iframe_url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug("BrowserPool: capture failed for %s: %s", iframe_url, e)
    finally:
        await context.close()  # cheap - shared browser process stays alive

    return captured if captured.get("url") else None
