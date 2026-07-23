# VFL Collector

Collects betpawa + betkraft VFL match data into Postgres.

## Markets captured

**Betpawa:** 1X2, BTTS, OU (1.5/2.5/3.5), HTFT (all 9), Double Chance

**Betkraft:** All 23 markets — 1X2, GG, TG15/25/35, DC, H1X2, DCH, HS,
1X2G, 1X2OU15/25/35, CS, DR (HT/FT), TG, TGOE, MG, FTS, TFG, T1G, T2G, HGG

## Setup

Set environment variable:
```
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

## Run locally
```
DATABASE_URL=... python vfl_collector.py
```

## Railway deployment
- Service type: Worker
- Add DATABASE_URL in Variables tab
