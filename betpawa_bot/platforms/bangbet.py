"""Bangbet betting platform integration targeting the real www.bangbet.com virtuals page."""
import asyncio, random
from config import CREDENTIALS, DEFAULT_STAKE
from browser_harness import get_browser, wait, evaluate

PHONE = CREDENTIALS["bangbet"]["phone"]
PIN = CREDENTIALS["bangbet"]["pin"]

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

async def dismiss_modals(page) -> None:
    """Dismiss any active popup modals on the page (Location Preference, Welcome Gifts, promos, etc.)."""
    try:
        await page.evaluate("""() => {
            const selectors = [
                '.close-black', '.close-i', '.dialog-close', '.close', '.close-btn', 
                '.icon-Icon_Delete_', '.icon-close', '.mark-bg'
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(btn => {
                    if (btn && btn.offsetHeight > 0) btn.click();
                });
            });
            
            // Also click Uganda location modal if still showing
            const ugLi = Array.from(document.querySelectorAll('.expand-li')).find(x => x.textContent.includes('Uganda') && x.offsetHeight > 0);
            if (ugLi) ugLi.click();
            
            // Generic close class finder
            Array.from(document.querySelectorAll('*')).forEach(el => {
                const cls = el.className;
                if (typeof cls === 'string' && el.offsetHeight > 0 && (
                    cls.includes('close-icon') || cls.includes('close-btn')
                )) {
                    el.click();
                }
            });
        }""")
        await asyncio.sleep(2)
    except Exception:
        pass

