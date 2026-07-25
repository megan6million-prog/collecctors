"""Bankroll & stake management."""
import json, os
from datetime import datetime, timedelta

STATE_FILE = os.path.expanduser("~/.vfl_betting_state.json")

_default_state = {
    "bankroll": {},
    "daily_stats": {},
    "current_session": {"started": None, "bets": 0, "won": 0, "lost": 0, "profit": 0}
}

def _load():
    if not os.path.exists(STATE_FILE):
        return dict(_default_state)
    with open(STATE_FILE) as f:
        return json.load(f)

def _save(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def update_balance(source, amount):
    state = _load()
    state["bankroll"][source] = amount
    _save(state)

def get_balance(source):
    state = _load()
    return state["bankroll"].get(source, 0)

def calculate_stake(balance, confidence, odds):
    """Kelly Criterion: stake = balance * (confidence * odds - 1) / (odds - 1)"""
    if odds <= 1:
        return 500
    edge = confidence * odds - 1
    if edge <= 0:
        return 0
    stake = balance * edge / (odds - 1)
    return min(max(int(stake), 500), int(balance * 0.10))
