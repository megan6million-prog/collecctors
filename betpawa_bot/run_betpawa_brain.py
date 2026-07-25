#!/usr/bin/env python3
"""
run_betpawa_brain.py — Brain V2 live client
============================================
Reads live rounds from SQLite DB, evaluates with Brain V2,
builds 6-fold accumulators, places via betpawa.py automation.

Run:
  python3 run_betpawa_brain.py           # dry-run (default)
  python3 run_betpawa_brain.py --live    # place real bets
"""
import os, sys, json, sqlite3, asyncio, random, logging
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, '/home/voltrix/bongo_runner')
sys.path.insert(0, '/home/voltrix/Desktop/txts/pawa')
sys.path.insert(0, '/home/voltrix/Desktop')

from betpawa_brain import BetpawaBrain, MatchOdds, RULE_WIN_PROBS
from betpawa_htft_brain import HTFTBrain

# ── Mode: 'htft' uses pair matrix, 'v2' uses form rules ──────────────
BRAIN_MODE = os.environ.get("BRAIN_MODE", "htft")   # 'htft' | 'v2'
HTFT_BRAIN = HTFTBrain() if BRAIN_MODE == "htft" else None

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bp_brain")

# ── Config ────────────────────────────────────────────────────────────
SQLITE_PATH = "/home/voltrix/vfl_data/vfl.db"
N_FOLD      = int(os.environ.get("N_FOLD", "6"))
STAKE_UGX   = int(os.environ.get("STAKE_UGX", "1"))
HIGH_CONF   = {'R1','R1H','R2','R3','R3B','R3H','R4','R4B'}

RESET="\033[0m"; BOLD="\033[1m"; GREEN="\033[32m"
RED="\033[31m";  YELLOW="\033[33m"; CYAN="\033[36m"


# ── Brain ─────────────────────────────────────────────────────────────

def seed_brain():
    brain = BetpawaBrain()
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT home_team, away_team, hg, ag FROM (
            SELECT home_team, away_team, hg, ag, MIN(id) as mid
            FROM betpawa_events WHERE hg IS NOT NULL
            GROUP BY round_id, home_team, away_team
        ) ORDER BY mid ASC
    """)
    n = 0
    for r in cur.fetchall():
        brain.teams[r['home_team']].update(int(r['hg'] or 0), int(r['ag'] or 0))
        brain.teams[r['away_team']].update(int(r['ag'] or 0), int(r['hg'] or 0))
        n += 1
    conn.close()
    log.info("Brain seeded: %d matches, %d teams", n, len(brain.teams))
    return brain


# ── Slip builder ──────────────────────────────────────────────────────

def build_slips(signals, n_fold=6):
    hc = [s for s in signals if s['rule'] in HIGH_CONF and s['market'] == 'OU25']
    by_rule = defaultdict(list)
    for s in hc: by_rule[s['rule']].append(s)
    pools = [sorted(v, key=lambda x: -x['odds']) for v in by_rule.values() if v]
    slips = []; current = []; used = set()
    while True:
        pools = [p for p in pools if p]
        if not pools: break
        pools.sort(key=lambda p: -p[0]['odds'])
        added = False
        for pool in pools:
            if not pool or len(current) >= n_fold: continue
            cand = pool[0]
            mk = (cand['home'], cand['away'])
            if mk in used: continue
            pool.pop(0); current.append(cand); used.add(mk); added = True
        if len(current) >= n_fold:
            slips.append(current[:n_fold]); current = []; used = set()
        elif not added:
            break
    return slips


# ── DB helpers ────────────────────────────────────────────────────────

def get_latest_round():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT MIN(id) as id, round_id, home_team, away_team, hg, ag,
               odd_1, odd_X, odd_2, btts_yes, btts_no, ou_data
        FROM betpawa_events WHERE hg IS NOT NULL AND odd_1 IS NOT NULL
        GROUP BY round_id, home_team, away_team
        ORDER BY MIN(id) DESC LIMIT 500
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows: return None, []
    rid = rows[0]['round_id']
    return rid, [dict(r) for r in rows if r['round_id'] == rid]


def find_settled_round_by_teams(home_team, away_team):
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT round_id FROM betpawa_events
        WHERE home_team = ? AND away_team = ? AND hg IS NOT NULL
        ORDER BY id DESC LIMIT 1
    """, (home_team, away_team))
    row = cur.fetchone()
    conn.close()
    if row:
        return row['round_id']
    return None


