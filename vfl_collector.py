#!/usr/bin/env python3
"""
vfl_collector.py — Clean VFL data collector
============================================
Collects betpawa + betkraft results + odds and saves to Postgres.

Odds strategy:
  - Betpawa: dedicated 2s poller grabs odds during pre-match window
  - Betkraft: dedicated poller grabs odds via /data before round starts
  - Results saved once FT score confirmed

Run:
  DATABASE_URL=postgresql://... python3 vfl_collector.py
"""

import os, json, time, threading, requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import pg8000
from urllib.parse import urlparse

DATABASE_URL          = os.environ.get('DATABASE_URL', '')
POLL_BP_ODDS_SECS     = 2   # fast — catch the 5-min betpawa window
POLL_BP_RESULTS_SECS  = 5
POLL_BK_ODDS_SECS     = 3   # fast — catch betkraft pre-match
POLL_BK_RESULTS_SECS  = 6


# ── DB ─────────────────────────────────────────────────────────────────
def get_db():
    u = urlparse(DATABASE_URL)
    return pg8000.connect(
        host=u.hostname, port=u.port or 5432,
        database=u.path.lstrip('/'),
        user=u.username, password=u.password,
        ssl_context=True, timeout=15,
    )

def execute_values(conn, sql, rows):
    if not rows: return
    n    = len(rows[0])
    ph   = '(' + ','.join(['%s'] * n) + ')'
    full = sql.replace('VALUES %s', 'VALUES ' + ','.join([ph] * len(rows)))
    flat = [v for row in rows for v in row]
    cur  = conn.cursor()
    cur.execute(full, flat)
    conn.commit()

def init_schema():
    stmts = [
        """CREATE TABLE IF NOT EXISTS betpawa_rounds (
            id           SERIAL PRIMARY KEY,
            round_id     TEXT NOT NULL,
            league       TEXT NOT NULL,
            league_id    TEXT,
            home         TEXT NOT NULL,
            away         TEXT NOT NULL,
            ft_h INT, ft_a INT, ht_h INT, ht_a INT,
            htft_outcome TEXT,
            odds_1 FLOAT, odds_x FLOAT, odds_2 FLOAT,
            ou_15_over FLOAT, ou_15_under FLOAT,
            ou_25_over FLOAT, ou_25_under FLOAT,
            ou_35_over FLOAT, ou_35_under FLOAT,
            btts_yes FLOAT, btts_no FLOAT,
            htft_11 FLOAT, htft_1x FLOAT, htft_12 FLOAT,
            htft_x1 FLOAT, htft_xx FLOAT, htft_x2 FLOAT,
            htft_21 FLOAT, htft_2x FLOAT, htft_22 FLOAT,
            collected_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(round_id, league_id, home, away)
        )""",
        """CREATE TABLE IF NOT EXISTS betkraft_rounds (
            id           SERIAL PRIMARY KEY,
            round_id     TEXT NOT NULL,
            match_n      INT,
            home TEXT NOT NULL, away TEXT NOT NULL,
            ft_h INT, ft_a INT, ht_h INT, ht_a INT,
            markets      TEXT,
            odds_1 FLOAT, odds_x FLOAT, odds_2 FLOAT,
            ou_25_over FLOAT, ou_25_under FLOAT,
            btts_yes FLOAT,
            collected_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(round_id, home, away)
        )""",
        "CREATE INDEX IF NOT EXISTS bp_pair ON betpawa_rounds(league_id,home,away)",
        "CREATE INDEX IF NOT EXISTS bp_rnd  ON betpawa_rounds(round_id)",
        "CREATE INDEX IF NOT EXISTS bk_pair ON betkraft_rounds(home,away)",
        "CREATE INDEX IF NOT EXISTS bk_rnd  ON betkraft_rounds(round_id)",
    ]
    conn = get_db()
    try:
        cur = conn.cursor()
        for s in stmts:
            cur.execute(s)
        conn.commit()
        print("[init] Schema ready", flush=True)
    finally:
        conn.close()


# ── HTTP ────────────────────────────────────────────────────────────────
def fetch(url, headers, payload=None, timeout=10):
    for i in range(4):
        try:
            if payload is not None:
                r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1 + i)
    return None


# ══════════════════════════════════════════════════════════════════════
#  BETPAWA
# ══════════════════════════════════════════════════════════════════════
BP_H = {
    'x-pawa-brand': 'betpawa-uganda',
    'x-pawa-language': 'en',
    'devicetype': 'web',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}
