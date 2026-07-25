"""Bongobongo betting platform integration using coordinates-based layout parsing."""
import asyncio, random, sys
from config import CREDENTIALS, DEFAULT_STAKE
from browser_harness import get_browser, wait, evaluate, click_xy

PHONE = CREDENTIALS["bongobongo"]["phone"]
PIN = CREDENTIALS["bongobongo"]["pin"]

_selected_match_y = None
_last_odds = []

async def ensure_login():
    """Login if not already logged in."""
    page = await get_browser()
    await page.goto("https://www.bongobongo.ug", timeout=20000)
    await wait(4)
    body = await page.inner_text("body")
    
    # Check if blocked by Cloudflare Turnstile
    body_lower = body.lower()
    if "cloudflare" in body_lower or "turnstile" in body_lower or "verify you are human" in body_lower:
        print("[login] WARNING: Blocked by Cloudflare Turnstile challenge.")
        return False
        
    if "/login" in page.url or ("balance" not in body_lower and "deposit" not in body_lower):
        await page.goto("https://www.bongobongo.ug/login", timeout=15000)
        await wait(3)
        mobile_val = await evaluate("() => document.querySelector('#mobile')?.value || ''")
        if not mobile_val:
            await page.locator("#mobile").first.fill(PHONE)
        pin_val = await evaluate("() => document.querySelector('#password')?.value || ''")
        if not pin_val:
            await page.locator("#password").first.fill(PIN)
        await evaluate("""() => {
            const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim()==='Login' && !x.disabled);
            if(b) b.click();
        }""")
        await wait(8)
        
    # Dismiss promotional overlays post-login
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('[class*=overlay], [class*=modal], [class*=popup], [class*=banner]').forEach(el => {
                el.style.display = 'none';
            });
            const closeBtn = document.querySelector('[class*=close]');
            if (closeBtn) closeBtn.click();
        }""")
    except Exception:
        pass
        
    body_after = await page.inner_text("body")
    body_after_lower = body_after.lower()
    return "login" not in body_after_lower and ("balance" in body_after_lower or "deposit" in body_after_lower)

async def get_balance():
    """Read current balance from page."""
    page = await get_browser()
    # Dismiss any active modals first to make header visible
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('[class*=overlay], [class*=modal], [class*=popup], [class*=banner]').forEach(el => {
                el.style.display = 'none';
            });
        }""")
        await asyncio.sleep(1)
    except Exception:
        pass
        
    return await evaluate("""() => {
        for (let el of document.querySelectorAll('*')) {
            let t = el.textContent.trim();
            if (t.toLowerCase().includes('balance') && el.offsetHeight > 0 && t.length < 40) {
                let m = t.match(/[\\d,]+\\.?\\d*/);
                if (m) return parseFloat(m[0].replace(',',''));
            }
        }
        return null;
    }""")

