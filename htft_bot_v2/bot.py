#!/usr/bin/env python3
"""
Betpawa HTFT Bot — Self-contained Railway deployment
Uses betpawa API for match discovery + Playwright for bet placement.
"""
import asyncio, os, sys, json, logging, random, requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Env config ────────────────────────────────────────────────────────
PHONE     = os.environ.get('BP_PHONE', '0705949189')
PIN       = os.environ.get('BP_PIN',   'password')
STAKE     = int(os.environ.get('STAKE_UGX', '1'))
DRY_RUN   = os.environ.get('DRY_RUN', 'true').lower() == 'true'

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('bot')

# ── Betpawa API ────────────────────────────────────────────────────────
BP_H = {
    'x-pawa-brand': 'betpawa-uganda', 'x-pawa-language': 'en',
    'devicetype': 'web', 'user-agent': 'Mozilla/5.0',
}
BASE    = 'https://www.betpawa.ug/api/sportsbook/virtual'
ENG_ID  = '7794'

def get_upcoming_matches():
    """Return English League matches from the next open round."""
    try:
        now = datetime.now(timezone.utc)
        d   = requests.get(f'{BASE}/v2/seasons/list/actual', headers=BP_H, timeout=10).json()
        for season in d.get('items', []):
            for rnd in season.get('rounds', []):
                t   = rnd.get('tradingTime', {})
                end = datetime.fromisoformat(t['end'].replace('Z', '+00:00'))
                if end < now: continue
                ed  = requests.get(f'{BASE}/v3/events/list/by-round/{rnd["id"]}',
                                   headers=BP_H, timeout=10).json()
                matches = []
                for e in ed.get('responses', []):
                    if str(e.get('competition', {}).get('id', '')) != ENG_ID: continue
                    parts = e.get('name', '').split(' - ')
                    if len(parts) == 2:
                        matches.append({'home': parts[0].strip(), 'away': parts[1].strip(),
                                        'round_id': rnd['id']})
                if matches:
                    return matches
    except Exception as ex:
        log.error(f"API error: {ex}")
    return []

# ── HTFT Matrix (full 380 pairs) ───────────────────────────────────────
# Auto-generated from betpawa_htft_brain.py
HTFT_MATRIX = {
    "ARS v SUN": [("X/1",4.35,0.3571,0.5536)],
    "AST v NOT": [("X/1",5.75,0.44,1.53)],
    "BHA v AST": [("X/2",8.0,0.3929,2.1429)],
    "BHA v BOU": [("2/2",6.0,0.3333,1.0)],
    "BHA v LIV": [("X/2",6.5,0.3077,1.0)],
    "BOU v NOT": [("X/2",8.5,0.2593,1.2037)],
    "BRE v ARS": [("X/2",5.25,0.25,0.3125)],
    "BRE v MUN": [("2/2",3.4,0.3333,0.1333)],
    "CHE v ARS": [("1/2",35.0,0.12,3.2)],
    "CHE v BHA": [("X/2",10.5,0.2692,1.8269)],
    "CHE v MCI": [("1/1",3.0,0.48,0.44)],
    "CHE v SUN": [("X/2",17.0,0.2333,2.9667)],
    "CRY v ARS": [("2/2",3.15,0.3462,0.0904)],
    "CRY v CHE": [("2/2",3.45,0.4,0.38)],
    "CRY v MCI": [("2/2",3.3,0.3793,0.2517)],
    "LEE v ARS": [("X/2",5.5,0.2609,0.4348), ("2/2",2.8,0.3913,0.0957)],
    "LEE v BHA": [("2/2",3.5,0.3333,0.1667)],
    "LEE v TOT": [("2/2",3.1,0.3939,0.2212)],
    "LIV v SUN": [("X/1",4.5,0.3438,0.5469)],
    "MCI v BRE": [("1/1",2.3,0.4615,0.0615)],
    "MCI v NOT": [("X/X",9.0,0.2069,0.8621)],
    "MUN v LIV": [("1/X",14.5,0.1562,1.2656)],
    "MUN v NOT": [("1/1",2.21,0.5417,0.1971)],
    "MUN v SUN": [("1/X",18.5,0.2,2.7)],
    "NEW v ARS": [("2/2",4.45,0.4074,0.813)],
    "NEW v BHA": [("1/2",37.0,0.0938,2.4688)],
    "NOT v BRE": [("X/2",8.75,0.3478,2.0435)],
    "NOT v MUN": [("1/X",18.5,0.1562,1.8906)],
    "SUN v ARS": [("2/2",2.55,0.4783,0.2196)],
    "SUN v BOU": [("2/2",3.65,0.4,0.46)],
    "TOT v MCI": [("2/1",27.0,0.1304,2.5217)],
    "TOT v SUN": [("1/1",1.9,0.5312,0.0094)],
}

import base64

