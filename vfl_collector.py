#!/usr/bin/env python3
"""
vfl_collector.py — Clean VFL data collector
============================================
Rate-limited design:
  - Only fetches ACTIVE round (within tradingTime window) for odds
  - Only fetches LATEST completed round for results
  - Betkraft: /results/1/0 already batches all recent rounds
  - Max ~10 API calls per minute per platform
"""

import os, json, time, threading, requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pg8000
from urllib.parse import urlparse

DATABASE_URL = os.environ.get('DATABASE_URL', '')


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
def fetch(url, headers, payload=None, timeout=12):
    for i in range(3):
        try:
            if payload is not None:
                r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # Back off on 429 rate limit
            if r.status_code == 429:
                print(f"[ratelimit] {url[:60]} — sleeping 10s", flush=True)
                time.sleep(10)
        except Exception:
            pass
        time.sleep(2 + i * 2)
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

def bp_get_active_rounds(seasons):
    """Return only rounds currently in their trading window + last completed."""
    now = datetime.now(timezone.utc)
    active = []; recent_done = []
    for season in seasons:
        for rnd in season.get('rounds',[]):
            t  = rnd.get('tradingTime',{})
            t0 = t.get('start',''); t1 = t.get('end','')
            try:
                start = datetime.fromisoformat(t0.replace('Z','+00:00'))
                end   = datetime.fromisoformat(t1.replace('Z','+00:00'))
                if start <= now <= end:
                    active.append(rnd['id'])       # betting window open
                elif end < now:
                    recent_done.append((end, rnd['id']))  # finished
            except Exception:
                pass
    # Only keep last 5 completed rounds (avoid re-scanning old history)
    recent_done.sort(key=lambda x: x[0], reverse=True)
    return active, [r for _,r in recent_done[:5]]

def bp_collect():
    seen=set(); mkt_cache={}; saved=0
    print("[betpawa] Collector started", flush=True)

    while True:
        try:
            data = fetch(BP_SEASONS, BP_H)
            if not data: time.sleep(5); continue

            seasons = data.get('items',[])
            active_rounds, recent_rounds = bp_get_active_rounds(seasons)
            rounds_to_fetch = list(set(active_rounds + recent_rounds))

            print(f"[betpawa] active={len(active_rounds)} recent={len(recent_rounds)} fetching={len(rounds_to_fetch)}", flush=True)

            for round_id in rounds_to_fetch:
                ed = fetch(BP_EVENTS.format(round_id=round_id), BP_H)
                if not ed: continue
                time.sleep(0.5)  # gentle between round fetches

                records=[]
                for e in ed.get('responses',[]):
                    eid=e['id']
                    name=e.get('name','')
                    if ' - ' not in name: continue
                    home,away=[x.strip() for x in name.split(' - ',1)]
                    comp=e.get('competition',{}); lid=str(comp.get('id',''))
                    lname=BP_LEAGUES.get(lid,comp.get('name',''))
                    if not lname: continue

                    # Cache odds if in active window
                    if round_id in active_rounds:
                        mkts=bp_markets(e)
                        if mkts['1x2'] or mkts['htft']:
                            mkt_cache[eid]=mkts

                    key=(round_id,lid,home,away)
                    if key in seen: continue

                    ppr=e.get('results',{}).get('participantPeriodResults',[])
                    if not ppr: continue
                    hth,hta,fth,fta=bp_scores(ppr)
                    if fth is None: continue

                    # Validate FT score — must be >= HT score for each team
                    if hth is not None and (fth < hth or fta < hta): continue

                    seen.add(key)
                    m=mkt_cache.get(eid,{})
                    x12=m.get('1x2',{}); ou=m.get('ou',[]); btts=m.get('btts',{}); htft=m.get('htft',{})
                    outcome=bp_htft(hth,hta,fth,fta)
                    has_odds='✓' if x12.get('1') else '✗'
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
                    print(f"[betpawa] [{lname}] {home} v {away} HT={hth}:{hta} FT={fth}:{fta} HTFT={outcome} odds={has_odds}", flush=True)

                if records:
                    bp_save(records); saved+=len(records)
                    print(f"[betpawa] +{len(records)} saved (total {saved})", flush=True)

        except Exception as e:
            print(f"[betpawa] Error: {e}", flush=True)

        time.sleep(8)  # wait 8s before next full cycle (~12 req/min max)


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

