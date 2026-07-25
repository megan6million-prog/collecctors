"""Betkraft (Bandabets Euro Virtuals) betting platform integration."""
import asyncio, random
from config import CREDENTIALS, DEFAULT_STAKE
from browser_harness import get_browser, wait, evaluate

PHONE = CREDENTIALS["betkraft"]["phone"] or CREDENTIALS["bangbet"]["phone"]
PIN = CREDENTIALS["betkraft"]["pin"] or CREDENTIALS["bangbet"]["pin"]

TEAM_MAPPINGS = {
    "manch city": "Manchester Blue",
    "man city": "Manchester Blue",
    "manchester city": "Manchester Blue",
    "chelsea": "London Reds",
    "aston villa": "Villa",
    "aston v": "Villa",
    "villa": "Villa",
    "liverpool": "The Reds",
    "manch utd": "Manchester Red",
    "man utd": "Manchester Red",
    "manchester united": "Manchester Red",
    "n.forest": "N. Forest",
    "nottingham": "N. Forest",
    "tottenham": "Spurs",
    "spurs": "Spurs",
    "west ham": "WestHam",
    "westham": "WestHam",
    "arsenal": "London Blues",
    "wolverhampton": "Wolves",
    "wolves": "Wolves",
    "crystal palace": "Palace",
    "palace": "Palace",
}

def map_team_name(name: str) -> str:
    name_lower = name.lower().strip()
    return TEAM_MAPPINGS.get(name_lower, name)

def normalize_market_name(name: str) -> str:
    name_clean = name.upper().replace(" ", "").replace("_", "").replace("-", "")
    mapping = {
        "1X2": "1X2",
        "GG": "GG",
        "BTTS": "GG",
        "BOTHSELFTOCORE": "GG",
        "BOTH TEAMS TO SCORE": "GG",
        "OVERUNDER1.5": "OV/UN 1.5",
        "OV/UN1.5": "OV/UN 1.5",
        "O/U1.5": "OV/UN 1.5",
        "OVERUNDER2.5": "OV/UN 2.5",
        "OV/UN2.5": "OV/UN 2.5",
        "O/U2.5": "OV/UN 2.5",
        "DC": "DC",
        "DOUBLECHANCE": "DC",
        "OVERUNDER3.5": "OV/UN 3.5",
        "OV/UN3.5": "OV/UN 3.5",
        "O/U3.5": "OV/UN 3.5",
        "HT": "HT",
        "HALFTIME": "HT",
        "DCHT": "DC (HT)",
        "DC(HT)": "DC (HT)",
        "DOUBLECHANCEHALFTIME": "DC (HT)",
        "HALFTIMESCORE": "Half-Time Score",
        "HTSCORE": "Half-Time Score",
        "1X2&BTTS": "1X2 & BTTS",
        "1X2&OV/UN1.5": "1X2 & OV/UN 1.5",
        "1X2&OV/UN2.5": "1X2 & OV/UN 2.5",
        "1X2&OV/UN3.5": "1X2 & OV/UN 3.5",
        "1X2&OV/UN4.5": "1X2 & OV/UN 4.5",
        "1X2&OV/UN5.5": "1X2 & OV/UN 5.5",
        "CORRECTSCORE": "Correct Score",
        "HT/FT": "HT/FT",
        "HTFT": "HT/FT",
        "FIRSTTEAMTOSCORE": "First Team to Score",
        "GOAL:GOALHALFTIME": "Goal:Goal Half Time",
        "MULTIGOALS": "Multi-Goals",
        "TEAM1GOAL/NOGOAL": "Team 1 Goal/No Goal",
        "TEAM1OV/UN1.5": "Team 1 OV/UN 1.5",
        "TEAM2GOAL/NOGOAL": "Team 2 Goal/No Goal",
        "TEAM2OV/UN1.5": "Team 2 OV/UN 1.5",
        "TIMEOFFIRSTGOAL": "Time of First Goal",
        "TOTALGOALS": "Total Goals",
        "TOTALGOALSODD/EVEN": "Total Goals Odd/Even",
    }
    for k, v in mapping.items():
        if k in name_clean:
            return v
    return name

def normalize_pick(market: str, pick: str) -> str:
    p = str(pick).strip().upper()
    m = market.upper()
    
    if m in ["1X2", "HT", "HT/FT", "1X2 & BTTS", "1X2 & OV/UN 1.5", "1X2 & OV/UN 2.5", "1X2 & OV/UN 3.5", "1X2 & OV/UN 4.5", "1X2 & OV/UN 5.5"]:
        if p in ["HOME", "1", "W"]: return "1"
        if p in ["AWAY", "2", "L"]: return "2"
        if p in ["DRAW", "X", "D"]: return "X"
        
    if "GG" in m or "BTTS" in m or "GOAL" in m:
        if p in ["YES", "GG", "1"]: return "Yes"
        if p in ["NO", "NG", "0"]: return "No"
        
    if "OV" in m or "UN" in m or "O/U" in m:
        if p in ["OVER", "OV", "O"]: return "Over"
        if p in ["UNDER", "UN", "U"]: return "Under"
        
    if "ODD" in m or "EVEN" in m:
        if p in ["ODD", "ODDS"]: return "Odd"
        if p in ["EVEN", "EVENS"]: return "Even"
        
    if m == "FIRST TEAM TO SCORE":
        if p in ["HOME", "1"]: return "Home"
        if p in ["AWAY", "2"]: return "Away"
        if p in ["NO GOALS", "NONE", "0"]: return "No Goals"
        
    return str(pick).strip()

