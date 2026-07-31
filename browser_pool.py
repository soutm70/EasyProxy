import logging
import asyncio
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

AD_BLOCK_DOMAINS = [
    "tabretwicht.com", "effectivecpmnetwork.com", "cleverwebserver.com",
    "adsboosters.xyz", "histats.com", "aclib", "cobnutscopsole.com",
]

class BrowserPool:
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
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-gpu"]
                )
        return cls._browser

    @classmethod
    async def close(cls):
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if cls._playwright:
            await cls._playwright.stop()
            cls._playwright = None


async def extract_via_browser_capture(iframe_url: str, referer: str, timeout: int = 12) -> dict | None:
    """Loads iframe_url in a shared headless browser, captures the first .m3u8 request."""
    browser = await BrowserPool.get_browser()
    captured = {}
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
        await context.close()  # cheap — browser process itself stays alive

    return captured if captured.get("url") else None