def get_round_matches(round_id):
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT round_id, home_team, away_team, hg, ag FROM betpawa_events
        WHERE round_id = ? AND hg IS NOT NULL
        GROUP BY home_team, away_team
    """, (round_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def evaluate_round(brain, matches):
    signals = []
    for r in matches:
        ou = json.loads(r.get('ou_data') or '[]')
        try:
            mo = MatchOdds(
                home=r['home_team'], away=r['away_team'],
                h_odd=float(r['odd_1'] or 0), d_odd=float(r['odd_X'] or 0),
                a_odd=float(r['odd_2'] or 0),
                u25=float(ou[1]['under']) if len(ou) > 1 else 0,
                o25=float(ou[1]['over'])  if len(ou) > 1 else 0,
                btts_yes=float(r['btts_yes'] or 0),
                btts_no =float(r['btts_no']  or 0),
                dc_1x=0, dc_x2=0, dc_12=0,
            )
        except Exception:
            continue
        for sig in brain.evaluate([mo]):
            signals.append({
                'sig': sig, 'rule': sig.rule, 'market': sig.market,
                'pick': sig.pick, 'odds': sig.odds,
                'wp': RULE_WIN_PROBS.get(sig.rule, 0.5),
                'home': r['home_team'], 'away': r['away_team'],
                'hg': int(r['hg'] or 0), 'ag': int(r['ag'] or 0),
            })
    return signals


def evaluate_htft_round(matches: list) -> list:
    """
    HTFT brain: for each match in the round, look up the pair matrix
    and return flat bet signals (one per +EV HTFT market).
    Output dict is compatible with existing slip/dashboard machinery.
    """
    signals = []
    seen = set()
    for r in matches:
        home = r.get('home_team') or r.get('home', '')
        away = r.get('away_team') or r.get('away', '')
        if not home or not away: continue
        pair = (home, away)
        if pair in seen: continue
        seen.add(pair)
        for sig in HTFT_BRAIN.evaluate(home, away):
            signals.append({
                'sig':    sig,
                'rule':   'HTFT',
                'market': 'HTFT',
                'pick':   sig.market,          # e.g. '1/2', '2/X'
                'odds':   sig.odds,
                'wp':     sig.p_win,
                'ev':     sig.ev,
                'kelly':  sig.kelly_frac,
                'home':   home,
                'away':   away,
                'hg':     int(r.get('hg') or 0),
                'ag':     int(r.get('ag') or 0),
            })
    return signals


def evaluate_scraped_round(brain, matches):
    signals = []
    for r in matches:
        odds = r.get('odds', {})
        x12 = odds.get('1x2', {})
        ou = odds.get('ou', [])
        btts = odds.get('btts', {})
        try:
            mo = MatchOdds(
                home=r['home'], away=r['away'],
                h_odd=float(x12.get('1') or 0), d_odd=float(x12.get('X') or 0),
                a_odd=float(x12.get('2') or 0),
                u25=float(ou[2]['under']) if len(ou) > 2 else 0,
                o25=float(ou[2]['over'])  if len(ou) > 2 else 0,
                btts_yes=float(btts.get('yes') or 0),
                btts_no =float(btts.get('no') or 0),
                dc_1x=0, dc_x2=0, dc_12=0,
            )
        except Exception:
            continue
        for sig in brain.evaluate([mo]):
            signals.append({
                'sig': sig, 'rule': sig.rule, 'market': sig.market,
                'pick': sig.pick, 'odds': sig.odds,
                'wp': RULE_WIN_PROBS.get(sig.rule, 0.5),
                'home': r['home'], 'away': r['away'],
                'hg': 0, 'ag': 0,
            })
    return signals


# ── Dashboard ─────────────────────────────────────────────────────────

def draw_dashboard(stats, active_slips, history):
    print("\033[2J\033[H", end="")
    now = datetime.now().strftime("%H:%M:%S")
    mode = f"{RED}{BOLD}[LIVE]{RESET}" if not stats['dry_run'] else f"{GREEN}{BOLD}[DRY RUN]{RESET}"
    print(f"{BOLD}{CYAN}{'='*80}")
    print(f"  BETPAWA BRAIN V2  |  {now}  |  {mode}  |  N_FOLD={N_FOLD}  STAKE={STAKE_UGX:,}")
    print(f"{'='*80}{RESET}")

    roi = 100 * stats['profit'] / stats['start_balance'] if stats['start_balance'] else 0
    pc  = GREEN if stats['profit'] >= 0 else RED
    print(f"  {BOLD}Balance:{RESET} {stats['bankroll']:,.0f} UGX  |  "
          f"{BOLD}Profit:{RESET} {pc}{stats['profit']:+,.0f} UGX ({roi:+.1f}%){RESET}  |  "
          f"{BOLD}Slips:{RESET} {stats['total']} W:{GREEN}{stats['wins']}{RESET} "
          f"L:{RED}{stats['losses']}{RESET} "
          f"WR:{YELLOW}{100*stats['wins']/max(1,stats['total']):.0f}%{RESET}")

    print(f"{CYAN}{'-'*80}{RESET}")
    print(f"  {BOLD}{YELLOW}ACTIVE SLIPS THIS ROUND:{RESET}")
    if not active_slips:
        print(f"    Waiting for next round...")
    else:
        for i, slip in enumerate(active_slips):
            acca = 1.0
            for s in slip: acca *= s['odds']
            joint_wp = 1.0
            for s in slip: joint_wp *= s['wp']
            print(f"  {BOLD}Slip {i+1}:{RESET} {CYAN}{acca:.0f}x{RESET}  "
                  f"WP:{YELLOW}{joint_wp*100:.0f}%{RESET}  "
                  f"Pot.win:{GREEN}{int(STAKE_UGX*acca):,} UGX{RESET}")
            for s in slip:
                print(f"    [{s['rule']:<4}] {s['home']:<4} vs {s['away']:<4}  "
                      f"U25 @ {s['odds']:.2f}x  wp={s['wp']*100:.0f}%")

    print(f"{CYAN}{'-'*80}{RESET}")
    print(f"  {BOLD}{GREEN}RECENT HISTORY (last 8 slips):{RESET}")
    if not history:
        print(f"    No results yet.")
    for h in reversed(history[-8:]):
        c = GREEN if h['won'] else RED
        slip_res = f"{c}{'✓ WIN' if h['won'] else '✗ LOSS'}{RESET}"
        payout_str = f"{c}+{h['payout']:,.0f}{RESET}" if h['won'] else f"{c}-{h['stake']:,.0f}{RESET}"
        print(f"  [{h['round_id']}] {BOLD}{h['acca_odds']:.0f}x{RESET}  {slip_res}  {payout_str}  stake={h['stake']:,}")
        # Per-leg detail
        for leg in h.get('legs', []):
            won_leg = leg['hg'] + leg['ag'] <= 2
            tick = f"{GREEN}✓{RESET}" if won_leg else f"{RED}✗{RESET}"
            score = f"{leg['hg']}:{leg['ag']}"
            tg = leg['hg'] + leg['ag']
            tg_str = f"{GREEN}U({tg}){RESET}" if won_leg else f"{RED}O({tg}){RESET}"
            print(f"    {tick} [{leg['rule']:<4}] {leg['home']:<4} vs {leg['away']:<4}  "
                  f"U@{leg['odds']:.2f}  score:{score}  {tg_str}")
    print(f"{BOLD}{CYAN}{'='*80}{RESET}")


# ── Placement ─────────────────────────────────────────────────────────

async def place_slip_live(slip, stake):
    from platforms.betpawa import (
        navigate_to_virtuals, click_match_odd, set_stake, confirm_bet, _click_tab
    )
    from browser_harness import wait

    if BRAIN_MODE == "htft":
        # Each slip is a single HTFT bet: [signal]
        # Place each bet independently (no accumulator)
        s = slip[0]
        home  = s['home']
        away  = s['away']
        pick  = s['pick']   # e.g. '1/2', '2/X', 'X/1' etc.
        await navigate_to_virtuals()
        await _click_tab("HTFT")
        await wait(1.5)
        clicked = await click_match_odd(home, 'HTFT', pick)
        if not clicked:
            log.error("HTFT click failed: %s v %s [%s]", home, away, pick)
            return False
        await asyncio.sleep(random.uniform(0.3, 0.6))
        await set_stake(stake)
        await asyncio.sleep(0.5)
        await confirm_bet()
        return True
    else:
        # Original V2 accumulator: multiple U25 legs
        await navigate_to_virtuals()
        await _click_tab("O/U")
        await wait(1.5)
        for s in slip:
            clicked = await click_match_odd(s['home'], 'OU25', 'Under')
            if not clicked:
                log.error("Click failed: %s", s['home'])
                return False
            await asyncio.sleep(random.uniform(0.3, 0.6))
        await set_stake(stake)
        await asyncio.sleep(0.5)
        await confirm_bet()
        return True


# ── Main ──────────────────────────────────────────────────────────────

async def run(live=False):
    log.info("=== BETPAWA BRAIN V2 ===")
    log.info("DRY_RUN=%s | N_FOLD=%d | STAKE=%d", not live, N_FOLD, STAKE_UGX)

    brain = seed_brain()

    if live:
        from browser_harness import set_source
        set_source("betpawa")
        from platforms.betpawa import ensure_login, get_balance, navigate_to_virtuals, get_matches_odds
        log.info("Logging in...")
        ok = await ensure_login()
        if not ok:
            log.error("Login failed"); return
        bal = await get_balance()
        log.info("Balance: %s UGX", f"{int(bal):,}" if bal else "?")
        start_bal = bal or STAKE_UGX * 30
        
        stats = {
            'bankroll': start_bal, 'start_balance': start_bal,
            'profit': 0, 'wins': 0, 'losses': 0, 'total': 0,
            'dry_run': False
        }
        history = []
    else:
        # Dry-run: replay history first to build dashboard state
        import glob
        log.info("Replaying historical rounds from JSON files...")
        stats = {
            'bankroll': STAKE_UGX * 50.0, 'start_balance': STAKE_UGX * 50.0,
            'profit': 0.0, 'wins': 0, 'losses': 0, 'total': 0,
            'dry_run': True
        }
        history = []
        
        # Load and sort JSON rounds
        files = glob.glob("/home/voltrix/vfl_data/betpawa_round_*.json")
        json_rounds = []
        for fp in files:
            try:
                with open(fp) as f:
                    data = json.load(f)
                rid = data.get("round_id")
                events = data.get("events", [])
                if rid and events and (data.get("complete") or any(e.get("result") for e in events)):
                    json_rounds.append((int(rid), data))
            except:
                continue
        
        json_rounds.sort(key=lambda x: x[0])
        log.info("Found %d complete JSON rounds", len(json_rounds))
        
        for rid, data in json_rounds:
            matches = []
            for e in data.get("events", []):
                mkts = e.get("odds", {}) or e.get("markets", {})
                matches.append({
                    'home_team': e.get('home_team') or e.get('home', ''),
                    'away_team': e.get('away_team') or e.get('away', ''),
                    'odd_1': mkts.get('1x2', {}).get('1'),
                    'odd_X': mkts.get('1x2', {}).get('X'),
                    'odd_2': mkts.get('1x2', {}).get('2'),
                    'btts_yes': mkts.get('btts', {}).get('yes'),
                    'btts_no': mkts.get('btts', {}).get('no'),
                    'ou_data': json.dumps(mkts.get('ou', [])),
                    'hg': e.get('hg'),
                    'ag': e.get('ag'),
                })
            signals = evaluate_htft_round(matches) if BRAIN_MODE == "htft" else evaluate_round(brain, matches)
            slips = [[s] for s in signals] if BRAIN_MODE == "htft" else build_slips(signals, N_FOLD)
            for slip in slips:
                acca = 1.0
                for s in slip: acca *= s['odds']
                if BRAIN_MODE == "htft":
                    # Single bet — win if actual HTFT matches pick
                    # During dry-run replay we don't have FT, so skip settlement
                    continue
                all_won = all(s['hg'] + s['ag'] <= 2 for s in slip)
                payout = int(STAKE_UGX * (acca - 1)) if all_won else -STAKE_UGX
                stats['bankroll'] += payout
                stats['profit'] += payout
                stats['total'] += 1
                if all_won: stats['wins'] += 1
                else: stats['losses'] += 1
                history.append({
                    'round_id': str(rid), 'won': all_won,
                    'acca_odds': acca, 'stake': STAKE_UGX,
                    'payout': int(STAKE_UGX * acca) if all_won else 0,
                    'legs': slip,
                })
            # Update main brain
            for m in matches:
                if m['hg'] is not None and m['ag'] is not None:
                    brain.update(m['home_team'], m['away_team'], int(m['hg']), int(m['ag']))
        
        log.info("Replay finished. Profit: %+d, Bankroll: %d", stats['profit'], stats['bankroll'])

    last_rid = None
    last_upcoming_key = None

    while True:
        try:
            if live:
                from platforms.betpawa import navigate_to_virtuals, get_matches_odds
                # Only navigate if we don't have a current round loaded yet
                if last_upcoming_key is None:
                    await navigate_to_virtuals()

                live_data = await get_matches_odds()
                matches = live_data.get('matches', [])
                if not matches:
                    log.warning("No matches found. Waiting...")
                    await asyncio.sleep(15)
                    continue

                upcoming_key = matches[0]['home'] + matches[0]['away']
                if upcoming_key == last_upcoming_key:
                    # Same round — just wait, no page reload
                    await asyncio.sleep(15)
                    continue

                # New round detected — navigate fresh
                log.info("New upcoming round: %s vs %s", matches[0]['home'], matches[0]['away'])
                await navigate_to_virtuals()
                if BRAIN_MODE == "htft":
                    signals = evaluate_htft_round(matches)
                else:
                    signals = evaluate_scraped_round(brain, matches)
                log.info("Signals: %d [mode=%s]", len(signals), BRAIN_MODE)

                slips = build_slips(signals, N_FOLD) if BRAIN_MODE != "htft" else [[s] for s in signals]
                log.info("Slips: %d", len(slips))
                draw_dashboard(stats, slips, history)

                placed_slips = []
                for i, slip in enumerate(slips):
                    acca = 1.0
                    for s in slip: acca *= s['odds']
                    legs_str = " | ".join(f"{s['home']} vs {s['away']} U@{s['odds']:.2f}" for s in slip)
                    log.info("Slip %d/%d: %.0fx — %s", i+1, len(slips), acca, legs_str)

                    # Place live
                    ok = await place_slip_live(slip, STAKE_UGX)
                    log.info("  → %s", "PLACED ✓" if ok else "FAILED ✗")
                    if ok:
                        placed_slips.append(slip)
                        # Log to file
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        line = f"[{ts}] [LIVE] SLIP {acca:.0f}x stake={STAKE_UGX}  {legs_str}"
                        with open(os.path.expanduser("~/betpawa_bets.log"), "a") as f:
                            f.write(line + "\n")
                    await asyncio.sleep(2)

                # Wait for round to settle in SQLite
                home_anchor = matches[0]['home']
                away_anchor = matches[0]['away']
                log.info("Waiting for matches to finish and settle in SQLite...")
                
                settled_rid = None
                for _ in range(40):  # up to 10 minutes
                    settled_rid = find_settled_round_by_teams(home_anchor, away_anchor)
                    if settled_rid:
                        break
                    await asyncio.sleep(15)

                if not settled_rid:
                    log.error("Timed out waiting for SQLite update. Proceeding anyway.")
                    last_upcoming_key = upcoming_key
                    continue

                log.info("Round %s settled in SQLite. Processing results...", settled_rid)
                result_matches = get_round_matches(settled_rid)
                
                # Map results
                res_map = {}
                for rm in result_matches:
                    res_map[(rm['home_team'], rm['away_team'])] = (rm['hg'], rm['ag'])

                # Settle placed slips
                for slip in placed_slips:
                    acca = 1.0
                    for s in slip: acca *= s['odds']
                    
                    legs_result = []
                    all_won = True
                    for s in slip:
                        res = res_map.get((s['home'], s['away']))
                        if res is None:
                            res = res_map.get((s['away'], s['home']))
                            if res is not None:
                                hg, ag = res[1], res[0]
                            else:
                                hg, ag = 0, 0
                        else:
                            hg, ag = res[0], res[1]

                        if BRAIN_MODE == "htft":
                            # SQLite hg/ag = HT score only
                            # We log the HT score; HTFT win determination
                            # requires FT score which isn't in SQLite.
                            # Mark as PENDING — human verifies or scrape FT later.
                            # For now assume unknown = loss (conservative).
                            won_leg = False   # TODO: derive from FT when available
                            log.warning("HTFT settlement PENDING for %s v %s [%s] HT=%d:%d",
                                        s['home'], s['away'], s['pick'], hg, ag)
                        else:
                            won_leg = (hg + ag <= 2)
                        if not won_leg:
                            all_won = False
                        
                        legs_result.append({
                            'home': s['home'], 'away': s['away'],
                            'odds': s['odds'], 'rule': s['rule'],
                            'pick': s.get('pick',''),
                            'hg': hg, 'ag': ag
                        })

                    payout = int(STAKE_UGX * (acca - 1)) if all_won else -STAKE_UGX
                    stats['bankroll'] += payout
                    stats['profit'] += payout
                    stats['total'] += 1
                    if all_won: stats['wins'] += 1
                    else: stats['losses'] += 1

                    history.append({
                        'round_id': settled_rid, 'won': all_won,
                        'acca_odds': acca, 'stake': STAKE_UGX,
                        'payout': int(STAKE_UGX * acca) if all_won else 0,
                        'legs': legs_result
                    })

                # Update brain
                for rm in result_matches:
                    brain.update(rm['home_team'], rm['away_team'], rm['hg'], rm['ag'])

                last_upcoming_key = upcoming_key
                draw_dashboard(stats, [], history)
                log.info("Round done. Waiting for next...\n")
                await asyncio.sleep(15)

            else:
                # Dry-run: passive loop watching SQLite
                rid, matches = get_latest_round()
                if not rid or rid == last_rid:
                    draw_dashboard(stats, [], history)
                    await asyncio.sleep(15)
                    continue

                log.info("Round %s | %d matches", rid, len(matches))
                if BRAIN_MODE == "htft":
                    signals = evaluate_htft_round(matches)
                else:
                    signals = evaluate_round(brain, matches)
                    hc = sum(1 for s in signals if s['rule'] in HIGH_CONF and s['market']=='OU25')
                    log.info("HC-U25: %d", hc)
                log.info("Signals: %d [mode=%s]", len(signals), BRAIN_MODE)

                slips = build_slips(signals, N_FOLD) if BRAIN_MODE != "htft" else [[s] for s in signals]
                log.info("Slips: %d", len(slips))
                draw_dashboard(stats, slips, history)

                for i, slip in enumerate(slips):
                    acca = 1.0
                    for s in slip: acca *= s['odds']
                    if BRAIN_MODE == "htft":
                        legs_str = "%s v %s [%s] @ %.1f EV=%+.0f%%" % (
                            slip[0]['home'], slip[0]['away'],
                            slip[0]['pick'], slip[0]['odds'],
                            slip[0].get('ev',0)*100)
                    else:
                        legs_str = " | ".join(f"{s['home']} vs {s['away']} U@{s['odds']:.2f}" for s in slip)
                    log.info("Bet %d/%d: %.0fx — %s", i+1, len(slips), acca, legs_str)

                    # Log to file
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mode_tag = "DRY-HTFT" if BRAIN_MODE == "htft" else "DRY"
                    line = f"[{ts}] [{mode_tag}] {acca:.0f}x stake={STAKE_UGX}  {legs_str}"
                    with open(os.path.expanduser("~/betpawa_bets.log"), "a") as f:
                        f.write(line + "\n")

                    if BRAIN_MODE == "htft":
                        # No FT in SQLite — skip P&L settlement in dry run
                        stats['total'] += 1
                        continue

                    # Determine result (V2 mode only)
                    all_won = all(s['hg'] + s['ag'] <= 2 for s in slip)
                    payout = int(STAKE_UGX * (acca - 1)) if all_won else -STAKE_UGX
                    stats['bankroll'] += payout
                    stats['profit'] += payout
                    stats['total'] += 1
                    if all_won: stats['wins'] += 1
                    else: stats['losses'] += 1

                    history.append({
                        'round_id': rid, 'won': all_won,
                        'acca_odds': acca, 'stake': STAKE_UGX,
                        'payout': int(STAKE_UGX * acca) if all_won else 0,
                        'legs': slip,
                    })

                # Update brain
                for m in matches:
                    brain.update(m['home_team'], m['away_team'], int(m.get('hg') or 0), int(m.get('ag') or 0))

                last_rid = rid
                draw_dashboard(stats, slips, history)
                log.info("Round done. Waiting...\n")
                await asyncio.sleep(15)

        except KeyboardInterrupt:
            log.info("Stopped"); break
        except Exception as e:
            log.error("Error: %s", e)
            await asyncio.sleep(30)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="Place real bets")
    args = p.parse_args()
    asyncio.run(run(live=args.live))