def bk_fetch_round_odds(rn_id):
    """Fetch all markets for one round — sequential with delay to avoid rate limits."""
    match_odds = {}
    for mkt in BK_ALL_MARKETS:
        d = fetch(BK_DATA, BK_H, payload={
            'round_number_id': rn_id, 'competition_id': 1,
            'country_id': None, 'market_id': mkt
        })
        if d:
            for m in d.get('data',{}).get('matches',[]):
                eid = m['event_id']
                if eid not in match_odds:
                    match_odds[eid] = {'home':m['home'],'away':m['away'],'markets':{}}
                for mk in m.get('markets',[]):
                    if mk.get('market_id') == mkt:
                        match_odds[eid]['markets'][mkt] = mk
        time.sleep(0.3)  # 0.3s between market fetches = ~7s for all 23 markets
    return match_odds

def bk_parse_score(s):
    try: p=s.split(':'); return int(p[0]),int(p[1])
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
    # odds_cache keyed by season_id (str) → {event_id: {home,away,markets}}
    cached_seasons = set()
    print("[betkraft] Collector started", flush=True)

    while True:
        try:
            # 1. Cache odds for upcoming periods (keyed by season_id)
            pdata = fetch(BK_PERIODS, BK_H)
            if pdata:
                periods = pdata.get('data',{}).get('periods',[])
                for period in periods[-2:]:
                    rn_id  = period.get('round_number_id')
                    sid    = str(period.get('season_id',''))
                    if rn_id and sid and sid not in cached_seasons:
                        print(f"[betkraft/odds] Fetching odds rn={rn_id} season={sid}...", flush=True)
                        match_odds = bk_fetch_round_odds(rn_id)
                        if match_odds:
                            odds_cache[sid] = match_odds
                            cached_seasons.add(sid)
                            print(f"[betkraft/odds] Cached {len(match_odds)} matches for season={sid}", flush=True)
                        # Trim — keep last 10 seasons
                        if len(cached_seasons) > 10:
                            oldest = sorted(cached_seasons)[0]
                            cached_seasons.discard(oldest)
                            odds_cache.pop(oldest, None)

            # 2. Fetch results — look up odds via season_id
            rdata = fetch(BK_RESULTS, BK_H)
            if not rdata: time.sleep(10); continue

            rounds = rdata.get('data',{}).get('results',[])
            records=[]
            for rnd in rounds:
                round_id = str(rnd.get('round_id',''))
                sid      = str(rnd.get('season_id',''))
                if not round_id: continue
                cached = odds_cache.get(sid, {})

                for i,m in enumerate(rnd.get('matches',[])):
                    home=(m.get('home') or '').strip()
                    away=(m.get('away') or '').strip()
                    if not home or not away: continue
                    fth,fta=bk_parse_score(m.get('result',''))
                    if fth is None: continue
                    key=(round_id,home,away)
                    if key in seen: continue
                    seen.add(key)

                    hth,hta=bk_parse_score(m.get('half_time_scores',''))
                    match_odd = next((v for v in cached.values()
                                      if v.get('home')==home and v.get('away')==away), {})
                    mkts_dict = match_odd.get('markets',{})
                    x12  = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('1X2',{}).get('outcomes',[])} if '1X2' in mkts_dict else {}
                    ou25 = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('TG25',{}).get('outcomes',[])} if 'TG25' in mkts_dict else {}
                    btts = {o['outcome_id']:float(o['odd_value']) for o in mkts_dict.get('GG',{}).get('outcomes',[])} if 'GG' in mkts_dict else {}
                    all_mkts = [{'id':k,'outcomes':v.get('outcomes',[])} for k,v in mkts_dict.items()]

                    has_odds='✓' if x12 else '✗'
                    records.append((
                        round_id,i+1,home,away,fth,fta,hth,hta,
                        json.dumps(all_mkts),
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

        time.sleep(10)  # 10s between result checks


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