def log_screenshot(page_obj, label):
    """Fire-and-forget: save screenshot and print as base64 to logs."""
    async def _do():
        try:
            path = f'/tmp/{label}.png'
            await page_obj.screenshot(path=path)
            with open(path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            # Print first 200 chars of b64 as marker — paste full output to decode
            log.info(f"[SCREENSHOT:{label}] {b64[:200]}...TRUNCATED({len(b64)}chars)")
        except Exception as e:
            log.warning(f"Screenshot failed: {e}")
    return _do()

# ── Browser helpers ────────────────────────────────────────────────────
async def login(page):
    await page.goto('https://www.betpawa.ug', timeout=30000)
    await asyncio.sleep(4)
    body = await page.inner_text('body')
    if 'LOGIN' not in body[:500].upper():
        log.info("Already logged in"); return True
    await page.goto('https://www.betpawa.ug/login', timeout=15000)
    await asyncio.sleep(3)
    ph = page.locator('#phoneNumber, input[name=username]').first
    if await ph.count(): await ph.fill(PHONE)
    pw = page.locator('input[name=password], input[type=password]').first
    if await pw.count(): await pw.fill(PIN)
    await asyncio.sleep(1)
    await pw.press('Enter')
    await asyncio.sleep(8)
    if await page.query_selector('#phoneNumber'):
        log.error("Login failed"); return False
    log.info("Logged in ✓"); return True

async def place_bet(page, home, away, market, stake):
    try:
        await page.goto('https://www.betpawa.ug/virtual-sports?virtualTab=upcoming&leagueId=7794',
                        timeout=30000)
        await asyncio.sleep(7)

        # Click match — format confirmed as "HOME - AWAY"
        match_text = f'{home} - {away}'
        match_el = page.get_by_text(match_text, exact=False).first
        if not await match_el.count():
            body = await page.inner_text('body')
            log.warning(f"Match not found: '{match_text}' | page: {body[200:400]}")
            await log_screenshot(page, f'no_match_{home}_{away}')
            return False
        await match_el.click()
        await asyncio.sleep(4)

        # All HTFT outcomes are directly visible after clicking match — NO tab click needed
        # Confirmed from local testing: 1/1, 1/X, 1/2, X/1, X/X, X/2, 2/1, 2/X, 2/2 all visible
        outcome_el = page.get_by_text(market, exact=True).first
        if not await outcome_el.count():
            body = await page.inner_text('body')
            log.warning(f"Outcome '{market}' not found | page: {body[200:400]}")
            await log_screenshot(page, f'no_outcome_{market}')
            return False
        await outcome_el.click()
        await asyncio.sleep(2)

        # Set stake
        inp = page.locator('input[placeholder*="stake" i]').first
        if await inp.count():
            await inp.click()
            await page.keyboard.press('Control+A')
            await page.keyboard.type(str(stake))
            await asyncio.sleep(0.5)

        # Check available buttons
        btns = await page.locator('button').all_inner_texts()
        log.info(f"Buttons: {[b.strip() for b in btns if b.strip()][:8]}")

        for btn_txt in ['Place Bet', 'Place bet', 'Confirm', 'BET', 'Bet']:
            btn = page.get_by_role('button', name=btn_txt)
            if await btn.count():
                await btn.click()
                await asyncio.sleep(4)
                await log_screenshot(page, f'placed_{home}_{away}_{market}')
                log.info(f"✓ {home} v {away} | {market} | {stake} UGX")
                return True

        await log_screenshot(page, 'no_place_btn')
        log.warning(f"No place bet button. All buttons: {btns}")
        return False

    except Exception as e:
        log.error(f"Error: {e}")
        await log_screenshot(page, 'error')
        return False

# ── Main ───────────────────────────────────────────────────────────────
async def run():
    log.info(f"Bot starting | stake={STAKE} UGX | dry_run={DRY_RUN}")
    log.info(f"Matrix: {len(HTFT_MATRIX)} pairs | {sum(len(v) for v in HTFT_MATRIX.values())} signals")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-setuid-sandbox',
                  '--disable-dev-shm-usage','--disable-gpu']
        )
        page = await browser.new_page()
        if not await login(page):
            log.error("Cannot login"); await browser.close(); return

        last_round = None
        total_placed = 0

        while True:
            try:
                matches = get_upcoming_matches()
                log.info(f"Upcoming: {[m['home']+' v '+m['away'] for m in matches]}")

                if not matches:
                    await asyncio.sleep(60); continue

                round_id = matches[0]['round_id']
                if round_id == last_round:
                    await asyncio.sleep(60); continue

                last_round = round_id
                log.info(f"New round: {round_id}")

                for m in matches:
                    key  = f"{m['home']} v {m['away']}"
                    sigs = HTFT_MATRIX.get(key, [])
                    if not sigs: continue
                    log.info(f"  {key} → {len(sigs)} bets: {[s[0] for s in sigs]}")
                    for market, odds, p_win, ev in sigs:
                        ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        tag = '[DRY]' if DRY_RUN else '[LIVE]'
                        log.info(f"{tag} {key} | {market} @ {odds} | EV={ev:.1f}")
                        if not DRY_RUN:
                            ok = await place_bet(page, m['home'], m['away'], market, STAKE)
                            if ok: total_placed += 1
                        await asyncio.sleep(random.uniform(1, 2))

                log.info(f"Round done | total placed: {total_placed}")

            except Exception as e:
                import traceback
                log.error(f"Loop: {e}\n{traceback.format_exc()}")

            await asyncio.sleep(180)

        await browser.close()

if __name__ == '__main__':
    asyncio.run(run())
