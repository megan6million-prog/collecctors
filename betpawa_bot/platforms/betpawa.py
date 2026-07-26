"""Betpawa betting platform integration."""
import asyncio, re, random
from config import CREDENTIALS, DEFAULT_STAKE
from browser_harness import get_browser, wait, screenshot, evaluate, click_xy

PHONE = CREDENTIALS["betpawa"]["phone"]
PIN = CREDENTIALS["betpawa"]["pin"]

async def ensure_login():
    """Login to betpawa. Form: #phoneNumber (text), input[name=password], button 'Login'."""
    page = await get_browser()
    await page.goto("https://www.betpawa.ug", timeout=20000)
    await wait(4)
    body = await page.inner_text("body")
    # Already logged in if an account/balance UI is present and no JOIN prompt.
    if "LOGIN" not in body[:300].upper() and "JOIN" not in body[:300].upper():
        return True
    await page.goto("https://www.betpawa.ug/login", timeout=15000)
    await wait(3)
    # Phone field is type=text with id=phoneNumber (NOT type=tel).
    # Use fill() — it dispatches the native 'input' event React's controlled inputs require
    # (programmatic keyboard typing alone can leave React state empty).
    phone_el = page.locator("#phoneNumber, input[name=username]").first
    if await phone_el.count() > 0:
        await phone_el.fill(PHONE)
    pw_el = page.locator("input[name=password], input[type=password]").first
    if await pw_el.count() > 0:
        await pw_el.fill(PIN)
    await wait(1)
    # Submit by pressing Enter in the password field — betpawa's React form submits on
    # Enter and fires /api/user/v3/authenticate. A programmatic button click does NOT
    # trigger the submit handler here, so Enter is the reliable path.
    try:
        await pw_el.press("Enter")
    except Exception:
        try:
            await page.get_by_role("button", name="Login").first.click()
        except Exception:
            pass
    await wait(8)
    # Verify by absence of the login form, not just URL.
    still_login = await evaluate("() => !!document.querySelector('#phoneNumber')")
    return not still_login

async def get_balance():
    """Read wallet balance. Betpawa shows it in the header once logged in
    (a 'UGX ...' or money-formatted value). Robust to layout via a header-money fallback."""
    page = await get_browser()
    return await evaluate(r"""() => {
        const num = (s) => {
            if (!s) return null;
            const m = String(s).match(/[0-9][0-9,]*\.?[0-9]*/);
            return m ? parseFloat(m[0].replace(/,/g, '')) : null;
        };
        // betpawa-specific balance containers (Next.js hashed class names)
        const sels = ['[class*="balanceMeasure" i]', '[class*="_balance_" i]',
                      '[class*="balanceContainer" i]', '[class*="balance" i]',
                      '[data-test*="balance" i]', '#balance'];
        for (const sel of sels) {
            const el = document.querySelector(sel);
            if (el) { const v = num(el.textContent); if (v !== null) return v; }
        }
        // header money fallback (top bar, small money-looking text)
        for (const e of document.querySelectorAll('*')) {
            const r = e.getBoundingClientRect();
            const t = (e.textContent || '').trim();
            if (r.y < 130 && e.offsetHeight > 0 && e.children.length <= 1 &&
                /(UGX\s*)?[0-9][0-9,]*\.[0-9]{2}/.test(t) && t.length < 25) {
                return num(t);
            }
        }
        return null;
    }""")

async def navigate_to_virtuals():
    """Navigate to virtual football section."""
    page = await get_browser()
    await page.goto("https://www.betpawa.ug/virtual-sports?virtualTab=upcoming&leagueId=7794", timeout=30000)
    await wait(10)
    return True


async def _scrape_current_tab():
    """Scrape the odds-value rows currently rendered (whichever market tab is active).
    Returns a list of {home, away, vals:[...]} in row order."""
    return await evaluate(r"""() => {
        const out = [];
        const valEls = [...document.querySelectorAll('[class*=oddsValue i]')];
        const rowMap = new Map();
        const order = [];
        for (const v of valEls) {
            let row = v;
            for (let i = 0; i < 6 && row && row.parentElement; i++) {
                row = row.parentElement;
                if (/[A-Za-z]{2,5}\s*-\s*[A-Za-z]{2,5}/.test(row.innerText || "")) break;
            }
            if (!row) continue;
            if (!rowMap.has(row)) { rowMap.set(row, []); order.push(row); }
            const num = parseFloat((v.textContent || "").trim());
            if (!isNaN(num)) rowMap.get(row).push(num);
        }
        for (const row of order) {
            const txt = (row.innerText || "").replace(/\n/g, " ");
            const mt = txt.match(/([A-Za-z]{2,5})\s*-\s*([A-Za-z]{2,5})/);
            if (!mt) continue;
            out.push({ home: mt[1], away: mt[2], vals: rowMap.get(row) });
        }
        return out;
    }""")


async def _click_tab(name):
    """Click a market tab by its text. Tries exact then partial match."""
    js = """() => {
        const want = %s.toUpperCase();
        const tabs = [...document.querySelectorAll('[class*=_tab_ i], [role=tab], button, a')];
        // Try exact match first
        for (const t of tabs) {
            if ((t.textContent || '').trim().toUpperCase() === want && t.offsetHeight > 0) {
                t.click(); return true;
            }
        }
        // Try partial match
        for (const t of tabs) {
            const txt = (t.textContent || '').trim().toUpperCase();
            if ((txt.includes(want) || want.includes(txt)) && txt.length > 0 && t.offsetHeight > 0) {
                t.click(); return true;
            }
        }
        return false;
    }""" % __import__("json").dumps(name)
    return await evaluate(js)


