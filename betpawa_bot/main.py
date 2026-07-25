#!/usr/bin/env python3
"""
Betpawa HTFT Bot — Railway entrypoint
Wraps run_betpawa_brain.py in HTFT live mode.

Env vars:
  BP_PHONE    betpawa phone (default: 0705949189)
  BP_PIN      betpawa PIN   (default: 4413)
  STAKE_UGX   stake per bet (default: 1)
  DRY_RUN     true/false    (default: false)
"""
import os, sys, asyncio

os.environ.setdefault('BRAIN_MODE', 'htft')
os.environ.setdefault('STAKE_UGX',  os.environ.get('STAKE_UGX', '1'))
os.environ.setdefault('DRY_RUN',    'false')
os.environ.setdefault('HEADLESS',   'true')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_betpawa_brain import run
asyncio.run(run(live=True))
