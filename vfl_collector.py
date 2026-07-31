#!/usr/bin/env python3
"""
vfl_collector.py — VFL Data Collector (Railway)
================================================
Betpawa:  EC2 logic — waits for round window, caches odds, polls results
Betkraft: EC2 logic — gets periods, fetches odds via /data, saves results

Both write to Railway Postgres via pg8000.
"""

import os, json, time, threading, requests, pg8000
from datetime import datetime, timezone
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

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

def db_exec(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        conn.commit()
    finally:
        conn.close()

def init_schema():
    stmts = [
        """CREATE TABLE IF NOT EXISTS betpawa_rounds (
            id SERIAL PRIMARY KEY,
            round_id TEXT NOT NULL, league TEXT NOT NULL, league_id TEXT,
            home TEXT NOT NULL, away TEXT NOT NULL,
            ft_h INT, ft_a INT, ht_h INT, ht_a INT, htft_outcome TEXT,
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
            id SERIAL PRIMARY KEY,
            round_id TEXT NOT NULL, season_id TEXT,
            home TEXT NOT NULL, away TEXT NOT NULL,
            ft_h INT, ft_a INT, ht_h INT, ht_a INT,
            markets TEXT,
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
        for s in stmts: cur.execute(s)
        conn.commit()
        print("[init] Schema ready", flush=True)
    finally:
        conn.close()

def bulk_insert(table, cols, rows):
    if not rows: return
    conn = get_db()
    try:
        cur  = conn.cursor()
        n    = len(cols)
        ph   = '(' + ','.join(['%s']*n) + ')'
        sql  = (f"INSERT INTO {table} ({','.join(cols)}) VALUES "
                + ','.join([ph]*len(rows))
                + " ON CONFLICT DO NOTHING")
        flat = [v for row in rows for v in row]
        cur.execute(sql, flat)
        conn.commit()
    finally:
        conn.close()


# ── HTTP ────────────────────────────────────────────────────────────────
def fetch(url, headers, payload=None, timeout=12):
    for i in range(4):
        try:
            r = (requests.post(url, json=payload, headers=headers, timeout=timeout)
                 if payload is not None
                 else requests.get(url, headers=headers, timeout=timeout))
            if r.status_code == 200: return r.json()
            if r.status_code == 429:
                print(f"[ratelimit] sleeping 10s", flush=True)
                time.sleep(10)
        except Exception: pass
        time.sleep(2 + i*2)
    return None


# ══════════════════════════════════════════════════════════════════════
#  BETPAWA  (EC2 logic + v2/v3 endpoints + pg8000)
# ══════════════════════════════════════════════════════════════════════
BP_H = {
    'x-pawa-brand': 'betpawa-uganda', 'x-pawa-language': 'en',
    'devicetype': 'web',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}
BP_SEASONS = 'https://www.betpawa.ug/api/sportsbook/virtual/v2/seasons/list/actual'
BP_EVENTS  = 'https://www.betpawa.ug/api/sportsbook/virtual/v3/events/list/by-round/{rid}'
BP_LEAGUES = {
    '7794':'English League','7795':'Spanish League','7796':'Italian League',
    '9183':'French League','9184':'German League',
    '13773':'Portuguese League','13774':'Dutch League',
}

def bp_scores(ppr):
    ht={}; ft={}
    for p in ppr:
        pt = p['participant']['type']
        for pr in p.get('periodResults',[]):
            s=pr['period']['slug']; v=int(pr['result'])
            if s in ('FIRST_HALF','HALF_TIME'): ht[pt]=v
            elif s=='FULL_TIME_EXCLUDING_OVERTIME': ft[pt]=v
    return ht.get('HOME'), ht.get('AWAY'), ft.get('HOME'), ft.get('AWAY')

def bp_is_finished(ppr):
    """True only if FT score is real (non-zero or minute>=90)."""
    ft={}
    for p in ppr:
        pt=p['participant']['type']
        for pr in p.get('periodResults',[]):
            if pr['period']['slug']=='FULL_TIME_EXCLUDING_OVERTIME':
                ft[pt]=int(pr['result'])
    if ft.get('HOME') is None: return False
    if ft.get('HOME',0)>0 or ft.get('AWAY',0)>0: return True
    return False

def bp_htft(hth,hta,fth,fta):
    if None in (hth,hta,fth,fta): return None
    hr='1' if hth>hta else('2' if hta>hth else 'X')
    fr='1' if fth>fta else('2' if fta>fth else 'X')
    return f"{hr}/{fr}"

def bp_markets(e):
    m={'1x2':{},'ou':[],'btts':{},'htft':{}}
    for mk in e.get('markets',[]):
        name=mk.get('marketType',{}).get('name','')
        rows=mk.get('row',[])
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
    cols = ['round_id','league','league_id','home','away',
            'ft_h','ft_a','ht_h','ht_a','htft_outcome',
            'odds_1','odds_x','odds_2',
            'ou_15_over','ou_15_under','ou_25_over','ou_25_under',
            'ou_35_over','ou_35_under','btts_yes','btts_no',
            'htft_11','htft_1x','htft_12','htft_x1','htft_xx','htft_x2',
            'htft_21','htft_2x','htft_22']
    bulk_insert('betpawa_rounds', cols, records)

def bp_collect():
    seen   = set()
    saved  = 0
    print("[betpawa] Collector started", flush=True)

    while True:
        try:
            data = fetch(BP_SEASONS, BP_H)
            if not data: time.sleep(5); continue

            now  = datetime.now(timezone.utc)

            # Collect ALL round IDs in current seasons
            current_rids = set()
            for s in data.get('items',[]):
                for rnd in s.get('rounds',[]):
                    current_rids.add(rnd['id'])
            # Prune seen: remove rounds no longer in API response
            seen &= current_rids

            best = None
            for s in data.get('items',[]):
                for rnd in s.get('rounds',[]):
                    rid = rnd['id']
                    if rid in seen: continue
                    t     = rnd.get('tradingTime',{})
                    start = datetime.fromisoformat(t['start'].replace('Z','+00:00'))
                    end   = datetime.fromisoformat(t['end'].replace('Z','+00:00'))
                    # Accept: upcoming, currently active, or recently finished (30 min)
                    if end < now and (now-end).total_seconds() > 1800: continue
                    dist = abs((start - now).total_seconds())
                    if best is None or dist < best[3]:
                        best = (rid, start, end, dist)

            if not best:
                time.sleep(5); continue

            rid, start, end, _ = best

            # Wait for trading window
            if start > now:
                wait = (start - now).total_seconds()
                if wait > 3:
                    print(f"[betpawa] Round {rid} at {start.strftime('%H:%M:%S')} — waiting {wait:.0f}s", flush=True)
                    time.sleep(wait)

            # ── Capture odds during trading window ──────────────────
            mkt_cache = {}
            league_cache = {}
            print(f"[betpawa] Round {rid} — capturing odds...", flush=True)
            for attempt in range(15):
                ed = fetch(BP_EVENTS.format(rid=rid), BP_H)
                if ed:
                    for e in ed.get('responses',[]):
                        comp = e.get('competition',{})
                        lid  = str(comp.get('id',''))
                        if lid not in BP_LEAGUES: continue
                        mkts = bp_markets(e)
                        if mkts['1x2'] or mkts['htft']:
                            mkt_cache[e['id']] = mkts
                            league_cache[e['id']] = {'id':lid,'name':BP_LEAGUES[lid]}
                    if mkt_cache:
                        print(f"[betpawa] Cached odds for {len(mkt_cache)} events", flush=True)
                        break
                time.sleep(2)

            # ── Poll for results ────────────────────────────────────
            seen_results = set()
            poll_end     = time.time() + 360  # 6 min max
            records      = []
            print(f"[betpawa] Polling results for round {rid}...", flush=True)

            while time.time() < poll_end:
                ed = fetch(BP_EVENTS.format(rid=rid), BP_H)
                if not ed: time.sleep(3); continue

                for e in ed.get('responses',[]):
                    if e['id'] in seen_results: continue
                    comp = e.get('competition',{}); lid=str(comp.get('id',''))
                    lname = BP_LEAGUES.get(lid, league_cache.get(e['id'],{}).get('name',''))
                    if not lname: continue

                    ppr = e.get('results',{}).get('participantPeriodResults',[])
                    if not ppr or not bp_is_finished(ppr): continue

                    hth,hta,fth,fta = bp_scores(ppr)
                    if fth is None: continue
                    # Validate: FT >= HT
                    if hth is not None and (fth<hth or fta<hta): continue

                    seen_results.add(e['id'])
                    name = e.get('name',''); parts=name.split(' - ')
                    if len(parts)!=2: continue
                    home,away = parts[0].strip(), parts[1].strip()

                    m    = mkt_cache.get(e['id'],{})
                    x12  = m.get('1x2',{}); ou=m.get('ou',[]); btts=m.get('btts',{}); htft=m.get('htft',{})
                    has  = '✓' if x12.get('1') else '✗'
                    print(f"[betpawa] [{lname}] {home} v {away} HT={hth}:{hta} FT={fth}:{fta} HTFT={bp_htft(hth,hta,fth,fta)} odds={has}", flush=True)

                    records.append((
                        rid, lname, lid, home, away,
                        fth, fta, hth, hta, bp_htft(hth,hta,fth,fta),
                        x12.get('1'),x12.get('X'),x12.get('2'),
                        ou[0].get('over')  if len(ou)>0 else None,
                        ou[0].get('under') if len(ou)>0 else None,
                        ou[1].get('over')  if len(ou)>1 else None,
                        ou[1].get('under') if len(ou)>1 else None,
                        ou[2].get('over')  if len(ou)>2 else None,
                        ou[2].get('under') if len(ou)>2 else None,
                        btts.get('yes'), btts.get('no'),
                        htft.get('1/1'),htft.get('1/X'),htft.get('1/2'),
                        htft.get('X/1'),htft.get('X/X'),htft.get('X/2'),
                        htft.get('2/1'),htft.get('2/X'),htft.get('2/2'),
                    ))

                # Check completion
                total_events = len([e for e in ed.get('responses',[])
                                     if BP_LEAGUES.get(str(e.get('competition',{}).get('id','')))])
                if len(seen_results) >= total_events and total_events > 0:
                    print(f"[betpawa] ✅ Round {rid} complete — {len(seen_results)} events", flush=True)
                    break
                time.sleep(3)

            # Save
            if records:
                avg_tg = sum(r[5]+r[6] for r in records)/len(records)
                if avg_tg < 2.0:
                    print(f"[betpawa] SKIP round {rid} — avg_tg={avg_tg:.2f} (rate-limited)", flush=True)
                else:
                    bp_save(records)
                    saved += len(records)
                    print(f"[betpawa] +{len(records)} saved avg_tg={avg_tg:.2f} (total {saved})", flush=True)

            seen.add(rid)

        except Exception as e:
            import traceback
            print(f"[betpawa] Error: {e}\n{traceback.format_exc()}", flush=True)
            time.sleep(10)


# ══════════════════════════════════════════════════════════════════════
#  BETKRAFT  (EC2 logic + pg8000)
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
BK_LIVE    = f'{BK_BASE}/live'
BK_DATA    = f'{BK_BASE}/data'
BK_RESULTS = f'{BK_BASE}/results/1/0'

BK_MARKETS = ['1X2','GG','TG15','TG25','DC','TG35','H1X2','DCH','HS',
              '1X2G','1X2OU15','1X2OU25','1X2OU35','CS','DR','TG','TGOE',
              'MG','FTS','TFG','T1G','T2G','HGG']

def bk_fetch_odds(rn_id):
    """Fetch all markets for one round sequentially."""
    match_odds = {}
    for mkt in BK_MARKETS:
        d = fetch(BK_DATA, BK_H, payload={
            'round_number_id': rn_id, 'competition_id': 1,
            'country_id': None, 'market_id': mkt
        })
        if d and d.get('status_code')==200:
            for m in d.get('data',{}).get('matches',[]):
                eid = m['event_id']
                if eid not in match_odds:
                    match_odds[eid] = {'home':m['home'],'away':m['away'],'mkts':{}}
                for mk in m.get('markets',[]):
                    if mk.get('market_id')==mkt:
                        match_odds[eid]['mkts'][mkt] = mk
        time.sleep(0.3)
    return match_odds

def bk_parse(s):
    try: p=s.split(':'); return int(p[0]),int(p[1])
    except: return None,None

def bk_save(records):
    cols = ['round_id','season_id','home','away','ft_h','ft_a','ht_h','ht_a',
            'markets','odds_1','odds_x','odds_2','ou_25_over','ou_25_under','btts_yes']
    bulk_insert('betkraft_rounds', cols, records)

def bk_collect():
    seen          = set()
    odds_cache    = {}   # season_id -> {event_id -> {home,away,mkts}}
    cached_sids   = set()
    saved         = 0
    print("[betkraft] Collector started", flush=True)

    while True:
        try:
            # Cache odds for upcoming periods (keyed by season_id)
            pdata = fetch(BK_PERIODS, BK_H)
            if pdata:
                for period in pdata.get('data',{}).get('periods',[])[-3:]:
                    rn_id = period.get('round_number_id')
                    sid   = str(period.get('season_id',''))
                    if rn_id and sid and sid not in cached_sids:
                        print(f"[betkraft/odds] Fetching rn={rn_id} season={sid}...", flush=True)
                        mo = bk_fetch_odds(rn_id)
                        if mo:
                            odds_cache[sid] = mo
                            cached_sids.add(sid)
                            print(f"[betkraft/odds] Cached {len(mo)} matches", flush=True)
                        if len(cached_sids) > 15:
                            oldest = sorted(cached_sids)[0]
                            cached_sids.discard(oldest)
                            odds_cache.pop(oldest, None)

            # Fetch results
            rdata = fetch(BK_RESULTS, BK_H)
            if not rdata: time.sleep(10); continue

            records = []
            for rnd in rdata.get('data',{}).get('results',[]):
                round_id = str(rnd.get('round_id',''))
                sid      = str(rnd.get('season_id',''))
                if not round_id: continue
                cached = odds_cache.get(sid, {})

                for m in rnd.get('matches',[]):
                    home=(m.get('home') or '').strip()
                    away=(m.get('away') or '').strip()
                    if not home or not away: continue
                    fth,fta=bk_parse(m.get('result',''))
                    if fth is None: continue
                    key=(round_id,home,away)
                    if key in seen: continue
                    seen.add(key)

                    hth,hta=bk_parse(m.get('half_time_scores',''))
                    mo    = next((v for v in cached.values() if v.get('home')==home and v.get('away')==away),{})
                    mkts  = mo.get('mkts',{})
                    x12   = {o['outcome_id']:float(o['odd_value']) for o in mkts.get('1X2',{}).get('outcomes',[])} if '1X2' in mkts else {}
                    ou25  = {o['outcome_id']:float(o['odd_value']) for o in mkts.get('TG25',{}).get('outcomes',[])} if 'TG25' in mkts else {}
                    btts  = {o['outcome_id']:float(o['odd_value']) for o in mkts.get('GG',{}).get('outcomes',[])} if 'GG' in mkts else {}
                    all_m = [{'id':k,'outcomes':v.get('outcomes',[])} for k,v in mkts.items()]
                    has   = '✓' if x12 else '✗'

                    records.append((
                        round_id, sid, home, away, fth, fta, hth, hta,
                        json.dumps(all_m),
                        x12.get('1'),x12.get('X'),x12.get('2'),
                        ou25.get('O'),ou25.get('U'),
                        btts.get('Y'),
                    ))
                    print(f"[betkraft] rnd={round_id} {home} v {away} HT={hth}:{hta} FT={fth}:{fta} odds={has}", flush=True)

            if records:
                bk_save(records)
                saved += len(records)
                print(f"[betkraft] +{len(records)} saved (total {saved})", flush=True)

        except Exception as e:
            print(f"[betkraft] Error: {e}", flush=True)
        time.sleep(10)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    if not DATABASE_URL:
        print("[error] DATABASE_URL not set"); exit(1)
    try:
        init_schema()
    except Exception as e:
        print(f"[init] {e} — continuing", flush=True)

    t_bp = threading.Thread(target=bp_collect, name='betpawa',  daemon=True)
    t_bk = threading.Thread(target=bk_collect, name='betkraft', daemon=True)
    t_bp.start(); t_bk.start()
    print("[main] Both collectors running.", flush=True)

    try:
        while True:
            time.sleep(60)
            bp_ok=t_bp.is_alive(); bk_ok=t_bk.is_alive()
            print(f"[health] betpawa={'OK' if bp_ok else 'DEAD'} betkraft={'OK' if bk_ok else 'DEAD'}", flush=True)
            if not bp_ok:
                t_bp=threading.Thread(target=bp_collect,name='betpawa',daemon=True); t_bp.start()
            if not bk_ok:
                t_bk=threading.Thread(target=bk_collect,name='betkraft',daemon=True); t_bk.start()
    except KeyboardInterrupt:
        print("\n[main] Stopped.")
