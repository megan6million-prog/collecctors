"""Browser Harness — Playwright wrapper with Qwen vision support + stealth + proxy."""
import asyncio, base64, json, os, re
from playwright.async_api import async_playwright
from config import HEADLESS, PROFILE_DIR, get_proxy
from models.qwen_client import analyze_screenshot
from stealth import (
    STEALTH_ARGS, STEALTH_UA, STEALTH_INIT_JS, apply_stealth,
    human_pause, human_click, human_type,
)

_browser = None
_context = None
_page = None
_source = None   # the platform this browser session is for (selects the proxy)
_playwright = None

def set_source(source):
    """Tell the harness which platform it's driving, so get_browser() picks the
    right per-platform proxy. Call before the first get_browser() of a session."""
    global _source
    _source = source

async def get_browser():
    global _browser, _context, _page
    if _page and not _page.is_closed():
        return _page
    proxy = get_proxy(_source)
    # If a proxy is required, try candidates until one actually completes a
    # navigation in Chromium (urllib-healthy != Chromium-healthy for some IPs).
    candidates = [proxy]
    if proxy and os.getenv("WEBSHARE_TOKEN"):
        try:
            from .proxy_pool import fetch_proxies
            for p in fetch_proxies():
                cand = {"server": "http://%s:%d" % (p["ip"], p["port"]),
                        "username": proxy.get("username"), "password": proxy.get("password")}
                if cand["server"] != proxy["server"]:
                    candidates.append(cand)
        except Exception:
            pass

    last_err = None
    for cand in candidates[:6]:
        try:
            return await _launch(cand)
        except Exception as e:
            last_err = e
            await _teardown()
            if cand:
                print(f"[harness] proxy {cand.get('server')} failed ({str(e)[:50]}); rotating", flush=True)
            else:
                break  # no-proxy launch failing is fatal
    if last_err:
        raise last_err
    return await _launch(None)


async def _launch(proxy):
    """Launch the persistent context with the given proxy and verify reachability."""
    global _browser, _context, _page, _playwright
    _playwright = await async_playwright().start()
    launch_kwargs = dict(
        headless=HEADLESS,
        has_touch=True,
        user_agent=STEALTH_UA,
        locale="en-US",
        timezone_id="Africa/Kampala",
        viewport={"width": 1366, "height": 768},
        args=STEALTH_ARGS,
        ignore_default_args=["--enable-automation"],
    )
    if proxy:
        launch_kwargs["proxy"] = proxy
        print(f"[harness] using proxy {proxy.get('server')} for source={_source}", flush=True)
    base_profile = PROFILE_DIR or os.path.expanduser("~/.vfl_betting_profile")
    profile_path = f"{base_profile}_{_source}" if _source else base_profile
    _browser = await _playwright.chromium.launch_persistent_context(
        profile_path,
        **launch_kwargs,
    )
    _context = _browser
    await apply_stealth(_context)
    _page = _context.pages[0] if _context.pages else await _context.new_page()
    try:
        await _page.evaluate(STEALTH_INIT_JS)
    except Exception:
        pass
    # If using a proxy, verify it can actually complete an HTTPS navigation in
    # Chromium — this is what catches IPs that pass a urllib check but fail here.
    if proxy:
        await _page.goto("https://api.ipify.org", timeout=20000, wait_until="domcontentloaded")
    return _page

async def _teardown():
    global _browser, _context, _page, _playwright
    try:
        if _context:
            await _context.close()
    except Exception:
        pass
    try:
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    _browser = _context = _page = _playwright = None

async def close_browser():
    global _browser, _context, _page, _playwright
    try:
        if _context:
            await _context.close()
    except Exception:
        pass
    try:
        if _browser and hasattr(_browser, "close"):
            await _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    _browser = _context = _page = _playwright = None

async def screenshot():
    page = await get_browser()
    path = f"/tmp/vfl_ss_{asyncio.get_running_loop().time():.0f}.png"
    await page.screenshot(path=path)
    return path

async def screenshot_b64():
    page = await get_browser()
    return base64.b64encode(await page.screenshot()).decode()

async def click_text(text, ctx=None):
    p = ctx or await get_browser()
    el = p.locator(f"text={text}").first
    if await el.count() > 0 and await el.is_visible():
        await el.click()
        return True
    return False

async def click_xy(x, y, ctx=None):
    p = ctx or await get_browser()
    # Human-like move-then-click instead of an instant teleport click.
    await human_click(p, x, y)
    return True

async def wait(seconds):
    await asyncio.sleep(seconds)

async def get_text(ctx=None):
    p = ctx or await get_browser()
    return await p.inner_text("body")

async def evaluate(js, ctx=None):
    p = ctx or await get_browser()
    return await p.evaluate(js)

async def qwen_analyze(instruction):
    img_b64 = await screenshot_b64()
    return await analyze_screenshot(img_b64, instruction)

async def qwen_find_and_click(instruction):
    analysis = await qwen_analyze(instruction)
    page = await get_browser()
    coords = re.findall(r"(\d{1,4})\s*,\s*(\d{1,4})", analysis)
    if coords:
        x, y = int(coords[0][0]), int(coords[0][1])
        await page.mouse.click(x, y)
        return True, f"Clicked at ({x},{y})"
    text_match = re.findall(r"(?:click|press|tap)\s+(?:on\s+)?['\"]?([A-Za-z\s]+)['\"]?", analysis, re.I)
    if text_match:
        return False, f"Suggested text click: '{text_match[0]}'"
    return False, f"Could not determine target. Analysis: {analysis[:200]}"
