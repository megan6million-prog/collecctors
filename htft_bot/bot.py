#!/usr/bin/env python3
"""
betpawa_htft_bot.py — HTFT betting bot
=======================================
Uses betpawa API to get upcoming matches (no scraping).
Uses Playwright headless browser only for bet placement.

Env vars:
  BP_PHONE   betpawa phone (default: 0705949189)
  BP_PIN     betpawa PIN   (default: 4413)
  STAKE_UGX  stake per bet in UGX (default: 1)
  DRY_RUN    true/false (default: true)
"""

import asyncio, os, json, logging, time, random, requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright

BP_PHONE  = os.environ.get('BP_PHONE',  '0705949189')
BP_PIN    = os.environ.get('BP_PIN',    '4413')
STAKE_UGX = int(os.environ.get('STAKE_UGX', '1'))
DRY_RUN   = os.environ.get('DRY_RUN', 'true').lower() == 'true'
LOG_FILE  = os.environ.get('LOG_FILE', '/tmp/htft_bets.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('htft_bot')

# ── Betpawa API ────────────────────────────────────────────────────────
BP_H = {
    'x-pawa-brand': 'betpawa-uganda',
    'x-pawa-language': 'en',
    'devicetype': 'web',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}
BP_BASE    = 'https://www.betpawa.ug/api/sportsbook/virtual'
BP_SEASONS = f'{BP_BASE}/v2/seasons/list/actual'
BP_EVENTS  = f'{BP_BASE}/v3/events/list/by-round/{{round_id}}'
ENG_LEAGUE = '7794'


def get_upcoming_english_matches():
    """Returns list of {round_id, home, away} for the next English League round."""
    try:
        d   = requests.get(BP_SEASONS, headers=BP_H, timeout=10).json()
        now = datetime.now(timezone.utc)
        for season in d.get('items', []):
            for rnd in season.get('rounds', []):
                t = rnd.get('tradingTime', {})
                try:
                    start = datetime.fromisoformat(t['start'].replace('Z', '+00:00'))
                    end   = datetime.fromisoformat(t['end'].replace('Z', '+00:00'))
                    # Accept rounds that are upcoming or currently open
                    if end < now:
                        continue
                    ed = requests.get(BP_EVENTS.format(round_id=rnd['id']),
                                      headers=BP_H, timeout=10).json()
                    matches = []
                    for e in ed.get('responses', []):
                        if str(e.get('competition', {}).get('id', '')) != ENG_LEAGUE:
                            continue
                        name  = e.get('name', '')
                        parts = name.split(' - ')
                        if len(parts) == 2:
                            matches.append({
                                'round_id': rnd['id'],
                                'home': parts[0].strip(),
                                'away': parts[1].strip(),
                                'event_id': e['id'],
                            })
                    if matches:
                        return matches
                except Exception:
                    pass
    except Exception as ex:
        log.error(f"API error: {ex}")
    return []


# ── HTFT Pair Matrix ───────────────────────────────────────────────────
# Format: 'HOME v AWAY': [('MARKET', odds, p_win, ev), ...]
HTFT_MATRIX = {
    "ARS v AST": [("1/2",65.0,0.1667,9.83),("2/X",15.0,0.1667,1.50),("X/1",5.0,0.3333,0.65)],
    "WOL v EVE": [("1/2",61.0,0.25,14.25)],
    "FUL v SUN": [("1/2",51.0,0.2857,13.57)],
    "BOU v EVE": [("1/2",65.0,0.20,12.00),("2/X",14.5,0.20,1.90)],
    "BRE v MCI": [("1/2",37.0,0.3333,11.33),("2/X",15.5,0.1667,1.58)],
    "AST v NEW": [("1/2",35.0,0.3333,10.67)],
    "MCI v WHU": [("1/2",61.0,0.1667,9.17),("2/X",17.0,0.1667,1.83)],
    "LIV v WHU": [("1/2",80.0,0.125,9.00),("2/X",17.5,0.125,1.19)],
    "NOT v EVE": [("1/2",50.0,0.20,9.00)],
    "LIV v FUL": [("1/2",66.0,0.1429,8.43),("2/X",19.0,0.1429,1.71)],
    "BHA v EVE": [("1/2",55.0,0.1667,8.17)],
    "CHE v BUR": [("1/2",62.0,0.1429,7.86),("2/X",21.5,0.2857,5.14),("1/1",1.8,0.5714,0.03)],
    "CHE v SUN": [("1/2",69.0,0.125,7.63),("X/X",9.0,0.25,1.25),("X/2",17.0,0.25,3.25)],
    "TOT v BUR": [("1/2",49.0,0.1667,7.17)],
    "NEW v WOL": [("2/X",27.0,0.2857,6.71),("1/X",16.5,0.2857,3.71),("X/2",14.5,0.1429,1.07)],
    "MUN v WHU": [("1/2",53.0,0.1429,6.57),("2/2",8.0,0.2857,1.29)],
    "CHE v WHU": [("1/X",15.5,0.3333,4.17),("2/2",4.1,0.3333,0.37)],
    "BUR v BRE": [("1/2",37.0,0.20,6.40),("2/X",16.5,0.20,2.30),("2/2",4.3,0.40,0.72)],
    "EVE v MUN": [("1/2",31.0,0.20,5.20),("2/X",18.0,0.20,2.60),("2/2",5.5,0.20,0.10)],
    "CHE v ARS": [("1/2",35.0,0.1667,4.83),("2/X",16.5,0.1667,1.75)],
    "NOT v TOT": [("1/2",30.0,0.20,5.00),("2/X",14.0,0.20,1.80),("X/1",7.0,0.20,0.40),("X/X",8.0,0.20,0.60)],
    "MCI v FUL": [("1/2",51.0,0.1667,7.50)],
    "BHA v CRY": [("1/2",49.0,0.125,5.13),("2/X",17.5,0.125,1.19)],
    "EVE v MCI": [("1/1",4.0,0.4444,0.78),("2/X",18.5,0.1111,1.06)],
    "LEE v BOU": [("2/1",33.0,0.1667,4.50),("2/2",4.3,0.3333,0.43)],
    "BUR v AST": [("1/2",28.0,0.1667,3.67),("2/X",14.5,0.1667,1.42)],
    "BUR v CHE": [("1/2",23.5,0.20,3.70),("2/X",16.0,0.20,2.20)],
    "BUR v FUL": [("1/2",31.0,0.1667,4.17),("2/X",16.0,0.1667,1.67)],
    "SUN v WOL": [("1/2",32.0,0.1429,3.57),("2/X",18.5,0.1429,1.64)],
    "TOT v LEE": [("1/2",42.0,0.125,4.25),("2/X",20.5,0.125,1.56)],
    "LEE v WOL": [("1/2",35.0,0.1429,4.00),("2/X",19.5,0.1429,1.79)],
    "CHE v CRY": [("1/2",62.0,0.125,6.75)],
    "LIV v BRE": [("2/X",20.0,0.1667,2.33)],
    "WHU v MCI": [("2/X",18.0,0.1667,2.00)],
    "EVE v BHA": [("2/X",17.5,0.1667,1.92)],
    "BHA v BRE": [("2/X",19.0,0.1111,1.11)],
    "CRY v BOU": [("1/1",3.9,0.5556,1.17),("X/2",6.2,0.2222,0.38),("2/X",18.0,0.1111,1.00)],
    "LEE v LIV": [("2/2",3.4,0.4444,0.51)],
    "ARS v BUR": [("X/X",9.0,0.3333,2.00)],
    "SUN v NOT": [("X/X",8.5,0.40,2.40)],
    "BHA v AST": [("X/X",7.5,0.2857,1.14)],
    "ARS v MUN": [("X/X",6.5,0.30,0.95),("2/X",16.0,0.15,1.40)],
    "MCI v ARS": [("2/X",18.0,0.15,1.70)],
    "TOT v MCI": [("2/X",15.5,0.15,1.33)],
}


def log_bet(home, away, market, odds, stake, dry_run, result=None):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tag  = '[DRY]' if dry_run else '[LIVE]'
    line = f"{ts} {tag} {home} v {away} | HTFT {market} @ {odds} | stake={stake}"
    if result: line += f" | {result}"
    log.info(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


async def login(page):
    await page.goto('https://www.betpawa.ug', timeout=30000)
    await asyncio.sleep(4)
    body = await page.inner_text('body')
    if 'LOGIN' not in body[:500].upper():
        log.info("Already logged in")
        return True
    await page.goto('https://www.betpawa.ug/login', timeout=15000)
    await asyncio.sleep(3)
    phone_el = page.locator('#phoneNumber, input[name=username]').first
    if await phone_el.count(): await phone_el.fill(BP_PHONE)
    pw_el = page.locator('input[name=password], input[type=password]').first
    if await pw_el.count(): await pw_el.fill(BP_PIN)
    await asyncio.sleep(1)
    await pw_el.press('Enter')
    await asyncio.sleep(8)
    still_login = await page.query_selector('#phoneNumber')
    if still_login:
        log.error("Login failed — check credentials")
        return False
    log.info("Logged in ✓")
    return True


async def place_htft_bet(page, home, away, market, odds, stake):
    """Open the match HTFT market and click the correct outcome."""
    try:
        url = f'https://www.betpawa.ug/virtual-sports?virtualTab=upcoming&leagueId=7794'
        await page.goto(url, timeout=30000)
        await asyncio.sleep(6)

        # Find and click the match row
        # betpawa shows matches as "HOME - AWAY" text
        match_text = f'{home} - {away}'
        match_el = page.get_by_text(match_text, exact=False).first
        if not await match_el.count():
            # Try reversed (sometimes betpawa shows full names)
            log.warning(f"Match text not found: '{match_text}'")
            return False
        await match_el.click()
        await asyncio.sleep(3)

        # Look for HTFT tab
        for tab_text in ['HT/FT', 'HTFT', 'Half Time', 'HT / FT']:
            tab = page.get_by_text(tab_text, exact=False).first
            if await tab.count():
                await tab.click()
                await asyncio.sleep(2)
                break
        else:
            log.warning(f"HTFT tab not found for {home} v {away}")
            return False

        # Click the market outcome button
        outcome_el = page.get_by_text(market, exact=True).first
        if not await outcome_el.count():
            log.warning(f"Outcome '{market}' not found")
            return False
        await outcome_el.click()
        await asyncio.sleep(1)

        # Set stake amount
        stake_input = page.locator(
            'input[placeholder*="stake" i], input[class*="stake" i], '
            'input[placeholder*="amount" i]'
        ).first
        if await stake_input.count():
            await stake_input.triple_click()
            await stake_input.type(str(stake))
            await asyncio.sleep(0.5)

        # Click Place Bet / Confirm
        for btn_text in ['Place Bet', 'Confirm', 'Place bet']:
            btn = page.get_by_role('button', name=btn_text).first
            if await btn.count():
                await btn.click()
                await asyncio.sleep(3)
                log.info(f"✓ Placed: {home} v {away} | {market} @ {odds}")
                return True

        log.warning("Place bet button not found")
        return False

    except Exception as e:
        log.error(f"Bet error {home} v {away} {market}: {e}")
        return False


async def run():
    log.info("=" * 55)
    log.info(f"HTFT Bot | stake={STAKE_UGX} UGX | dry_run={DRY_RUN}")
    log.info(f"Matrix: {len(HTFT_MATRIX)} pairs | "
             f"{sum(len(v) for v in HTFT_MATRIX.values())} signals")
    log.info("=" * 55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox',
                  '--disable-dev-shm-usage', '--disable-gpu']
        )
        page = await browser.new_page()

        if not await login(page):
            await browser.close()
            return

        last_round_id = None
        bets_placed   = 0

        while True:
            try:
                # Get upcoming matches via API (not scraping)
                matches = get_upcoming_english_matches()
                log.info(f"Upcoming English League matches: {len(matches)}")

                if not matches:
                    log.info("No upcoming matches — waiting 60s")
                    await asyncio.sleep(60)
                    continue

                round_id = matches[0]['round_id']
                if round_id == last_round_id:
                    log.info(f"Same round {round_id} — waiting for next")
                    await asyncio.sleep(60)
                    continue

                last_round_id = round_id
                log.info(f"New round: {round_id} | {len(matches)} matches")

                bets_this_round = 0
                for m in matches:
                    home = m['home']; away = m['away']
                    key  = f"{home} v {away}"
                    if key not in HTFT_MATRIX:
                        continue
                    signals = HTFT_MATRIX[key]
                    log.info(f"  {key} → {len(signals)} bets")
                    for market, odds, p_win, ev in signals:
                        log_bet(home, away, market, odds, STAKE_UGX, DRY_RUN)
                        bets_this_round += 1
                        if not DRY_RUN:
                            ok = await place_htft_bet(
                                page, home, away, market, odds, STAKE_UGX
                            )
                            if ok: bets_placed += 1
                        await asyncio.sleep(random.uniform(1.0, 2.0))

                log.info(
                    f"Round {round_id} done: {bets_this_round} signals | "
                    f"placed={bets_placed} total"
                )

            except Exception as e:
                log.error(f"Loop error: {e}")

            # Wait for next round
            log.info("Waiting 3 min for next round...")
            await asyncio.sleep(180)

        await browser.close()


if __name__ == '__main__':
    asyncio.run(run())