async def get_matches_odds():
    """Extract match listings with 1x2 + O/U2.5 + BTTS odds from the betpawa
    virtuals page by scraping each market tab and merging rows by match order.

    Tab odds layouts:
      1X2  -> [home, draw, away]
      O/U  -> the page shows multiple O/U lines; we take the 2.5 pair [over, under]
      BTTS -> [yes, no]
    """
    page = await get_browser()

    # 1X2 (default/active tab)
    await _click_tab("1X2"); await wait(1.5)
    base = await _scrape_current_tab()
    matches = []
    for i, r in enumerate(base, 1):
        if len(r["vals"]) < 3:
            continue
        matches.append({"n": i, "home": r["home"], "away": r["away"],
                        "odds": {"1x2": {"1": r["vals"][0], "X": r["vals"][1], "2": r["vals"][2]}}})

    # BTTS tab -> [yes, no] per row
    if await _click_tab("BTTS"):
        await wait(1.5)
        btts_rows = await _scrape_current_tab()
        for m, r in zip(matches, btts_rows):
            if len(r["vals"]) >= 2 and r["home"] == m["home"]:
                m["odds"]["btts"] = {"yes": r["vals"][0], "no": r["vals"][1]}

    # O/U tab -> betpawa lists all 3 lines (1.5, 2.5, 3.5) as 6 values per row:
    # [over1.5, under1.5, over2.5, under2.5, over3.5, under3.5]
    # We want the 2.5 line (indices 2,3)
    if await _click_tab("O/U"):
        await wait(1.5)
        ou_rows = await _scrape_current_tab()
        for m, r in zip(matches, ou_rows):
            if len(r["vals"]) >= 4 and r["home"] == m["home"]:
                m["odds"]["ou"] = [{}, {}, {"over": r["vals"][2], "under": r["vals"][3]}]

    # restore 1X2 tab for any downstream clicking
    await _click_tab("1X2"); await wait(0.5)
    return {"matches": matches}


async def click_odd_by_index(index=0):
    page = await get_browser()
    data = await get_matches_odds()
    if index < len(data.get("odds", [])):
        o = data["odds"][index]
        await click_xy(o["x"], o["y"])
        await wait(random.uniform(0.5, 1.2))
        return True
    return False

async def set_stake(amount=DEFAULT_STAKE):
    page = await get_browser()
    amt_str = str(int(amount))
    await evaluate("""() => {
        const amt = %s;
        const inputs = document.querySelectorAll('input');
        for (let inp of inputs) {
            const ph = (inp.placeholder || '').toLowerCase();
            const t = (inp.type || '').toLowerCase();
            if (t === 'number' || ph.includes('stake') || ph.includes('amount')) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, amt);
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
                return;
            }
        }
    }""" % __import__("json").dumps(amt_str))
    await wait(random.uniform(0.3, 0.7))
    return True

async def confirm_bet():
    page = await get_browser()
    # Find and click the "Place Bet" button using Playwright locator
    import re as _re
    btn = page.locator('button').filter(has_text=_re.compile(r'place.*bet|confirm|bet now', _re.IGNORECASE))
    if await btn.count() > 0:
        await btn.first.click()
    else:
        # Fallback: any button with place/bet/confirm text
        await evaluate("""() => {
            const all = document.querySelectorAll('button');
            for (let el of all) {
                const t = el.textContent.toLowerCase().trim();
                if ((t.includes('place') || t.includes('bet now') || t.includes('confirm')) && el.offsetHeight > 0 && !el.disabled) {
                    el.click(); return;
                }
            }
        }""")
    await wait(random.uniform(1.5, 2.5))
    return True


async def click_match_odd(team, market, pick):
    """Click the specific odds button for a team/market/pick on the betpawa virtuals page."""
    tab_map = {'1X2': '1X2', 'BTTS': 'BTTS', 'OU25': 'O/U', 'HTFT': 'HT/FT'}
    cols_per_match = {"1X2": 3, "BTTS": 2, "OU25": 6, "HTFT": 9}
    idx_in_match = {
        '1X2': {'1': 0, 'W': 0, 'w': 0, 'X': 1, 'D': 1, 'd': 1, '2': 2, 'L': 2, 'l': 2},
        'BTTS': {'Yes': 0, 'No': 1},
        'OU25': {'Over': 2, 'Under': 3},
        'HTFT': {
            '1/1': 0, '1/X': 1, '1/2': 2,
            'X/1': 3, 'X/X': 4, 'X/2': 5,
            '2/1': 6, '2/X': 7, '2/2': 8
        }
    }
    tab = tab_map.get(market)
    col = idx_in_match.get(market, {}).get(pick)
    n_cols = cols_per_match.get(market, 2)
    if tab is None or col is None:
        return False

    await _click_tab(tab)
    await wait(1.5)

    # Find match index by team abbreviation
    rows = await _scrape_current_tab()
    match_idx = -1
    team_upper = team.upper()[:3]
    for i, r in enumerate(rows):
        if team_upper in r["home"].upper() or team_upper in r["away"].upper():
            match_idx = i
            break
    if match_idx < 0:
        return False

    target_btn_idx = match_idx * n_cols + col

    # Use Playwright locator click (mouse.click doesn't trigger Vue handlers)
    page = await get_browser()
    btns = page.locator('[class*="_betButton_"]')
    count = await btns.count()
    if target_btn_idx >= count:
        return False
    await btns.nth(target_btn_idx).click()
    await wait(random.uniform(0.8, 1.5))
    return True