BP_BASE    = 'https://www.betpawa.ug/api/sportsbook/virtual'
BP_SEASONS = f'{BP_BASE}/v2/seasons/list/actual'
BP_EVENTS  = f'{BP_BASE}/v3/events/list/by-round/{{round_id}}'
BP_LEAGUES = {
    '7794':'English League','7795':'Spanish League',
    '7796':'Italian League','9183':'French League',
    '9184':'German League','13773':'Portuguese League','13774':'Dutch League',
}

def bp_scores(ppr):
    ht={}; ft={}
    for p in ppr:
        pt = p['participant']['type']
        for pr in p.get('periodResults',[]):
            s = pr['period']['slug']; v = int(pr['result'])
            if s in ('FIRST_HALF','HALF_TIME'): ht[pt]=v
            elif s == 'FULL_TIME_EXCLUDING_OVERTIME': ft[pt]=v
    return ht.get('HOME'), ht.get('AWAY'), ft.get('HOME'), ft.get('AWAY')

def bp_htft(hth,hta,fth,fta):
    if None in (hth,hta,fth,fta): return None
    hr='1' if hth>hta else('2' if hta>hth else 'X')
    fr='1' if fth>fta else('2' if fta>fth else 'X')
    return f"{hr}/{fr}"

def bp_markets(event):
    m={'1x2':{},'ou':[],'btts':{},'htft':{}}
    for mk in event.get('markets',[]):
        name = mk.get('marketType',{}).get('name','')
        rows = mk.get('row',[])
        if name=='1X2 - FT' and rows:
            for p in rows[0].get('prices',[]): m['1x2'][p['name']]=float(p['price'])
        elif name=='Both Teams To Score - FT' and rows:
            for p in rows[0].get('prices',[]): m['btts'][p['name'].lower()]=float(p['price'])
        elif name=='Total Score Over/Under - FT':
            for row in rows:
                line={}
                for p in row.get('prices',[]): line[p['name'].lower()]=float(p['price'])
                m['ou'].append(line)
        elif name=='HT / FT':
            for row in rows:
                for p in row.get('prices',[]): m['htft'][p['name']]=float(p['price'])
    return m

def bp_save(records):
    if not records: return
    conn=get_db()
    try:
        execute_values(conn,"""
            INSERT INTO betpawa_rounds
              (round_id,league,league_id,home,away,
               ft_h,ft_a,ht_h,ht_a,htft_outcome,
               odds_1,odds_x,odds_2,
               ou_15_over,ou_15_under,ou_25_over,ou_25_under,
               ou_35_over,ou_35_under,btts_yes,btts_no,
               htft_11,htft_1x,htft_12,htft_x1,htft_xx,htft_x2,htft_21,htft_2x,htft_22)
            VALUES %s
            ON CONFLICT (round_id,league_id,home,away) DO NOTHING
        """, records)
    finally: conn.close()