async def ensure_login() -> bool:
    """Check login status and log in to www.bangbet.com if not logged in."""
    page = await get_browser()
    
    print("[login] Setting location state to Uganda...")
    await page.goto("https://www.bangbet.com/ug/", timeout=45000)
    await asyncio.sleep(6)

    # Dismiss any active modal popups (Welcome gifts, location preference, etc.)
    await dismiss_modals(page)

    print("[login] Navigating to Bangbet virtuals...")
    await page.goto("https://www.bangbet.com/virtuals/", timeout=45000)
    await asyncio.sleep(8)

    # Dismiss modals on virtuals
    await dismiss_modals(page)

    body_text = await page.inner_text('body')
    is_logged_in = "Join / Login" not in body_text and "str.JoinLogin" not in body_text

    if not is_logged_in:
        print("[login] Not logged in. Clicking Join / Login...")
        click_success = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('.joinNow'));
            if (btns.length > 0) {
                btns.forEach(btn => btn.click());
                return true;
            }
            return false;
        }""")
        if click_success:
            await asyncio.sleep(6)

            # Wait for login iframe to be attached
            try:
                await page.wait_for_selector("iframe[src*='auth/welcome']", timeout=15000)
            except Exception as e:
                print(f"[login] Login iframe did not appear: {e}")
                return False

            # Locate the login iframe
            frame_locator = page.frame_locator("iframe[src*='auth/welcome']").first
            phone_input = frame_locator.locator("input[type='tel']").first
            pass_input = frame_locator.locator("input[type='password']").first

            if await phone_input.count() > 0:
                clean_phone = PHONE[1:] if PHONE.startswith("0") else PHONE
                print(f"[login] Entering phone {clean_phone}...")
                await phone_input.fill(clean_phone)
                await pass_input.fill(PIN)
                await asyncio.sleep(1)

                await frame_locator.locator(".nextBtn").first.click()
                await asyncio.sleep(8)
            else:
                print("[login] Login inputs not found in iframe.")
                return False
        else:
            print("[login] Join / Login button not found.")
            return False
    else:
        print("[login] Already logged in.")

    # Dismiss post-login popups (World cup promo, coupons modal, etc.)
    await dismiss_modals(page)

    body_text = await page.inner_text('body')
    return "Join / Login" not in body_text and "str.JoinLogin" not in body_text

async def get_balance() -> float:
    """Read wallet balance from the parent page."""
    page = await get_browser()
    try:
        # First try direct selector
        val = await page.evaluate("""() => {
            const el = document.querySelector('.balance');
            if (el) {
                const v = parseFloat(el.textContent.trim().replace(/,/g, ''));
                if (!isNaN(v)) return v;
            }
            return null;
        }""")
        if val is not None:
            return val
            
        # Fallback to general text search
        bal = await page.evaluate("""() => {
            for (let el of document.querySelectorAll('*')) {
                const t = el.textContent.trim();
                if ((t.includes('Balance') || t.includes('UGX') || t.includes('Deposit')) && el.offsetHeight > 0 && t.length < 40) {
                    const m = t.match(/[\\d,]+\\.?\\d*/);
                    if (m) return parseFloat(m[0].replace(',',''));
                }
            }
            return null;
        }""")
        return bal
    except Exception:
        return None

async def navigate_to_banda_league():
    """Navigate to the English League tab and best upcoming Match Day."""
    page = await get_browser()
    
    print("[nav] Setting location state to Uganda...")
    await page.goto("https://www.bangbet.com/ug/", timeout=40000)
    await asyncio.sleep(6)

    # Dismiss modal if present
    await dismiss_modals(page)

    print("[nav] Navigating to Bangbet virtuals page...")
    await page.goto("https://www.bangbet.com/virtuals/", timeout=40000)
    await asyncio.sleep(8)

    # Dismiss modal on virtuals
    await dismiss_modals(page)

    # Select English League tab
    print("[nav] Selecting English League tab...")
    await page.evaluate("""(tabText) => {
        const tabs = Array.from(document.querySelectorAll('.swiper-slide'));
        const targetTab = tabs.find(el => el.textContent.includes(tabText));
        if (targetTab) {
            const clickTarget = targetTab.querySelector('.menu-list-item') || targetTab;
            clickTarget.click();
        }
    }""", "English League")
    await asyncio.sleep(5)

    # Select best upcoming Match Day
    print("[nav] Selecting best upcoming Match Day...")
    await page.evaluate("""() => {
        const slides = Array.from(document.querySelectorAll('.swiper-slide'));
        const matchDaySlides = slides.filter(s => s.textContent.includes('Match Day'));
        const bestSlide = matchDaySlides.find(s => {
            const text = s.textContent;
            if (text.includes('Finished') || text.includes('00:00') || text.includes('Playing')) {
                return false;
            }
            const timeMatch = text.match(/(\\d{2}):(\\d{2})/);
            if (timeMatch) {
                const minutes = parseInt(timeMatch[1], 10);
                const seconds = parseInt(timeMatch[2], 10);
                return (minutes * 60 + seconds) > 20;
            }
            return false;
        });
        if (bestSlide) {
            const item = bestSlide.querySelector('.menu-list-item') || bestSlide;
            item.click();
        }
    }""")
    await asyncio.sleep(5)
    return True

async def get_matches_odds():
    """Extract match listings and pre-match 1X2 odds."""
    page = await get_browser()
    await dismiss_modals(page)
    matches = await page.evaluate("""() => {
        const textNodes = Array.from(document.querySelectorAll('*'))
            .filter(el => el.offsetHeight > 0)
            .filter(el => {
                const children = Array.from(el.children);
                const visibleChildren = children.filter(c => c.offsetHeight > 0);
                return visibleChildren.length === 0 && el.textContent.trim().length > 0;
            });
            
        const marketIndexes = [];
        textNodes.forEach((node, idx) => {
            const t = node.textContent.trim();
            if (t.startsWith('+') && t.includes('Markets')) {
                marketIndexes.push(idx);
            }
        });
        
        const results = [];
        marketIndexes.forEach((mIdx, idx) => {
            if (mIdx >= 5) {
                const home = textNodes[mIdx - 5].textContent.trim();
                const away = textNodes[mIdx - 4].textContent.trim();
                const o1 = parseFloat(textNodes[mIdx - 3].textContent.trim());
                const oX = parseFloat(textNodes[mIdx - 2].textContent.trim());
                const o2 = parseFloat(textNodes[mIdx - 1].textContent.trim());
                
                if (home && away && !isNaN(o1) && !isNaN(oX) && !isNaN(o2)) {
                    results.push({
                        n: idx + 1,
                        home: home,
                        away: away,
                        odds: {
                            "1x2": {
                                "1": o1,
                                "X": oX,
                                "2": o2
                            }
                        }
                    });
                }
            }
        });
        return results;
    }""")
    return {"matches": matches, "odds": []}

async def click_match_odd(team: str, market: str = "1X2", pick: str = "1") -> bool:
    """Click odds selection for a specific team, market and pick."""
    page = await get_browser()
    await dismiss_modals(page)
    pick_map = {'HOME': 0, '1': 0, 'W': 0, 'DRAW': 1, 'X': 1, 'D': 1, 'AWAY': 2, '2': 2, 'L': 2}
    col_idx = pick_map.get(str(pick).upper(), 0)
    mapped_team = map_team_name(team)
    
    # Check and clear old betslip selections first
    betslip_btn = page.locator(".betslip-nav-container").first
    if await betslip_btn.count() > 0:
        await betslip_btn.click()
        await asyncio.sleep(2)
        await page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button')).find(el => el.textContent.includes('Remove All'));
            if (btn) btn.click();
            const closeBtn = document.querySelector('.icon-Icon_BetslipFold_48') || document.querySelector('.back-ico') || document.querySelector('.mark-bg');
            if (closeBtn) closeBtn.click();
        }""")
        await asyncio.sleep(2)
        
    clicked = await page.evaluate("""([teamName, offset]) => {
        const textNodes = Array.from(document.querySelectorAll('*'))
            .filter(el => el.offsetHeight > 0)
            .filter(el => {
                const children = Array.from(el.children);
                const visibleChildren = children.filter(c => c.offsetHeight > 0);
                return visibleChildren.length === 0 && el.textContent.trim().length > 0;
            });
            
        const marketIndexes = [];
        textNodes.forEach((node, idx) => {
            const t = node.textContent.trim();
            if (t.startsWith('+') && t.includes('Markets')) {
                marketIndexes.push(idx);
            }
        });
        
        for (let mIdx of marketIndexes) {
            if (mIdx >= 5) {
                const home = textNodes[mIdx - 5].textContent.trim();
                const away = textNodes[mIdx - 4].textContent.trim();
                
                if (home.toLowerCase() === teamName.toLowerCase() || away.toLowerCase() === teamName.toLowerCase()) {
                    const oddNode = textNodes[mIdx - 3 + offset];
                    if (oddNode) {
                        const container = oddNode.closest('.action-box') || oddNode;
                        container.click();
                        return true;
                    }
                }
            }
        }
        return false;
    }""", [mapped_team, col_idx])
    
    await asyncio.sleep(3)
    return clicked