async def get_betkraft_frame(page):
    for f in page.frames:
        if "betkraft" in f.url:
            return f
    return None

async def ensure_login():
    """Login if not already logged in to Bandabets."""
    page = await get_browser()
    await page.goto("https://ug.bandabets.com", timeout=30000)
    await wait(4)
    
    # Dismiss initial popup overlays/ad banners
    await page.evaluate("""() => {
        const selectors = [
            '[class*=overlay]', '[class*=modal]', '[class*=popup]', 
            '[class*=dialog]', '[class*=banner]', '.pop-up', '.ads-overlay'
        ];
        selectors.forEach(sel => {
            try {
                document.querySelectorAll(sel).forEach(el => {
                    el.style.display = 'none';
                });
            } catch(e) {}
        });
    }""")
    
    body = await page.inner_text("body")
    if "Deposit" not in body and "Balance" not in body:
        await page.context.clear_cookies()
        await page.goto("https://ug.bandabets.com/login", timeout=20000)
        await wait(5)
        
        # Dismiss overlays on login page too
        await page.evaluate("""() => {
            const selectors = [
                '[class*=overlay]', '[class*=modal]', '[class*=popup]', 
                '[class*=dialog]', '[class*=banner]', '.pop-up', '.ads-overlay'
            ];
            selectors.forEach(sel => {
                try {
                    document.querySelectorAll(sel).forEach(el => {
                        el.style.display = 'none';
                    });
                } catch(e) {}
            });
        }""")
        
        # Fetch dynamically from CREDENTIALS to ensure DB overrides are read
        phone_num = CREDENTIALS["betkraft"]["phone"] or CREDENTIALS["bangbet"]["phone"]
        pin_code = CREDENTIALS["betkraft"]["pin"] or CREDENTIALS["bangbet"]["pin"]
        
        phone_el = page.locator("input[name=phone]").first
        if await phone_el.count() > 0:
            await phone_el.fill(phone_num)
        pass_el = page.locator("input[name=password]").first
        if await pass_el.count() > 0:
            await pass_el.fill(pin_code)
        await wait(2)
        await evaluate("""() => {
            const btn = [...document.querySelectorAll('button')].find(x => x.textContent.includes('Login') && !x.disabled);
            if (btn) btn.click();
        }""")
        await wait(10)
    body = await page.inner_text("body")
    return "Deposit" in body or "Balance" in body

async def get_balance():
    """Read wallet balance. Tries the in-iframe wallet (#accBal); falls back to the
    bandabets header balance (.balance-link, e.g. '8.80') since betkraft runs inside
    the bandabets-authenticated session."""
    page = await get_browser()
    # 1) iframe wallet
    target_frame = await get_betkraft_frame(page)
    if target_frame:
        try:
            val = await target_frame.evaluate("""() => {
                const el = document.querySelector('#accBal');
                return el ? parseFloat(el.textContent.trim().replace(/,/g,'')) : null;
            }""")
            if val is not None:
                return val
        except Exception:
            pass
    # 2) parent bandabets header balance
    try:
        return await evaluate(r"""() => {
            const num = (s) => {
                if (!s) return null;
                const m = String(s).match(/[0-9][0-9,]*\.?[0-9]*/);
                return m ? parseFloat(m[0].replace(/,/g,'')) : null;
            };
            let el = document.querySelector('.balance-link');
            if (el) { const v = num(el.textContent); if (v !== null) return v; }
            el = document.querySelector('#accessAccount, .authetication');
            if (el) { const v = num(el.textContent); if (v !== null) return v; }
            return null;
        }""")
    except Exception:
        return None

async def navigate_to_iframe():
    """Navigate to the Euro Virtuals iframe page."""
    page = await get_browser()
    url = "https://ug.bandabets.com/iframe?IsDemo=0&providerID=55&gameName=Euro+Virtuals&gameID=550e8400-e29b-41d4-a716-446655440000"
    await page.goto(url, wait_until="networkidle", timeout=60000)
    await wait(5)
    return True

