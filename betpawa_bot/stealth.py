"""
Stealth — anti-bot-detection for Playwright Chromium.

Masks the common automation fingerprints that sites (and Cloudflare/anti-fraud
on betting platforms) check for:
  - navigator.webdriver
  - missing/spoofed plugins, languages, mimeTypes
  - headless UA / "HeadlessChrome" token
  - chrome runtime object absence
  - WebGL vendor/renderer (SwiftShader gives away headless)
  - permissions query anomaly
Also provides realistic launch args, a human User-Agent, and human-like timing.
"""
import asyncio
import random

# A real, current desktop Chrome UA (headless Chromium otherwise reports HeadlessChrome).
STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Chromium launch args that reduce automation signals + are required on headless servers.
STEALTH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",   # removes navigator.webdriver=true
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1366,768",
    "--start-maximized",
    "--lang=en-US,en",
]

# Context options that look like a real browser.
STEALTH_CONTEXT = {
    "user_agent": STEALTH_UA,
    "locale": "en-US",
    "timezone_id": "Africa/Kampala",      # match the UG betting audience
    "viewport": {"width": 1366, "height": 768},
    "device_scale_factor": 1,
    "is_mobile": False,
    "has_touch": True,
    "extra_http_headers": {
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    },
}

# JS injected before any page script runs — neutralizes the detection vectors.
STEALTH_INIT_JS = r"""
// navigator.webdriver -> undefined
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// realistic languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// non-empty plugins + mimeTypes (headless has none)
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
      { name: 'Native Client', filename: 'internal-nacl-plugin' },
    ];
    arr.item = i => arr[i]; arr.namedItem = n => arr.find(p => p.name === n);
    return arr;
  }
});

// chrome runtime object (present in real Chrome, absent in headless)
window.chrome = window.chrome || { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };

// permissions.query for 'notifications' should match a real browser
const _origQuery = navigator.permissions && navigator.permissions.query;
if (_origQuery) {
  navigator.permissions.query = (params) =>
    params && params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origQuery(params);
}

// hardware that looks like a normal laptop
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 1 });

// WebGL vendor/renderer spoof (headless reports Google SwiftShader)
try {
  const getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';                 // UNMASKED_VENDOR_WEBGL
    if (p === 37446) return 'Intel Iris OpenGL Engine';   // UNMASKED_RENDERER_WEBGL
    return getParam.apply(this, [p]);
  };
} catch (e) {}

// hide the CDP/console hook some detectors look for
try { delete navigator.__proto__.webdriver; } catch (e) {}
"""


async def apply_stealth(context):
    """Apply the init script + headers to a browser context (or page)."""
    try:
        await context.add_init_script(STEALTH_INIT_JS)
    except Exception:
        pass


# ─── Human-like behaviour ───────────────────────────────────────────────────

async def human_pause(min_s=0.4, max_s=1.6):
    """Random short delay to mimic human reaction time."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_click(page, x, y):
    """Move the mouse in a couple of steps then click — not an instant teleport-click."""
    try:
        await page.mouse.move(x + random.randint(-3, 3), y + random.randint(-3, 3),
                              steps=random.randint(4, 12))
        await human_pause(0.05, 0.25)
        await page.mouse.click(x, y, delay=random.randint(40, 140))
    except Exception:
        await page.mouse.click(x, y)


async def human_type(page, selector, text):
    """Type with per-character delay instead of instant fill."""
    el = page.locator(selector).first
    await el.click()
    for ch in text:
        await el.type(ch, delay=random.randint(60, 180))
    await human_pause(0.2, 0.6)