async def set_stake(amount):
    """Open betslip drawer and enter stake via custom keyboard."""
    page = await get_browser()
    await dismiss_modals(page)
    betslip_btn = page.locator(".betslip-nav-container").first
    if await betslip_btn.count() > 0:
        await betslip_btn.click()
        await asyncio.sleep(4)
        
    stake_input = page.locator(".wager-money-input").first
    if await stake_input.count() > 0:
        await stake_input.click(force=True)
        await asyncio.sleep(2)
        
        success = await page.evaluate("""(stakeStr) => {
            const container = document.querySelector('.board-container');
            if (!container) return false;
            
            const items = Array.from(container.querySelectorAll('.item'));
            const clearKey = items.find(el => el.textContent.trim() === 'Clear') || container.querySelector('.clear-btn');
            if (clearKey) clearKey.click();
            
            for (let char of stakeStr) {
                const key = items.find(el => el.textContent.trim() === char);
                if (key) key.click();
            }
            
            const doneKey = Array.from(container.querySelectorAll('*')).find(el => el.textContent.trim() === 'Done');
            if (doneKey) doneKey.click();
            return true;
        }""", str(amount))
        return success
    return False

async def confirm_bet() -> bool:
    """Submit the bet slip."""
    page = await get_browser()
    await dismiss_modals(page)
    place_btn = page.locator(".bet-btn-box, .place-button").first
    if await place_btn.count() > 0:
        await place_btn.click()
        await asyncio.sleep(5)
        
        # Close betslip drawer
        await page.evaluate("""() => {
            const closeBtn = document.querySelector('.icon-Icon_BetslipFold_48') ||
                             document.querySelector('.back-ico') ||
                             document.querySelector('.mark-bg');
            if (closeBtn) closeBtn.click();
        }""")
        await asyncio.sleep(2)
        return True
    return False
