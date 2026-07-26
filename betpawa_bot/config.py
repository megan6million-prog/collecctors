"""
VFL Betting Engine — Configuration
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
# Fallback to backend/.env if running from outside
backend_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", ".env")
if os.path.exists(backend_env):
    load_dotenv(backend_env)

# EC2 Backend
ENGINE_URL = os.getenv("ENGINE_URL", "http://127.0.0.1:8001")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "voltrix_analytics_2026")

# Model Server (Qwen2.5-VL-3B)
MODEL_SERVER = os.getenv("MODEL_SERVER", "http://100.52.221.75:8000")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5-vl-3b-q4.gguf")

# Browser Settings
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
PROFILE_DIR = os.getenv("PROFILE_DIR", os.path.expanduser("~/.vfl_betting_profile"))

# ─── Residential proxy ────────────────────────────────────────────────────────
# A global proxy applied to the browser. Format expected by Playwright:
#   PROXY_SERVER = "http://host:port"  (or socks5://host:port)
#   PROXY_USERNAME / PROXY_PASSWORD for authenticated residential proxies.
# Per-platform overrides let you route ONLY the bot-walled site (bongobongo)
# through residential while others go direct (saves proxy bandwidth).
PROXY_SERVER = os.getenv("PROXY_SERVER", "")
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

# Per-platform proxy routing.
#   "pool"   -> use the health-checked Webshare proxy pool (for bot-walled sites)
#   "direct" -> never use a proxy
#   "<url>"  -> use a specific static proxy server
# Only bongobongo is proxied for now (Webshare free tier is unreliable, so we
# keep the blast radius to the one site that actually needs it to get past its
# anti-bot wall). The other three connect directly.
PLATFORM_PROXY = {
    "bongobongo": os.getenv("BONGO_PROXY", "pool"),
    "betpawa": os.getenv("BETPAWA_PROXY", "direct"),
    "bangbet": os.getenv("BANGBET_PROXY", "direct"),
    "betkraft": os.getenv("BETKRAFT_PROXY", "direct"),
}


def get_proxy(source=None):
    """Return a Playwright proxy dict for the given source, or None for direct.

    Routing is driven entirely by PLATFORM_PROXY:
      - 'direct'  -> None (no proxy) — this is the default for all sites except bongobongo
      - 'pool'    -> auto-pick a healthy proxy from the Webshare pool
      - '<url>'   -> use that explicit proxy server
    Unknown/None sources default to direct, so the proxy can never leak to a
    site that isn't explicitly opted in."""
    mode = PLATFORM_PROXY.get(source, "direct")

    if mode == "direct" or not mode:
        return None

    if mode == "pool":
        if not os.getenv("WEBSHARE_TOKEN"):
            return None
        try:
            from proxy_pool import pick_working_proxy
            picked = pick_working_proxy()
            if picked:
                return {k: v for k, v in picked.items() if not k.startswith("_")}
        except Exception:
            pass
        return None

    # explicit static proxy URL
    server = mode
    proxy = {"server": server}
    if PROXY_USERNAME:
        proxy["username"] = PROXY_USERNAME
    if PROXY_PASSWORD:
        proxy["password"] = PROXY_PASSWORD
    return proxy


# Betting Defaults
DEFAULT_STAKE = int(os.getenv("DEFAULT_STAKE", "500"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "0.10"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.75"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Per-platform Credentials
CREDENTIALS = {
    "bongobongo": {
        "phone": os.getenv("BONGO_PHONE", "0705949189"),
        "pin": os.getenv("BONGO_PIN", "4413"),
        "login_url": "https://www.bongobongo.ug/login",
        "game_url": "https://www.bongobongo.ug/game/info/1x2-gaming-virtual-soccer",
        "sports_live_url": "https://www.bongobongo.ug/sports/live",
    },
    "betpawa": {
        "phone": os.getenv("BP_PHONE", "0705949189"),
        "pin": os.getenv("BP_PIN", "password"),
        "login_url": "https://www.betpawa.ug/login",
    },
    "bangbet": {
        "phone": os.getenv("BANGBET_PHONE", "0705949189"),
        "pin": os.getenv("BANGBET_PIN", "password"),
        "login_url": "https://ug.bandabets.com/login",
    },
    "betkraft": {
        "phone": os.getenv("BETKRAFT_PHONE", "0705949189"),
        "pin": os.getenv("BETKRAFT_PIN", "password"),
        "login_url": "https://vl.betkraft.co.uk/login",
    },
}

def load_db_credentials():
    import psycopg2
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        backend_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", ".env")
        if os.path.exists(backend_env):
            with open(backend_env) as f:
                for line in f:
                    if line.strip().startswith("DATABASE_URL="):
                        db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    if db_url:
        try:
            conn = psycopg2.connect(db_url)
            with conn.cursor() as cur:
                cur.execute("SELECT site, username, password FROM site_accounts")
                for site, username, password in cur.fetchall():
                    if site in CREDENTIALS:
                        if username: CREDENTIALS[site]["phone"] = username
                        if password: CREDENTIALS[site]["pin"] = password
            conn.close()
        except Exception as e:
            print(f"[config] Warning: Could not load credentials from DB site_accounts: {e}")

try:
    load_db_credentials()
except Exception:
    pass

# Supported Leagues per Platform
PLATFORM_LEAGUES = {
    "bongobongo": ["English", "Spanish", "World Cup", "Chile"],
    "betpawa": ["English League", "German League", "Italian League", "Spanish League", 
                "French League", "Dutch League", "Portuguese League"],
    "bangbet": ["English League", "World Cup"],
    "betkraft": ["English"],
}

# Prediction types per source
SOURCE_PREDICTION_ENDPOINTS = {
    "bongobongo": "/api/engine/v3/predict?source=bongobongo&league=English",
    "betpawa": "/api/engine/v3/predict?source=betpawa&league=English+League",
    "bangbet": "/api/engine/v3/predict?source=bangbet&league=English+League",
    "betkraft": "/api/engine/v3/predict?source=betkraft&league=English",
}

# Market types that are bettable
BETTABLE_MARKETS = {
    "1X2": {"columns": 3, "labels": ["1", "X", "2"]},
    "O/U": {"columns": 2, "labels": ["Over", "Under"]},
    "GG": {"columns": 2, "labels": ["Yes", "No"]},
    "DC": {"columns": 3, "labels": ["1X", "12", "X2"]},
}
