import logging
import time
from typing import Dict, Any

from browser_pool import extract_via_browser_capture

logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    pass


# Manually sourced channel slug -> icelanders.st embed path mapping.
# Populate this as you discover working slugs via DevTools on dlhd.st.
# Example: "fox-sports-504" maps directly to the embed URL path segment.
CHANNEL_SLUGS: dict[str, str] = {
    "fox-sports-504": "fox-sports-504",
    # "espn-1": "espn-1",
    # add more as you source them manually
}

ICELANDERS_BASE = "https://logic.icelanders.st/embed"

# Cache of browser-captured signed URLs: slug -> (m3u8_url, headers, expiry_ts)
_signed_url_cache: dict[str, tuple[str, dict, float]] = {}
SIGNED_URL_CACHE_TTL = 240  # seconds - adjust after observing real CDN expiry


class IcelandersExtractor:
    """Dedicated extractor for logic.icelanders.st embed pages.

    Unlike DLStreamsExtractor, this always uses the headless-browser
    capture path since icelanders.st embeds require JS execution to
    reveal the signed .m3u8 URL (no atob/source/XOR pattern present
    in static HTML).

    Channels are referenced by slug (e.g. "fox-sports-504"), sourced
    manually via browser DevTools rather than resolved dynamically
    from dlhd.st, since domain assignment there is not controllable.
    """

    def __init__(self, request_headers: dict = None, proxies: list = None, bypass_warp: bool = False):
        self.request_headers = request_headers or {}
        self.proxies = proxies or []
        self.bypass_warp_active = bypass_warp
        self.mediaflow_endpoint = "hls_manifest_proxy"

    @staticmethod
    def _resolve_slug(channel_ref: str) -> str:
        """Accepts either a known alias or a raw slug and returns the
        actual icelanders.st path segment to use."""
        return CHANNEL_SLUGS.get(channel_ref, channel_ref)

    async def extract(self, channel_ref: str, **kwargs) -> Dict[str, Any]:
        """Extracts the M3U8 URL and headers for a given icelanders.st channel.

        channel_ref: either a key from CHANNEL_SLUGS or a raw slug
        matching the embed path, e.g. "fox-sports-504".
        """
        slug = self._resolve_slug(channel_ref)
        embed_url = f"{ICELANDERS_BASE}/{slug}"

        cached = _signed_url_cache.get(slug)
        if cached and cached[2] > time.time():
            logger.debug("Icelanders: using cached URL for %s", slug)
            stream_url, headers, _ = cached
        else:
            logger.info("Icelanders: launching browser capture for %s", embed_url)
            browser_result = await extract_via_browser_capture(embed_url, referer=embed_url)
            if not browser_result:
                raise ExtractorError(f"Icelanders: browser capture failed for {embed_url}")
            stream_url = browser_result["url"]
            headers = browser_result.get("headers", {})
            _signed_url_cache[slug] = (stream_url, headers, time.time() + SIGNED_URL_CACHE_TTL)
            logger.info("Icelanders: captured stream URL for %s: %s", slug, stream_url)

        playback_headers = {
            "Referer": f"{ICELANDERS_BASE.rsplit('/embed', 1)[0]}/",
            "Origin": ICELANDERS_BASE.rsplit("/embed", 1)[0],
            "User-Agent": headers.get(
                "user-agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            ),
            "Accept": "*/*",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

        return {
            "destination_url": stream_url,
            "request_headers": playback_headers,
            "mediaflow_endpoint": self.mediaflow_endpoint,
            "captured_manifest": None,
            "captured_manifests": {stream_url: ""},
        }

    async def close(self):
        pass  # No persistent session to close; BrowserPool is shared app-wide