async def click_match_odd(team: str, market: str = "1X2", pick: str = "1") -> bool:
    """Click the odds selection for a specific team, market and pick in Betkraft."""
    page = await get_browser()
    target_frame = await get_betkraft_frame(page)
    if not target_frame:
        print("[-] Betkraft frame not found")
        return False
        
    for _ in range(60):
        active_time_el = await target_frame.query_selector(".time li.active_time")
        if active_time_el:
            text = await active_time_el.inner_text()
            if "LIVE" not in text.upper() and "ENDED" not in text.upper() and ":" in text:
                break
        await asyncio.sleep(1)
        
    mapped_team = map_team_name(team)
    norm_market = normalize_market_name(market)
    norm_pick = normalize_pick(market, pick)
    
    print(f"[*] Placing betkraft bet: team='{mapped_team}' (orig: '{team}'), market='{norm_market}' (orig: '{market}'), pick='{norm_pick}' (orig: '{pick}')")
    
    clicked = await target_frame.evaluate("""([marketName, teamName, pickVal]) => {
        const tabs = [...document.querySelectorAll('.marketstab button.tablinks')];
        const targetTab = tabs.find(x => x.textContent.trim().toLowerCase() === marketName.toLowerCase());
        if (!targetTab) {
            console.log("Market tab not found: " + marketName);
            return false;
        }
        targetTab.click();
        
        const rows = [...document.querySelectorAll('.row-even, .row-odd')];
        const targetRow = rows.find(r => {
            const txt = (r.innerText || '').toLowerCase();
            return txt.includes(teamName.toLowerCase());
        });
        
        if (!targetRow) {
            console.log("Match row not found for team: " + teamName);
            return false;
        }
        
        const options = [...targetRow.querySelectorAll('.btn-option, button.btn')];
        const targetOpt = options.find(o => {
            const marketLbl = o.querySelector('.market, .market-option');
            return marketLbl && marketLbl.textContent.trim().toLowerCase() === pickVal.toLowerCase();
        });
        
        if (!targetOpt) {
            console.log("Odd option not found for pick: " + pickVal);
            return false;
        }
        
        targetOpt.click();
        return true;
    }""", [norm_market, mapped_team, norm_pick])
    
    return clicked

async def set_stake(amount=DEFAULT_STAKE):
    """Expand the betslip if collapsed, and enter the stake amount."""
    page = await get_browser()
    target_frame = await get_betkraft_frame(page)
    if not target_frame:
        return False
        
    success = await target_frame.evaluate("""async (amountVal) => {
        let input = document.querySelector('#stakeAmount');
        if (!input || input.offsetHeight === 0) {
            const trigger = document.querySelector('.betslip-number-wrapper');
            if (trigger) trigger.click();
            
            // Wait for it to open
            for (let i = 0; i < 20; i++) {
                await new Promise(r => setTimeout(r, 100));
                input = document.querySelector('#stakeAmount');
                if (input && input.offsetHeight > 0) break;
            }
        }
        
        if (input) {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, String(amountVal));
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        return false;
    }""", amount)
    
    await wait(random.uniform(0.3, 0.7))
    return success

async def confirm_bet():
    """Click the Place Bet button to submit the bet."""
    page = await get_browser()
    target_frame = await get_betkraft_frame(page)
    if not target_frame:
        print("[betkraft] debug: target_frame not found in confirm_bet")
        return False
        
    res = await target_frame.evaluate("""() => {
        const btn = document.querySelector('.mybetslip a') || document.querySelector('.placebet-btn a');
        if (!btn) return "not_found";
        const h = btn.offsetHeight;
        if (h <= 0) return "height_0";
        btn.click();
        return "clicked";
    }""")
    print(f"[betkraft] debug: evaluate returned {res} in confirm_bet")
    if res == "clicked":
        await wait(random.uniform(1.2, 2.2))
        return True
    return False

async def get_matches_odds():
    """Extract match listings and odds from the Betkraft iframe."""
    page = await get_browser()
    target_frame = await get_betkraft_frame(page)
    if not target_frame:
        return {"matches": [], "odds": []}
        
    matches = await target_frame.evaluate("""async () => {
        const results = [];
        for (let i = 0; i < 50; i++) {
            const rows = document.querySelectorAll('.row-even, .row-odd');
            if (rows.length > 0) break;
            await new Promise(r => setTimeout(r, 100));
        }
        const rows = document.querySelectorAll('.row-even, .row-odd');
        rows.forEach((r, idx) => {
            const homeEl = r.querySelector('.home-team .teamname');
            const awayEl = r.querySelector('.away-team .teamname');
            if (!homeEl || !awayEl) return;
            
            const home = homeEl.textContent.trim();
            const away = awayEl.textContent.trim();
            
            const odds = {};
            const options = r.querySelectorAll('.btn-option, button.btn');
            options.forEach(o => {
                const mkt = o.querySelector('.market, .market-option');
                const val = o.querySelector('.market-selection, .odd-val, span:last-child');
                if (mkt && val) {
                    const pick = mkt.textContent.trim().toUpperCase();
                    const oddVal = parseFloat(val.textContent.trim());
                    if (!isNaN(oddVal)) {
                        odds[pick] = oddVal;
                    }
                }
            });
            
            if (Object.keys(odds).length >= 3) {
                results.push({
                    n: idx + 1,
                    home: home,
                    away: away,
                    odds: {
                        "1x2": {
                            "1": odds["1"],
                            "X": odds["X"],
                            "2": odds["2"]
                        }
                    }
                });
            }
        });
        return results;
    }""")
    return {"matches": matches, "odds": []}

