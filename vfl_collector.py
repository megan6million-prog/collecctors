#!/usr/bin/env python3
"""
vfl_collector.py — Clean VFL data collector
============================================
Collects betpawa + betkraft results and saves to Postgres.
No learners, no miners — just raw data.

Strategy (betpawa):
  - Poll v2/seasons/list/actual every 4s to find round list
  - For each round: poll v3/events/list/by-round/{id}
  - Cache odds when markets are present (pre-match window)
  - Save when FULL_TIME_EXCLUDING_OVERTIME result appears

Run:
  DATABASE_URL=postgresql://... python3 vfl_collector.py
"""

import os, json, time, threading, requests, psycopg2
from datetime import datetime
from psycopg2.extras import execute_values

DATABASE_URL           = os.environ.get('DATABASE_URL', '')
POLL_BETPAWA_SECONDS   = 4
POLL_BETKRAFT_SECONDS  = 5


# ── DB ─────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_schema():
    ddl = [
        """CREATE TABLE IF NOT EXISTS betpawa_rounds (
            id           SERIAL PRIMARY KEY,
            round_id     TEXT NOT NULL,
            league       TEXT NOT NULL,
            league_id    TEXT,
            home         TEXT NOT NULL,
            away         TEXT NOT NULL,
            ft_h         INT,
            ft_a         INT,
            ht_h         INT,
            ht_a         INT,
            htft_outcome TEXT,
            odds_1       FLOAT, odds_x FLOAT, odds_2 FLOAT,
            ou_15_over   FLOAT, ou_15_under FLOAT,
            ou_25_over   FLOAT, ou_25_under FLOAT,
            ou_35_over   FLOAT, ou_35_under FLOAT,
            btts_yes     FLOAT, btts_no     FLOAT,
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
            home         TEXT NOT NULL,
            away         TEXT NOT NULL,
            ft_h         INT,
            ft_a         INT,
            ht_h         INT,
            ht_a         INT,
            markets      JSONB,
            odds_1       FLOAT, odds_x FLOAT, odds_2 FLOAT,
            ou_25_over   FLOAT, ou_25_under FLOAT,
            btts_yes     FLOAT,
            collected_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(round_id, home, away)
        )""",
        "CREATE INDEX IF NOT EXISTS bp_pair_idx  ON betpawa_rounds(league_id, home, away)",
        "CREATE INDEX IF NOT EXISTS bp_round_idx ON betpawa_rounds(round_id)",
        "CREATE INDEX IF NOT EXISTS bk_pair_idx  ON betkraft_rounds(home, away)",
        "CREATE INDEX IF NOT EXISTS bk_round_idx ON betkraft_rounds(round_id)",
    ]
    with get_db() as conn:
        with conn.cursor() as cur:
            for stmt in ddl:
                cur.execute(stmt)
        conn.commit()
    print("[init] Schema ready", flush=True)


# ── HTTP ────────────────────────────────────────────────────────────────
def fetch(url, headers, method='GET', payload=None, timeout=10):
    for attempt in range(4):
        try:
            if method == 'POST':
                r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            time.sleep(1 + attempt)
        except Exception:
            time.sleep(2 + attempt)
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
    '7794': 'English League', '7795': 'Spanish League',
    '7796': 'Italian League', '9183': 'French League',
    '9184': 'German League',  '13773': 'Portuguese League',
    '13774': 'Dutch League',
}


def bp_scores(ppr):
    """Extract HT (FIRST_HALF) and FT (FULL_TIME_EXCLUDING_OVERTIME)."""
    ht = {}; ft = {}
    for p in ppr:
        pt = p['participant']['type']
        for pr in p.get('periodResults', []):
            slug = pr['period']['slug']
            val  = int(pr['result'])
            if slug in ('FIRST_HALF', 'HALF_TIME'):
                ht[pt] = val
            elif slug == 'FULL_TIME_EXCLUDING_OVERTIME':
                ft[pt] = val
    return ht.get('HOME'), ht.get('AWAY'), ft.get('HOME'), ft.get('AWAY')


