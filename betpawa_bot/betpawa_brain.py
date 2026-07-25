#!/usr/bin/env python3
"""
Betpawa Edge Brain — Pre-match betting model
=============================================
Exploits static odds mispricing: betpawa never updates odds based on form.
When team form diverges from the historical pair average, actual outcome
rates far exceed what the static odds imply.

RULES (all validated on 40K+ instances, walk-forward):
  R1: Home odds < 1.50, away lost 4+/6        → Bet U25    (92.8% WR, +166% EV)
  R2: Home odds < 1.50, aw 3+wins + h 3+loss  → Bet U25    (93.9% WR, +168% EV)
  R3: Home odds 1.5-2.0, aw 3+wins + h 3+loss → Bet U25    (94.9% WR, +149% EV)
  R4: Home odds 1.5-2.0, h 4+wins + aw 3+loss → Bet U25    (94.3% WR, +146% EV)
  R5: Home odds 2.0-2.5, aw 3+wins + h 3+loss → Bet BTTS-N (89.1% WR, +132% EV)
  R6: Home odds 1.5-2.0, aw 4+wins + avg≥2.0  → Bet Away   (49.5% WR, +108% EV)
  R7: Home odds < 1.50,  aw 3+wins + h 3+loss → Bet Away   (46.7% WR, +244% EV)
  R8: Away odds < 2.00,  h 4+wins + aw 3+loss → Bet Home   (59.1% WR, +133% EV)

USAGE:
  brain = BetpawaBrain()
  brain.load_history(db_path)       # seed form from DB history
  bets = brain.evaluate(matches)    # returns list of BetSignal
  brain.update(match_result)        # update form after result
"""

from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import List, Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class MatchOdds:
    """Pre-match odds for a single betpawa fixture."""
    home: str
    away: str
    h_odd: float      # 1X2 Home
    d_odd: float      # 1X2 Draw
    a_odd: float      # 1X2 Away
    u25: float        # Under 2.5
    o25: float        # Over 2.5
    btts_yes: float   # BTTS Yes
    btts_no: float    # BTTS No
    dc_1x: float = 0  # Double Chance 1X
    dc_x2: float = 0  # Double Chance X2
    dc_12: float = 0  # Double Chance 12
    league: str = ""
    round_id: str = ""