async def navigate_to_vsl_england():
    """Navigate to VSL England virtual football via the game iframe."""
    page = await get_browser()
    await page.goto("https://www.bongobongo.ug/game/info/1x2-gaming-virtual-soccer", timeout=30000)
    
    # Check if dry run (free play mode) or real mode
    import os
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    selector = '[data-game-info-play-fun]' if dry_run else '[data-game-info-play-real]'
    mode_name = "Free Play" if dry_run else "Play Now"
    print(f"[*] Navigating in {mode_name} mode...")
    
    try:
        await page.wait_for_selector(f'{selector}, button', timeout=15000)
    except Exception:
        pass
    await wait(3)
    # Dismiss overlays
    await evaluate("""() => {
        document.querySelectorAll('[class*=overlay], [class*=modal], [class*=popup], [class*=banner]').forEach(el => {
            el.style.display = 'none';
        });
    }""")
    # Click play button
    await evaluate(f"""() => {{
        const btn = document.querySelector('{selector}') ||
                    [...document.querySelectorAll('button')].find(b => b.textContent.includes('{mode_name}'));
        if (btn) btn.click();
    }}""")
    await wait(15)
    # Find the game iframe
    ife = await page.query_selector('iframe[src*="desertorchid"]') or await page.query_selector('iframe')
    if not ife:
        return False
    bx = await ife.bounding_box()
    if not bx:
        return False
    # Find the game frame
    gf = None
    for f in page.frames:
        if 'desertorchid' in f.url or '1x2games' in f.url:
            gf = f
            break
    if not gf:
        return False
    # Wait for game content
    for _ in range(15):
        try:
            frame_text = await gf.inner_text('body')
            if 'Select a country' in frame_text or ' v ' in frame_text:
                break
        except Exception:
            pass
        await wait(2)
    # Select English League if in lobby
    try:
        frame_text = await gf.inner_text('body')
    except Exception:
        frame_text = ""
    if 'Select a country' in frame_text:
        coords = await gf.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('*')).filter(el => el.offsetHeight > 0);
            const league = els.find(e => e.textContent.trim() === 'English League');
            const nextBtn = els.find(e => e.textContent.trim() === 'Next' || e.textContent.trim() === 'NEXT');
            const result = {};
            if (league) {
                const r = league.getBoundingClientRect();
                result.league = { x: r.x + r.width/2, y: r.y + r.height/2 };
            }
            if (nextBtn) {
                const r = nextBtn.getBoundingClientRect();
                result.nextBtn = { x: r.x + r.width/2, y: r.y + r.height/2 };
            }
            return result;
        }""")
        if 'league' in coords:
            click_x = bx['x'] + coords['league']['x']
            click_y = bx['y'] + coords['league']['y'] - 30
            await page.mouse.click(click_x, click_y)
            await wait(2)
            if 'nextBtn' in coords:
                next_x = bx['x'] + coords['nextBtn']['x']
                next_y = bx['y'] + coords['nextBtn']['y']
                await page.mouse.click(next_x, next_y)
                await wait(5)
    # Dismiss default league dialog
    await gf.evaluate("""() => {
        document.querySelectorAll('[class*=messi]').forEach(el => { el.style.display = 'none'; });
    }""")
    await wait(2)
    # Try clicking No on the dialog
    dialog_pos = await gf.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('*')).filter(el => el.offsetHeight > 0);
        const dialogText = els.find(e => e.textContent.includes('Set English League as default'));
        if (!dialogText) return null;
        const noBtn = els.find(e => (e.textContent.trim() === 'No' || e.textContent.trim() === 'NO') && e.offsetHeight > 0 && e.offsetHeight < 60);
        if (noBtn) {
            const r = noBtn.getBoundingClientRect();
            return { x: r.x + r.width/2, y: r.y + r.height/2 };
        }
        return null;
    }""")
    if dialog_pos:
        await page.mouse.click(bx['x'] + dialog_pos['x'], bx['y'] + dialog_pos['y'])
        await wait(3)
    return True


async def get_available_matches():
    """Extract matches and odds from the bongobongo game iframe."""
    page = await get_browser()
    # Find the game frame
    gf = None
    for f in page.frames:
        if 'desertorchid' in f.url or '1x2games' in f.url:
            gf = f
            break
    if not gf:
        return {"matches": []}
    # Find the game iframe element on the parent page
    ife = await page.query_selector('iframe[src*="desertorchid"]') or await page.query_selector('iframe')
    bx = await ife.bounding_box() if ife else {"x": 0, "y": 0, "width": 0, "height": 0}
    if not bx:
        bx = {"x": 0, "y": 0, "width": 0, "height": 0}
        
    # Wait for matches
    for _ in range(6):
        try:
            ft = await gf.inner_text('body')
            if ' v ' in ft:
                break
        except Exception:
            pass
        await wait(2)
        
    data = await gf.evaluate(r"""() => {
        const els = Array.from(document.querySelectorAll('*')).filter(el => el.offsetHeight > 0);
        const matchEls = els.filter(e => {
            const t = e.textContent.trim();
            return t.includes(' v ') && t.length < 50 && e.children.length <= 2;
        });
        
        // Find all odds buttons
        const oddsButtons = els.filter(el => {
            return /^\d+\.\d{2}$/.test(el.textContent.trim()) && el.children.length === 0;
        }).map(el => {
            const r = el.getBoundingClientRect();
            return {
                x: r.x + r.width/2,
                y: r.y + r.height/2,
                text: el.textContent.trim()
            };
        });

        const seen = new Set();
        const matches = [];
        for (const mel of matchEls) {
            const t = mel.textContent.trim();
            if (seen.has(t)) continue;
            seen.add(t);
            const r = mel.getBoundingClientRect();
            const y = r.y + r.height/2;
            
            // Find odds for this match
            const rowOdds = oddsButtons.filter(o => Math.abs(o.y - y) < 15);
            rowOdds.sort((a, b) => a.x - b.x);
            
            const parts = t.split(' v ');
            const odds = {};
            if (rowOdds.length >= 3) {
                odds["1"] = rowOdds[0].text;
                odds["X"] = rowOdds[1].text;
                odds["2"] = rowOdds[2].text;
            }
            matches.push({
                home: (parts[0]||"").trim(),
                away: (parts[1]||"").trim(),
                text: t,
                y: y,
                odds: odds
            });
        }
        return {matches: matches, allOdds: oddsButtons};
    }""")
    
    # Adjust coordinates with iframe offset
    matches_list = data.get("matches", [])
    all_odds = data.get("allOdds", [])
    for m in matches_list:
        m["y"] = m["y"] + bx["y"]
    for o in all_odds:
        o["x"] = o["x"] + bx["x"]
        o["y"] = o["y"] + bx["y"]
        
    global _last_odds
    _last_odds = all_odds
    return {"matches": matches_list}


