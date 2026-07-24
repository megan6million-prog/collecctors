#!/usr/bin/env python3
"""
betpawa_htft_bot.py — Standalone HTFT betting bot
==================================================
No DB needed. Uses the embedded HTFT pair matrix.
Places 1 UGX (minimum) bets on every +EV HTFT market
for each English League match.

Env vars:
  BP_PHONE     betpawa phone number
  BP_PIN       betpawa PIN
  STAKE_UGX    stake per bet (default: 1 = 1 shilling for tracking)
  DRY_RUN      true/false (default: true)
  LOG_FILE     path to bet log (default: /tmp/htft_bets.log)
"""

import asyncio, os, json, logging, time, random
from datetime import datetime
from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────
BP_PHONE  = os.environ.get('BP_PHONE',  '0705949189')
BP_PIN    = os.environ.get('BP_PIN',    '4413')
STAKE_UGX = int(os.environ.get('STAKE_UGX', '1'))
DRY_RUN   = os.environ.get('DRY_RUN', 'true').lower() == 'true'
LOG_FILE  = os.environ.get('LOG_FILE', '/tmp/htft_bets.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger('htft_bot')

# ── HTFT Pair Matrix (embedded — no DB needed) ────────────────────────
# Built from betpawa remeetings data: 380 pairs, fixed odds, +EV markets
# Format: 'HOME v AWAY': [('HTFT_MARKET', fixed_odds, p_win, ev), ...]
HTFT_MATRIX = {
    "ARS v AST": [("1/2", 65.0, 0.1667, 9.83), ("2/X", 15.0, 0.1667, 1.50), ("X/1", 5.0, 0.3333, 0.65)],
    "WOL v EVE": [("1/2", 61.0, 0.25, 14.25)],
    "FUL v SUN": [("1/2", 51.0, 0.2857, 13.57)],
    "BOU v EVE": [("1/2", 65.0, 0.20, 12.00), ("2/X", 14.5, 0.20, 1.90)],
    "BRE v MCI": [("1/2", 37.0, 0.3333, 11.33), ("2/X", 15.5, 0.1667, 1.58)],
    "AST v NEW": [("1/2", 35.0, 0.3333, 10.67)],
    "MCI v WHU": [("1/2", 61.0, 0.1667, 9.17), ("2/X", 17.0, 0.1667, 1.83)],
    "LIV v WHU": [("1/2", 80.0, 0.125, 9.00), ("2/X", 17.5, 0.125, 1.19)],
    "NOT v EVE": [("1/2", 50.0, 0.20, 9.00)],
    "LIV v FUL": [("1/2", 66.0, 0.1429, 8.43), ("2/X", 19.0, 0.1429, 1.71)],
    "BHA v EVE": [("1/2", 55.0, 0.1667, 8.17)],
    "CHE v BUR": [("1/2", 62.0, 0.1429, 7.86), ("2/X", 21.5, 0.2857, 5.14), ("1/1", 1.8, 0.5714, 0.03)],
    "CHE v SUN": [("1/2", 69.0, 0.125, 7.63), ("X/X", 9.0, 0.25, 1.25), ("X/2", 17.0, 0.25, 3.25)],
    "TOT v BUR": [("1/2", 49.0, 0.1667, 7.17)],
    "NEW v WOL": [("2/X", 27.0, 0.2857, 6.71), ("1/X", 16.5, 0.2857, 3.71), ("X/2", 14.5, 0.1429, 1.07)],
    "MUN v WHU": [("1/2", 53.0, 0.1429, 6.57), ("2/2", 8.0, 0.2857, 1.29)],
    "CHE v WHU": [("1/X", 15.5, 0.3333, 4.17), ("2/2", 4.1, 0.3333, 0.37)],
    "BUR v BRE": [("1/2", 37.0, 0.20, 6.40), ("2/X", 16.5, 0.20, 2.30), ("2/2", 4.3, 0.40, 0.72)],
    "EVE v MUN": [("1/2", 31.0, 0.20, 5.20), ("2/X", 18.0, 0.20, 2.60), ("2/2", 5.5, 0.20, 0.10)],
    "CHE v ARS": [("1/2", 35.0, 0.1667, 4.83), ("2/X", 16.5, 0.1667, 1.75)],
    "NOT v TOT": [("1/2", 30.0, 0.20, 5.00), ("2/X", 14.0, 0.20, 1.80), ("X/1", 7.0, 0.20, 0.40), ("X/X", 8.0, 0.20, 0.60)],
    "MCI v FUL": [("1/2", 51.0, 0.1667, 7.50)],
    "BHA v CRY": [("1/2", 49.0, 0.125, 5.13), ("2/X", 17.5, 0.125, 1.19)],
    "EVE v MCI": [("1/1", 4.0, 0.4444, 0.78), ("2/X", 18.5, 0.1111, 1.06)],
    "LEE v BOU": [("2/1", 33.0, 0.1667, 4.50), ("2/2", 4.3, 0.3333, 0.43)],
    "BUR v AST": [("1/2", 28.0, 0.1667, 3.67), ("2/X", 14.5, 0.1667, 1.42)],
    "BUR v CHE": [("1/2", 23.5, 0.20, 3.70), ("2/X", 16.0, 0.20, 2.20)],
    "BUR v FUL": [("1/2", 31.0, 0.1667, 4.17), ("2/X", 16.0, 0.1667, 1.67)],
    "SUN v WOL": [("1/2", 32.0, 0.1429, 3.57), ("2/X", 18.5, 0.1429, 1.64)],
    "TOT v LEE": [("1/2", 42.0, 0.125, 4.25), ("2/X", 20.5, 0.125, 1.56)],
    "LEE v WOL": [("1/2", 35.0, 0.1429, 4.00), ("2/X", 19.5, 0.1429, 1.79)],
    "CHE v CRY": [("1/2", 62.0, 0.125, 6.75)],
    "LIV v BRE": [("2/X", 20.0, 0.1667, 2.33)],
    "WHU v MCI": [("2/X", 18.0, 0.1667, 2.00)],
    "EVE v BHA": [("2/X", 17.5, 0.1667, 1.92)],
    "BHA v BRE": [("2/X", 19.0, 0.1111, 1.11)],
    "CRY v BOU": [("1/1", 3.9, 0.5556, 1.17), ("X/2", 6.2, 0.2222, 0.38), ("2/X", 18.0, 0.1111, 1.00)],
    "LEE v LIV": [("2/2", 3.4, 0.4444, 0.51)],
    "ARS v BUR": [("X/X", 9.0, 0.3333, 2.00)],
    "SUN v NOT": [("X/X", 8.5, 0.40, 2.40)],
    "BHA v AST": [("X/X", 7.5, 0.2857, 1.14)],
}


def log_bet(home, away, market, odds, stake, dry_run, result=None):
    ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tag = '[DRY]' if dry_run else '[LIVE]'
    line = f"{ts} {tag} {home} v {away} | HTFT {market} @ {odds} | stake={stake} UGX"
    if result: line += f" | {result}"
    log.info(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


async def login(page):
    log.info("Navigating to betpawa...")
    await page.goto('https://www.betpawa.ug', timeout=30000)
    await asyncio.sleep(4)
    body = await page.inner_text('body')
    if 'LOGIN' not in body[:500].upper() and 'JOIN' not in body[:500].upper():
        log.info("Already logged in")
        return True
    await page.goto('https://www.betpawa.ug/login', timeout=15000)
    await asyncio.sleep(3)
    phone_el = page.locator('#phoneNumber, input[name=username]').first
    if await phone_el.count() > 0:
        await phone_el.fill(BP_PHONE)
    pw_el = page.locator('input[name=password], input[type=password]').first
    if await pw_el.count() > 0:
        await pw_el.fill(BP_PIN)
    await asyncio.sleep(1)
    await pw_el.press('Enter')
    await asyncio.sleep(8)
    still_login = await page.query_selector('#phoneNumber')
    if still_login:
        log.error("Login failed")
        return False
    log.info("Logged in ✓")
    return True


async def get_balance(page):
    try:
        bal = await page.evaluate(r"""() => {
            const num = s => { const m = String(s||'').match(/[0-9][0-9,]*/); return m ? parseFloat(m[0].replace(/,/g,'')) : null; };
            for (const sel of ['[class*="balance" i]','[data-test*="balance" i]','#balance']) {
                const el = document.querySelector(sel);
                if (el) { const v = num(el.textContent); if (v !== null) return v; }
            }
            return null;
        }""")
        return bal
    except Exception:
        return None


async def get_upcoming_matches(page):
    """Navigate to English League virtuals and scrape upcoming matches."""
    await page.goto(
        'https://www.betpawa.ug/virtual-sports?virtualTab=upcoming&leagueId=7794',
        timeout=30000
    )
    await asyncio.sleep(8)
    try:
        matches = await page.evaluate(r"""() => {
            const results = [];
            const rows = document.querySelectorAll('[class*="event"], [class*="match"], [class*="fixture"]');
            rows.forEach(row => {
                const text = row.innerText || '';
                const teams = text.match(/([A-Z]{2,4})\s+[-vV]\s+([A-Z]{2,4})/);
                if (teams) results.push({home: teams[1], away: teams[2], text: text.slice(0,80)});
            });
            return results;
        }""")
        return matches or []
    except Exception as e:
        log.warning(f"Match scrape failed: {e}")
        return []


async def place_htft_bet(page, home, away, market, odds, stake):
    """Click the HTFT market for a match and place bet."""
    try:
        # Navigate to English League virtuals
        await page.goto(
            'https://www.betpawa.ug/virtual-sports?virtualTab=upcoming&leagueId=7794',
            timeout=30000
        )
        await asyncio.sleep(6)

        # Click on the match
        match_sel = f'text="{home}" >> xpath=../.. >> text="{away}"'
        match_el  = page.locator(f':text("{home}"):near(:text("{away}"))').first
        if await match_el.count() == 0:
            log.warning(f"Match not found on page: {home} v {away}")
            return False
        await match_el.click()
        await asyncio.sleep(3)

        # Click HTFT tab
        htft_tab = page.locator(':text("HT/FT"), :text("HTFT"), :text("Half Time/Full Time")').first
        if await htft_tab.count() == 0:
            log.warning("HTFT tab not found")
            return False
        await htft_tab.click()
        await asyncio.sleep(2)

        # Click the specific outcome
        outcome_el = page.locator(f':text("{market}")').first
        if await outcome_el.count() == 0:
            log.warning(f"Outcome {market} not found")
            return False
        await outcome_el.click()
        await asyncio.sleep(1)

        # Set stake
        stake_input = page.locator('input[placeholder*="stake" i], input[class*="stake" i]').first
        if await stake_input.count() > 0:
            await stake_input.triple_click()
            await stake_input.fill(str(stake))
        await asyncio.sleep(1)

        # Confirm bet
        confirm_btn = page.locator(':text("Place Bet"), :text("Confirm"), button[type=submit]').first
        if await confirm_btn.count() > 0:
            await confirm_btn.click()
            await asyncio.sleep(3)
            log.info(f"Bet placed: {home} v {away} | {market} @ {odds}")
            return True

        log.warning("Confirm button not found")
        return False

    except Exception as e:
        log.error(f"Bet failed {home} v {away} {market}: {e}")
        return False


async def run():
    log.info("="*50)
    log.info(f"HTFT Bot starting | stake={STAKE_UGX} UGX | dry_run={DRY_RUN}")
    log.info(f"Matrix: {len(HTFT_MATRIX)} pairs | {sum(len(v) for v in HTFT_MATRIX.values())} signals")
    log.info("="*50)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage']
        )
        page = await browser.new_page()

        if not await login(page):
            log.error("Cannot login. Exiting.")
            await browser.close()
            return

        last_round = None
        bets_placed = 0
        wins = 0

        while True:
            try:
                # Check balance
                bal = await get_balance(page)
                log.info(f"Balance: {bal:,.0f} UGX" if bal else "Balance: unknown")

                # Get upcoming matches
                matches = await get_upcoming_matches(page)
                log.info(f"Upcoming matches found: {len(matches)}")

                bets_this_round = []
                for m in matches:
                    home = m.get('home',''); away = m.get('away','')
                    key  = f"{home} v {away}"
                    if key not in HTFT_MATRIX:
                        continue
                    signals = HTFT_MATRIX[key]
                    for market, odds, p_win, ev in signals:
                        log_bet(home, away, market, odds, STAKE_UGX, DRY_RUN)
                        bets_this_round.append((home, away, market, odds))
                        if not DRY_RUN:
                            ok = await place_htft_bet(page, home, away, market, odds, STAKE_UGX)
                            if ok:
                                bets_placed += 1
                        await asyncio.sleep(random.uniform(0.5, 1.5))

                log.info(f"Round complete: {len(bets_this_round)} bets | total={bets_placed}")

            except Exception as e:
                log.error(f"Loop error: {e}")

            # Wait for next round (~5 min betpawa cycle)
            log.info("Waiting 4 minutes for next round...")
            await asyncio.sleep(240)

        await browser.close()


if __name__ == '__main__':
    asyncio.run(run())