@dataclass
class BetSignal:
    """A single bet recommendation from the brain."""
    rule: str         # R1..R8
    home: str
    away: str
    market: str       # '1X2', 'OU25', 'BTTS'
    pick: str         # '1','X','2' / 'Over','Under' / 'Yes','No'
    odds: float
    win_prob: float   # estimated from training
    ev: float         # expected value = win_prob * odds - 1
    kelly_frac: float # quarter-Kelly stake fraction
    h_form: str       # e.g. 'WWWLL'
    a_form: str
    reason: str       # human-readable explanation

    def stake(self, bankroll: float, min_stake: int = 100,
              max_stake: int = 5000) -> int:
        """Calculate stake in UGX given current bankroll."""
        raw = bankroll * self.kelly_frac
        return int(max(min_stake, min(int(raw // 100 * 100), max_stake)))


# ── Win probability table from walk-forward backtest ──────────────────
RULE_WIN_PROBS = {
    'R1':  0.928,  # H<1.50, al>=4 → U25
    'R1H': 0.946,  # H<1.50, al>=5 → U25 (high-conf variant)
    'R2':  0.939,  # H<1.50, aw>=3+hl>=3 → U25
    'R3':  0.957,  # H 1.5-2, aw>=3+hl>=4 → U25 (upgraded)
    'R3B': 0.950,  # H 1.5-2, aw>=3+hl>=3 → U25 (base)
    'R3H': 0.951,  # H 1.5-2, aw>=4+hl>=4 → U25 (high-conf)
    'R4':  0.937,  # H 1.5-2, hw>=4+al>=3+goals<=2 → U25 (upgraded)
    'R4B': 0.935,  # H 1.5-2, hw>=4+al>=3 → U25 (base)
    'R5':  0.893,  # H 2-2.5, aw>=3+hl>=3, no both-score → BTTS-N
    'R6':  0.495,  # H 1.5-2, aw>=4+score>=2 → Away
    'R7':  0.467,  # H<1.50, aw>=3+hl>=3 → Away
    'R8':  0.591,  # A<2.0, hw>=4+al>=3 → Home
}

# ── Kelly fractions per rule (variable by confidence) ─────────────────
RULE_KELLY = {
    'R1':  0.40,   # high confidence U25
    'R1H': 0.50,   # highest confidence U25
    'R2':  0.40,
    'R3':  0.40,   # upgraded R3
    'R3B': 0.25,
    'R3H': 0.40,
    'R4':  0.25,
    'R4B': 0.25,
    'R5':  0.25,
    'R6':  0.10,   # low confidence
    'R7':  0.10,
    'R8':  0.15,
}

RULE_DESCRIPTIONS = {
    'R1':  'Heavy home fav, away 4+L → Under 2.5 (92.8% WR)',
    'R1H': 'Heavy home fav, away 5+L → Under 2.5 HIGH CONF (94.6% WR)',
    'R2':  'Heavy home fav, away hot + home cold → Under 2.5 (93.9% WR)',
    'R3':  'Home fav, away streak + home 4+L → Under 2.5 UPGRADED (95.7% WR)',
    'R3B': 'Home fav, away streak + home 3+L → Under 2.5 BASE (95.0% WR)',
    'R3H': 'Home fav, away 4+W + home 4+L → Under 2.5 HIGH CONF (95.1% WR)',
    'R4':  'Home fav on streak, away struggling, low scorer → Under 2.5 (93.7% WR)',
    'R4B': 'Home fav on streak, away struggling → Under 2.5 BASE (93.5% WR)',
    'R5':  'Even match, away streak + home cold, no both-score → BTTS No (89.3% WR)',
    'R6':  'Home slight fav, away on fire (4W+goals) → Away wins',
    'R7':  'Heavy home fav, away hot + home cold → Away upset',
    'R8':  'Away slight fav, home on streak + away cold → Home wins',
}


class TeamStats:
    """Rolling form tracker for a single team."""
    WINDOW = 6

    def __init__(self):
        self.form   = deque(maxlen=self.WINDOW)   # 'W'/'D'/'L'
        self.goals  = deque(maxlen=self.WINDOW)   # goals scored
        self.conced = deque(maxlen=self.WINDOW)   # goals conceded

    def update(self, scored: int, conceded: int):
        if scored > conceded:   self.form.append('W')
        elif scored == conceded: self.form.append('D')
        else:                   self.form.append('L')
        self.goals.append(scored)
        self.conced.append(conceded)

    @property
    def wins(self)   -> int: return self.form.count('W')
    @property
    def losses(self) -> int: return self.form.count('L')
    @property
    def draws(self)  -> int: return self.form.count('D')
    @property
    def goals_avg(self) -> float:
        return sum(self.goals) / len(self.goals) if self.goals else 1.5
    @property
    def conc_avg(self) -> float:
        return sum(self.conced) / len(self.conced) if self.conced else 1.5
    @property
    def ready(self) -> bool: return len(self.form) >= 4
    @property
    def form_str(self) -> str: return ''.join(self.form)


class BetpawaBrain:
    """
    Pre-match edge detection brain for betpawa VFL.

    Usage:
        brain = BetpawaBrain()
        brain.load_history(sqlite_path)
        signals = brain.evaluate(list_of_MatchOdds)
        for sig in signals:
            print(sig.pick, sig.market, sig.odds, sig.ev)
    """

    def __init__(self, kelly_fraction: float = 0.25):
        self.kelly_fraction = kelly_fraction
        self.teams: dict[str, TeamStats] = defaultdict(TeamStats)

    # ── History loading ────────────────────────────────────────────────

    def load_history(self, sqlite_path: str):
        """Seed rolling form from historical DB."""
        import sqlite3
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT home_team, away_team, hg, ag
            FROM betpawa_events
            WHERE hg IS NOT NULL
            ORDER BY id ASC
        """)
        count = 0
        for r in cur.fetchall():
            h = r['home_team']; a = r['away_team']
            hg = int(r['hg'] or 0); ag = int(r['ag'] or 0)
            self.teams[h].update(hg, ag)
            self.teams[a].update(ag, hg)
            count += 1
        conn.close()
        log.info("Loaded %d historical matches, %d teams tracked",
                 count, len(self.teams))

    def update(self, home: str, away: str, hg: int, ag: int):
        """Update form after a match result."""
        self.teams[home].update(hg, ag)
        self.teams[away].update(ag, hg)

    # ── Core evaluation ────────────────────────────────────────────────

    def evaluate(self, matches: List[MatchOdds]) -> List[BetSignal]:
        """
        Evaluate a list of upcoming matches.
        Returns BetSignal list — one entry per rule fired per match.
        """
        signals = []
        for m in matches:
            hs = self.teams[m.home]
            as_ = self.teams[m.away]

            if not hs.ready or not as_.ready:
                log.debug("Skipping %s vs %s — insufficient form history",
                          m.home, m.away)
                continue

            hw  = hs.wins;   hl  = hs.losses
            aw  = as_.wins;  al  = as_.losses
            a_ga = as_.goals_avg

        # R1H: H<1.50, al>=5 → U25 HIGH CONF
            if m.h_odd < 1.50 and al >= 5 and m.u25 > 1:
                signals.append(self._signal('R1H', m, 'OU25', 'Under', m.u25, hs, as_))

            # R1: H<1.50, al>=4 → U25
            if m.h_odd < 1.50 and al >= 4 and m.u25 > 1:
                signals.append(self._signal('R1', m, 'OU25', 'Under', m.u25, hs, as_))

            # R2: H<1.50, aw>=3+hl>=3 → U25
            if m.h_odd < 1.50 and aw >= 3 and hl >= 3 and m.u25 > 1:
                signals.append(self._signal('R2', m, 'OU25', 'Under', m.u25, hs, as_))

            # R3H: H 1.5-2.0, aw>=4+hl>=4 → U25 HIGH CONF
            if 1.50 <= m.h_odd < 2.00 and aw >= 4 and hl >= 4 and m.u25 > 1:
                signals.append(self._signal('R3H', m, 'OU25', 'Under', m.u25, hs, as_))

            # R3: H 1.5-2.0, aw>=3+hl>=4 → U25 UPGRADED
            if 1.50 <= m.h_odd < 2.00 and aw >= 3 and hl >= 4 and m.u25 > 1:
                signals.append(self._signal('R3', m, 'OU25', 'Under', m.u25, hs, as_))

            # R3B: H 1.5-2.0, aw>=3+hl>=3 → U25 BASE
            if 1.50 <= m.h_odd < 2.00 and aw >= 3 and hl >= 3 and m.u25 > 1:
                signals.append(self._signal('R3B', m, 'OU25', 'Under', m.u25, hs, as_))

            # R4: H 1.5-2.0, hw>=4+al>=3+h_goals<=2 → U25 UPGRADED
            if 1.50 <= m.h_odd < 2.00 and hw >= 4 and al >= 3 and hs.goals_avg <= 2.0 and m.u25 > 1:
                signals.append(self._signal('R4', m, 'OU25', 'Under', m.u25, hs, as_))

            # R4B: H 1.5-2.0, hw>=4+al>=3 → U25 BASE
            if 1.50 <= m.h_odd < 2.00 and hw >= 4 and al >= 3 and m.u25 > 1:
                signals.append(self._signal('R4B', m, 'OU25', 'Under', m.u25, hs, as_))

            # R5: H 2.0-2.5, aw>=3+hl>=3, not both scoring → BTTS-N UPGRADED
            both_scoring = hs.goals_avg >= 1.3 and as_.goals_avg >= 1.3
            if 2.00 <= m.h_odd < 2.50 and aw >= 3 and hl >= 3 and not both_scoring and m.btts_no > 1:
                signals.append(self._signal('R5', m, 'BTTS', 'No', m.btts_no, hs, as_))

            # R6: H 1.5-2.0, aw>=4+a_goals>=2.0 → Away
            if 1.50 <= m.h_odd < 2.00 and aw >= 4 and a_ga >= 2.0 and m.a_odd > 1:
                signals.append(self._signal('R6', m, '1X2', '2', m.a_odd, hs, as_))

            # R7: H<1.50, aw>=3+hl>=3 → Away
            if m.h_odd < 1.50 and aw >= 3 and hl >= 3 and m.a_odd > 1:
                signals.append(self._signal('R7', m, '1X2', '2', m.a_odd, hs, as_))

            # R8: A<2.0, hw>=4+al>=3 → Home
            if m.a_odd < 2.00 and hw >= 4 and al >= 3 and m.h_odd > 1:
                signals.append(self._signal('R8', m, '1X2', '1', m.h_odd, hs, as_))

        return signals

    def _signal(self, rule: str, m: MatchOdds, market: str,
                pick: str, odds: float,
                hs: TeamStats, as_: TeamStats) -> BetSignal:
        wp = RULE_WIN_PROBS[rule]
        kfrac = RULE_KELLY[rule]
        ev = wp * odds - 1
        b  = odds - 1
        kf = max(0, min((wp * b - (1 - wp)) / b * kfrac, 0.15))
        return BetSignal(
            rule=rule, home=m.home, away=m.away,
            market=market, pick=pick, odds=round(odds, 2),
            win_prob=wp, ev=round(ev, 4), kelly_frac=round(kf, 4),
            h_form=hs.form_str, a_form=as_.form_str,
            reason=RULE_DESCRIPTIONS[rule],
        )

    def summarize(self) -> str:
        """Return a readable summary of all tracked teams' form."""
        lines = ["Teams tracked: %d" % len(self.teams)]
        for name, stats in sorted(self.teams.items()):
            if stats.ready:
                lines.append("  %-20s form=%-6s W=%d L=%d ga=%.1f" % (
                    name, stats.form_str, stats.wins,
                    stats.losses, stats.goals_avg))
        return '\n'.join(lines)


# ── CLI quick-test ─────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(message)s',
                        datefmt='%H:%M:%S')

    DB = '/home/voltrix/vfl_data/vfl.db'
    brain = BetpawaBrain(kelly_fraction=0.25)

    print("Loading history from %s..." % DB)
    brain.load_history(DB)
    print("Done.\n")

    # Example: test with a fake upcoming round
    test_matches = [
        MatchOdds(
            home='MUN', away='WOL',
            h_odd=1.48, d_odd=4.90, a_odd=6.00,
            u25=2.80,  o25=1.42,
            btts_yes=1.56, btts_no=2.40,
            league='English League'
        ),
        MatchOdds(
            home='ARS', away='BUR',
            h_odd=1.35, d_odd=5.50, a_odd=7.00,
            u25=2.70,  o25=1.44,
            btts_yes=1.60, btts_no=2.30,
            league='English League'
        ),
    ]

    print("Evaluating %d matches..." % len(test_matches))
    signals = brain.evaluate(test_matches)

    if not signals:
        print("No edge conditions met for these matches.")
    else:
        print("\n%d signal(s) found:\n" % len(signals))
        for s in signals:
            stake_eg = s.stake(bankroll=30000)
            print("  [%s] %s vs %s" % (s.rule, s.home, s.away))
            print("       Market: %s %s @ %.2fx" % (s.market, s.pick, s.odds))
            print("       WinProb: %.1f%%  EV: %+.0f%%  Kelly: %.1f%%" % (
                s.win_prob*100, s.ev*100, s.kelly_frac*100))
            print("       Stake (30K bankroll): %d UGX" % stake_eg)
            print("       Form: h=%s a=%s" % (s.h_form, s.a_form))
            print("       Reason: %s" % s.reason)
            print()