async def click_match_odd(target_team):
    """Store the match Y coordinate for the target team."""
    matches = await get_available_matches()
    global _selected_match_y
    for m in matches.get("matches", []):
        if target_team.lower() in m["home"].lower() or target_team.lower() in m["away"].lower():
            _selected_match_y = m["y"]
            return True
    return False

async def get_game_frame():
    page = await get_browser()
    for f in page.frames:
        if 'desertorchid' in f.url or '1x2games' in f.url:
            return f
    return None

async def place_bet(selection_text="1"):
    """Click the odd button for the selected match and expand betslip inside the iframe."""
    global _selected_match_y, _last_odds
    if not _selected_match_y:
        print("[-] place_bet: No match selected.")
        return False
        
    # Filter odds near the selected match Y
    row_odds = [o for o in _last_odds if abs(o["y"] - _selected_match_y) < 30]
    row_odds.sort(key=lambda o: o["x"])
    
    if len(row_odds) < 3:
        print(f"[-] place_bet: Expected 3 odds, found {len(row_odds)}.")
        return False
        
    # Group by rounded X coordinate to find 3 columns
    columns = {}
    for o in row_odds:
        col_key = round(o["x"] / 10) * 10
        if col_key not in columns:
            columns[col_key] = []
        columns[col_key].append(o)
        
    col_keys = sorted(columns.keys())
    if len(col_keys) < 3:
        print(f"[-] place_bet: Expected 3 columns, found {len(col_keys)}.")
        return False
        
    # Map selections
    sel_map = {
        "1": 0, "HOME": 0,
        "X": 1, "x": 1, "DRAW": 1,
        "2": 2, "AWAY": 2
    }
    col_idx = sel_map.get(str(selection_text).upper(), 0)
    if col_idx >= len(col_keys):
        print(f"[-] place_bet: Invalid selection '{selection_text}'.")
        return False
        
    # Pick the button in the column (largest Y coordinate)
    target_odd = max(columns[col_keys[col_idx]], key=lambda o: o["y"])
    
    # Click the odd button
    print(f"[*] Clicking odd button: x={target_odd['x']}, y={target_odd['y']}")
    await click_xy(target_odd["x"], target_odd["y"])
    await wait(random.uniform(0.5, 1.2))
    
    # Expand the betslip drawer inside the game iframe
    gf = await get_game_frame()
    if not gf:
        print("[-] place_bet: Game iframe not found.")
        return False
        
    expanded = False
    try:
        await gf.click('#betOptions', timeout=5000)
        expanded = True
    except Exception:
        try:
            await gf.click('.bet__slip', timeout=5000)
            expanded = True
        except Exception as e:
            print(f"[-] place_bet: Failed to click betslip drawer: {e}")
            
    print(f"[+] Betslip expanded drawer inside iframe: {expanded}")
    await wait(random.uniform(0.5, 1.0))
    return expanded