def bp_collect():
    seen=set(); mkt_cache={}; saved=0

    # ── Fast odds poller (every 2s) ────────────────────────────────
    def odds_poller():
        print("[betpawa/odds] Poller started (2s)", flush=True)
        while True:
            try:
                data = fetch(BP_SEASONS, BP_H)
                if not data: time.sleep(2); continue
                for season in data.get('items',[]):
                    for rnd in season.get('rounds',[]):
                        ed = fetch(BP_EVENTS.format(round_id=rnd['id']), BP_H)
                        if not ed: continue
                        for e in ed.get('responses',[]):
                            mkts = bp_markets(e)
                            if mkts['1x2'] or mkts['htft']:
                                mkt_cache[e['id']] = mkts
                                print(f"[betpawa/odds] cached rnd={rnd['id']} eid={e['id']} 1x2={mkts['1x2']}", flush=True)
            except Exception as ex:
                print(f"[betpawa/odds] {ex}", flush=True)
            time.sleep(POLL_BP_ODDS_SECS)

    threading.Thread(target=odds_poller, name='bp_odds', daemon=True).start()

    # ── Results poller (every 5s) ──────────────────────────────────
    print("[betpawa] Results poller started", flush=True)
    while True:
        try:
            data = fetch(BP_SEASONS, BP_H)
            if not data: time.sleep(POLL_BP_RESULTS_SECS); continue
            for season in data.get('items',[]):
                for rnd in season.get('rounds',[]):
                    round_id = rnd['id']
                    ed = fetch(BP_EVENTS.format(round_id=round_id), BP_H)
                    if not ed: continue
                    records=[]
                    for e in ed.get('responses',[]):
                        eid=e['id']
                        name=e.get('name','')
                        if ' - ' not in name: continue
                        home,away=[x.strip() for x in name.split(' - ',1)]
                        comp=e.get('competition',{}); lid=str(comp.get('id',''))
                        lname=BP_LEAGUES.get(lid,comp.get('name',''))
                        if not lname: continue
                        # Also cache if odds present
                        mkts=bp_markets(e)
                        if mkts['1x2'] or mkts['htft']:
                            mkt_cache[eid]=mkts
                        key=(round_id,lid,home,away)
                        if key in seen: continue
                        ppr=e.get('results',{}).get('participantPeriodResults',[])
                        if not ppr: continue
                        hth,hta,fth,fta=bp_scores(ppr)
                        if fth is None: continue
                        seen.add(key)
                        m=mkt_cache.get(eid,mkts)
                        x12=m.get('1x2',{}); ou=m.get('ou',[]); btts=m.get('btts',{}); htft=m.get('htft',{})
                        outcome=bp_htft(hth,hta,fth,fta)
                        records.append((
                            round_id,lname,lid,home,away,
                            fth,fta,hth,hta,outcome,
                            x12.get('1'),x12.get('X'),x12.get('2'),
                            ou[0].get('over')  if len(ou)>0 else None,
                            ou[0].get('under') if len(ou)>0 else None,
                            ou[1].get('over')  if len(ou)>1 else None,
                            ou[1].get('under') if len(ou)>1 else None,
                            ou[2].get('over')  if len(ou)>2 else None,
                            ou[2].get('under') if len(ou)>2 else None,
                            btts.get('yes'),btts.get('no'),
                            htft.get('1/1'),htft.get('1/X'),htft.get('1/2'),
                            htft.get('X/1'),htft.get('X/X'),htft.get('X/2'),
                            htft.get('2/1'),htft.get('2/X'),htft.get('2/2'),
                        ))
                        has_odds = '✓' if x12.get('1') else '✗'
                        print(f"[betpawa] [{lname}] {home} v {away} HT={hth}:{hta} FT={fth}:{fta} HTFT={outcome} odds={has_odds}", flush=True)
                    if records:
                        bp_save(records); saved+=len(records)
                        print(f"[betpawa] +{len(records)} saved (total {saved})", flush=True)
        except Exception as e:
            print(f"[betpawa] Error: {e}", flush=True)
        time.sleep(POLL_BP_RESULTS_SECS)


# ══════════════════════════════════════════════════════════════════════
#  BETKRAFT
# ══════════════════════════════════════════════════════════════════════
BK_H = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0',
    'Accept':     'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    'Referer':    'https://legacy-ui.betkraft.co.uk/',
    'Origin':     'https://legacy-ui.betkraft.co.uk',
}
BK_BASE    = 'https://vl.betkraft.co.uk'
BK_PERIODS = f'{BK_BASE}/periods/1'
BK_DATA    = f'{BK_BASE}/data'
BK_RESULTS = f'{BK_BASE}/results/1/0'

BK_ALL_MARKETS = ['1X2','GG','TG15','TG25','DC','TG35','H1X2','DCH','HS',
                  '1X2G','1X2OU15','1X2OU25','1X2OU35','CS','DR','TG','TGOE',
                  'MG','FTS','TFG','T1G','T2G','HGG']

def bk_fetch_odds(rn_id):
    """Fetch all 23 markets for a round, merge into per-match dict."""
    match_odds = {}
    def _fetch(mkt):
        d = fetch(BK_DATA, BK_H, payload={
            'round_number_id': rn_id, 'competition_id': 1,
            'country_id': None, 'market_id': mkt
        })
        if not d: return
        for m in d.get('data',{}).get('matches',[]):
            eid = m['event_id']
            if eid not in match_odds:
                match_odds[eid] = {'home': m['home'], 'away': m['away'], 'markets': []}
            for mk in m.get('markets',[]):
                if mk.get('market_id') == mkt:
                    match_odds[eid]['markets'].append(mk)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_fetch, BK_ALL_MARKETS))
    return match_odds

def bk_parse_score(s):
    try: parts=s.split(':'); return int(parts[0]),int(parts[1])
    except: return None,None

def bk_save(records):
    if not records: return
    conn=get_db()
    try:
        execute_values(conn,"""
            INSERT INTO betkraft_rounds
              (round_id,match_n,home,away,ft_h,ft_a,ht_h,ht_a,
               markets,odds_1,odds_x,odds_2,ou_25_over,ou_25_under,btts_yes)
            VALUES %s
            ON CONFLICT (round_id,home,away) DO NOTHING
        """, records)
    finally: conn.close()