def bp_htft(ht_h, ht_a, ft_h, ft_a):
    if None in (ht_h, ht_a, ft_h, ft_a):
        return None
    hr = '1' if ht_h > ht_a else ('2' if ht_a > ht_h else 'X')
    fr = '1' if ft_h > ft_a else ('2' if ft_a > ft_h else 'X')
    return f"{hr}/{fr}"


def bp_markets(event):
    m = {'1x2': {}, 'ou': [], 'btts': {}, 'htft': {}}
    for mk in event.get('markets', []):
        name = mk.get('marketType', {}).get('name', '')
        rows = mk.get('row', [])
        if name == '1X2 - FT' and rows:
            for p in rows[0].get('prices', []):
                m['1x2'][p['name']] = float(p['price'])
        elif name == 'Both Teams To Score - FT' and rows:
            for p in rows[0].get('prices', []):
                m['btts'][p['name'].lower()] = float(p['price'])
        elif name == 'Total Score Over/Under - FT':
            for row in rows:
                line = {}
                for p in row.get('prices', []):
                    line[p['name'].lower()] = float(p['price'])
                m['ou'].append(line)
        elif name == 'HT / FT':
            for row in rows:
                for p in row.get('prices', []):
                    m['htft'][p['name']] = float(p['price'])
    return m


def bp_save(records):
    if not records:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO betpawa_rounds
                  (round_id, league, league_id, home, away,
                   ft_h, ft_a, ht_h, ht_a, htft_outcome,
                   odds_1, odds_x, odds_2,
                   ou_15_over, ou_15_under, ou_25_over, ou_25_under,
                   ou_35_over, ou_35_under, btts_yes, btts_no,
                   htft_11, htft_1x, htft_12,
                   htft_x1, htft_xx, htft_x2,
                   htft_21, htft_2x, htft_22)
                VALUES %s
                ON CONFLICT (round_id, league_id, home, away) DO NOTHING
            """, records)
        conn.commit()


def bp_collect():
    seen      = set()   # (round_id, league_id, home, away)
    mkt_cache = {}      # event_id -> markets
    saved     = 0

    print("[betpawa] Collector started", flush=True)

    while True:
        try:
            data = fetch(BP_SEASONS, BP_H)
            if not data:
                time.sleep(POLL_BETPAWA_SECONDS); continue

            for season in data.get('items', []):
                for rnd in season.get('rounds', []):
                    round_id = rnd['id']
                    edata    = fetch(BP_EVENTS.format(round_id=round_id), BP_H)
                    if not edata:
                        continue

                    records = []
                    for e in edata.get('responses', []):
                        eid  = e['id']
                        name = e.get('name', '')
                        if ' - ' not in name:
                            continue
                        home, away = [x.strip() for x in name.split(' - ', 1)]

                        comp  = e.get('competition', {})
                        lid   = str(comp.get('id', ''))
                        lname = BP_LEAGUES.get(lid, comp.get('name', ''))
                        if not lname:
                            continue

                        # Cache odds during pre-match window
                        mkts = bp_markets(e)
                        if mkts['1x2'] or mkts['htft']:
                            mkt_cache[eid] = mkts

                        key = (round_id, lid, home, away)
                        if key in seen:
                            continue

                        ppr = e.get('results', {}).get('participantPeriodResults', [])
                        if not ppr:
                            continue
                        ht_h, ht_a, ft_h, ft_a = bp_scores(ppr)
                        if ft_h is None:
                            continue

                        seen.add(key)
                        m    = mkt_cache.get(eid, mkts)
                        x12  = m.get('1x2', {})
                        ou   = m.get('ou', [])
                        btts = m.get('btts', {})
                        htft = m.get('htft', {})

                        outcome = bp_htft(ht_h, ht_a, ft_h, ft_a)
                        records.append((
                            round_id, lname, lid, home, away,
                            ft_h, ft_a, ht_h, ht_a, outcome,
                            x12.get('1'),  x12.get('X'),  x12.get('2'),
                            ou[0].get('over')  if len(ou)>0 else None,
                            ou[0].get('under') if len(ou)>0 else None,
                            ou[1].get('over')  if len(ou)>1 else None,
                            ou[1].get('under') if len(ou)>1 else None,
                            ou[2].get('over')  if len(ou)>2 else None,
                            ou[2].get('under') if len(ou)>2 else None,
                            btts.get('yes'), btts.get('no'),
                            htft.get('1/1'), htft.get('1/X'), htft.get('1/2'),
                            htft.get('X/1'), htft.get('X/X'), htft.get('X/2'),
                            htft.get('2/1'), htft.get('2/X'), htft.get('2/2'),
                        ))
                        print(f"[betpawa] [{lname}] rnd={round_id} "
                              f"{home} v {away} HT={ht_h}:{ht_a} "
                              f"FT={ft_h}:{ft_a} HTFT={outcome}", flush=True)

                    if records:
                        bp_save(records)
                        saved += len(records)
                        print(f"[betpawa] +{len(records)} saved "
                              f"(total {saved})", flush=True)

        except Exception as e:
            print(f"[betpawa] Error: {e}", flush=True)

        time.sleep(POLL_BETPAWA_SECONDS)


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
BK_RESULTS = f'{BK_BASE}/results/1/0'   # competition_id=1 (English League)


def bk_parse_score(score_str):
    """Parse '3:0' -> (3, 0). Returns (None, None) on failure."""
    try:
        parts = score_str.split(':')
        return int(parts[0]), int(parts[1])
    except Exception:
        return None, None


def bk_save(records):
    if not records:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO betkraft_rounds
                  (round_id, match_n, home, away,
                   ft_h, ft_a, ht_h, ht_a,
                   markets, odds_1, odds_x, odds_2,
                   ou_25_over, ou_25_under, btts_yes)
                VALUES %s
                ON CONFLICT (round_id, home, away) DO NOTHING
            """, records)
        conn.commit()