async def set_stake(amount=DEFAULT_STAKE):
    """Set stake to target amount by clicking minus from full balance."""
    gf = await get_game_frame()
    if not gf:
        return False
    
    import re
    target_val = float(amount)
    
    # Read current stake
    current = await gf.evaluate("() => document.getElementById('stakeAmount')?.textContent || ''")
    nums = re.findall(r"[\d\.]+", current.replace(",", ""))
    if not nums:
        print(f"[-] Cannot read stake: '{current}'")
        return False
    
    current_val = float(nums[0])
    
    if abs(current_val - target_val) < 1:
        return True
    
    try:
        # Just click minus until we reach target — proven to work (5-6 clicks typically)
        max_clicks = 80
        clicks = 0
        stuck_count = 0
        
        while current_val > target_val + 1 and clicks < max_clicks:
            await gf.click('#stakeMinus', force=True)
            clicks += 1
            await wait(0.25)
            
            ui_str = await gf.evaluate("() => document.getElementById('stakeAmount')?.textContent || ''")
            ui_nums = re.findall(r"[\d\.]+", ui_str.replace(",", ""))
            if not ui_nums:
                continue
            
            new_val = float(ui_nums[0])
            if new_val == current_val:
                stuck_count += 1
                if stuck_count >= 5:
                    # Truly stuck at boundary
                    break
                await wait(0.3)  # Extra wait in case UI is slow
            else:
                stuck_count = 0
                current_val = new_val
        
        # If target is above current (rare), click plus
        while current_val < target_val - 1 and clicks < max_clicks:
            await gf.click('#stakePlus', force=True)
            clicks += 1
            await wait(0.25)
            
            ui_str = await gf.evaluate("() => document.getElementById('stakeAmount')?.textContent || ''")
            ui_nums = re.findall(r"[\d\.]+", ui_str.replace(",", ""))
            if ui_nums:
                new_val = float(ui_nums[0])
                if new_val == current_val:
                    break
                current_val = new_val
        
        success = abs(current_val - target_val) <= 250
        print(f"[{'+'if success else '-'}] Stake: {current_val} ({clicks} clicks)")
        return success
        
    except Exception as e:
        print(f"[-] set_stake error: {e}")
        return False

async def confirm_bet():
    """Click the Place Bet / Bet Now button inside the iframe."""
    gf = await get_game_frame()
    if not gf:
        return False
        
    try:
        await gf.click('#placeBet', timeout=5000, force=True)
        await wait(3.0)
        return True
    except Exception as e:
        print(f"[-] confirm_bet error: {e}")
        return False

async def get_game_timer():
    """Read the remaining seconds in the current betting/simulation period."""
    gf = await get_game_frame()
    if not gf:
        return None
    try:
        timer_text = await gf.evaluate("() => document.getElementById('timer')?.querySelector('h1')?.textContent || ''")
        if timer_text.strip().isdigit():
            return int(timer_text.strip())
    except Exception as e:
        print(f"[-] Error reading game timer: {e}")
    return None

async def wait_for_betting_period(min_seconds=10, max_wait_seconds=150):
    """Wait until the betting period is open and has at least min_seconds remaining."""
    gf = await get_game_frame()
    if not gf:
        return False
        
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < max_wait_seconds:
        t = await get_game_timer()
        
        is_betting_open = (t is not None and t > 0)
        
        # Live single-line timer (overwrite same line)
        if t is None:
            sys.stdout.write("\r[⏳] Waiting for timer...          ")
        elif not is_betting_open:
            sys.stdout.write("\r[⚽] Match playing... (%ds)         " % t)
        elif t < min_seconds:
            sys.stdout.write("\r[⏳] Round ending (%ds)...          " % t)
            sys.stdout.flush()
            await asyncio.sleep(t + 2)
            continue
        else:
            sys.stdout.write("\r                                      \r")
            sys.stdout.flush()
            return True
        
        sys.stdout.flush()
        await asyncio.sleep(3)
        
    sys.stdout.write("\r[-] Timeout.                              \n")
    sys.stdout.flush()
    return False