def bk_collect():
    seen=set(); odds_cache={}; saved=0

    # ── Fast odds poller ───────────────────────────────────────────
    def odds_poller():
        print("[betkraft/odds] Poller started (3s)", flush=True)
        while True:
            try:
                pdata = fetch(BK_PERIODS, BK_H)
                if not pdata: time.sleep(3); continue
                periods = pdata.get('data',{}).get('periods',[])
                # Poll last 3 periods (upcoming)
                for period in periods[-3:]:
                    rn_id = period.get('round_number_id')
                    if not rn_id: continue
                    match_odds = bk_fetch_odds(rn_id)
                    if match_odds:
                        odds_cache[rn_id] = match_odds
                        print(f"[betkraft/odds] cached rn={rn_id} matches={len(match_odds)}", flush=True)
            except Exception as ex:
                print(f"[betkraft/odds] {ex}", flush=True)
            time.sleep(POLL_BK_ODDS_SECS)

    threading.Thread(target=odds_poller, name='bk_odds', daemon=True).start()

    # ── Results poller ─────────────────────────────────────────────
    print("[betkraft] Results poller started", flush=True)
    while True:
        try:
            rdata = fetch(BK_RESULTS, BK_H)
            if not rdata: time.sleep(POLL_BK_RESULTS_SECS); continue
            rounds = rdata.get('data',{}).get('results',[])
            records=[]
            for rnd in rounds:
                round_id = str(rnd.get('round_id',''))
                if not round_id: continue
                # Try to get odds for this round from cache
                # round_id != round_number_id — map via season_id or just store what we have
                cached_odds = odds_cache.get(round_id, {})
                for i,m in enumerate(rnd.get('matches',[])):
                    home=(m.get('home') or m.get('home_team') or '').strip()
                    away=(m.get('away') or m.get('away_team') or '').strip()
                    if not home or not away: continue
                    fth,fta=bk_parse_score(m.get('result',''))
                    if fth is None: continue
                    key=(round_id,home,away)
                    if key in seen: continue
                    seen.add(key)
                    hth,hta=bk_parse_score(m.get('half_time_scores',''))
                    # Find odds from cache by team name
                    match_odd = next((v for v in cached_odds.values()
                                      if v.get('home')==home and v.get('away')==away), {})
                    mkts = match_odd.get('markets',[])
                    mkts_dict = {mk['market_id']: mk for mk in mkts}
                    x12  = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('1X2',{}).get('outcomes',[])}
                    ou25 = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('TG25',{}).get('outcomes',[])}
                    btts = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('GG',{}).get('outcomes',[])}
                    has_odds = '✓' if x12 else '✗'
                    records.append((
                        round_id,i+1,home,away,fth,fta,hth,hta,
                        json.dumps([{'market_id':mk['market_id'],'outcomes':mk.get('outcomes',[])} for mk in mkts]),
                        x12.get('1'),x12.get('X'),x12.get('2'),
                        ou25.get('O'),ou25.get('U'),
                        btts.get('Y'),
                    ))
                    print(f"[betkraft] rnd={round_id} {home} v {away} HT={hth}:{hta} FT={fth}:{fta} odds={has_odds}", flush=True)
            if records:
                bk_save(records); saved+=len(records)
                print(f"[betkraft] +{len(records)} saved (total {saved})", flush=True)
        except Exception as e:
            print(f"[betkraft] Error: {e}", flush=True)
        time.sleep(POLL_BK_RESULTS_SECS)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    if not DATABASE_URL:
        print("[error] DATABASE_URL not set. Exiting.", flush=True)
        exit(1)

    try:
        init_schema()
    except Exception as e:
        print(f"[init] Schema error: {e} — continuing", flush=True)

    t_bp = threading.Thread(target=bp_collect, name='betpawa',  daemon=True)
    t_bk = threading.Thread(target=bk_collect, name='betkraft', daemon=True)
    t_bp.start()
    t_bk.start()
    print("[main] Both collectors running.", flush=True)

    try:
        while True:
            time.sleep(60)
            bp_ok=t_bp.is_alive(); bk_ok=t_bk.is_alive()
            print(f"[health] betpawa={'OK' if bp_ok else 'DEAD'} betkraft={'OK' if bk_ok else 'DEAD'}", flush=True)
            if not bp_ok:
                t_bp=threading.Thread(target=bp_collect,name='betpawa',daemon=True); t_bp.start()
                print("[health] betpawa restarted", flush=True)
            if not bk_ok:
                t_bk=threading.Thread(target=bk_collect,name='betkraft',daemon=True); t_bk.start()
                print("[health] betkraft restarted", flush=True)
    except KeyboardInterrupt:
        print("\n[main] Stopped.", flush=True)