def bk_collect():
    seen  = set()   # (round_id, home, away)
    saved = 0

    print("[betkraft] Collector started", flush=True)

    while True:
        try:
            rdata = fetch(BK_RESULTS, BK_H)
            if not rdata:
                time.sleep(POLL_BETKRAFT_SECONDS); continue

            rounds = rdata.get('data', {}).get('results', [])

            records = []
            for rnd in rounds:
                round_id = str(rnd.get('round_id', ''))
                if not round_id:
                    continue

                for i, m in enumerate(rnd.get('matches', [])):
                    home = (m.get('home') or m.get('home_team') or '').strip()
                    away = (m.get('away') or m.get('away_team') or '').strip()
                    if not home or not away:
                        continue

                    ft_h, ft_a = bk_parse_score(m.get('result', ''))
                    if ft_h is None:
                        continue

                    key = (round_id, home, away)
                    if key in seen:
                        continue
                    seen.add(key)

                    ht_h, ht_a = bk_parse_score(m.get('half_time_scores', ''))

                    records.append((
                        round_id, i+1, home, away,
                        ft_h, ft_a, ht_h, ht_a,
                        json.dumps({}),   # odds not available in results endpoint
                        None, None, None,  # 1X2 odds
                        None, None, None,  # OU25, btts
                    ))
                    print(f"[betkraft] rnd={round_id} {home} v {away} "
                          f"HT={ht_h}:{ht_a} FT={ft_h}:{ft_a}", flush=True)

            if records:
                bk_save(records)
                saved += len(records)
                print(f"[betkraft] +{len(records)} saved "
                      f"(total {saved})", flush=True)

        except Exception as e:
            print(f"[betkraft] Error: {e}", flush=True)

        time.sleep(POLL_BETKRAFT_SECONDS)


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
    print("[main] Both collectors running. Ctrl+C to stop.", flush=True)

    try:
        while True:
            time.sleep(60)
            bp_ok = t_bp.is_alive()
            bk_ok = t_bk.is_alive()
            print(f"[health] betpawa={'OK' if bp_ok else 'DEAD'} "
                  f"betkraft={'OK' if bk_ok else 'DEAD'}", flush=True)
            if not bp_ok:
                t_bp = threading.Thread(target=bp_collect, name='betpawa', daemon=True)
                t_bp.start()
                print("[health] betpawa restarted", flush=True)
            if not bk_ok:
                t_bk = threading.Thread(target=bk_collect, name='betkraft', daemon=True)
                t_bk.start()
                print("[health] betkraft restarted", flush=True)
    except KeyboardInterrupt:
        print("\n[main] Stopped.", flush=True)
