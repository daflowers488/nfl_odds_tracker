# odds_pull.py — pulls DraftKings NFL odds via TheOddsAPI and saves to odds.db (SQLite)
import os, sys, json, sqlite3, datetime as dt
from pathlib import Path
from urllib.parse import urlencode
import requests
from datetime import UTC

BOOKMAKER = "draftkings"
SPORT = "americanfootball_nfl"
MARKETS = ["h2h", "spreads", "totals"]   # moneyline, spread, totals
REGIONS = "us"
ODDS_FORMAT = "american"
API_BASE = "https://api.the-odds-api.com/v4/sports"
MONTHLY_CAP = 480
DB_PATH = "odds.db"
ENV_PATH = ".env"

def load_api_key():
    key = os.environ.get("THEODDS_API_KEY")
    if not key and Path(ENV_PATH).exists():
        for line in Path(ENV_PATH).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("THEODDS_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        print("ERROR: Put THEODDS_API_KEY in .env")
        sys.exit(1)
    return key

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def setup(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS api_usage(
      yyyymm TEXT PRIMARY KEY, requests_used INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS games(
      game_id TEXT PRIMARY KEY, commence_time TEXT, home_team TEXT, away_team TEXT
    );
    CREATE TABLE IF NOT EXISTS odds_snapshots(
      snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL, game_id TEXT NOT NULL,
      market TEXT NOT NULL, side TEXT NOT NULL,
      point REAL, price_american INTEGER, implied_prob REAL, raw_json TEXT,
      FOREIGN KEY (game_id) REFERENCES games(game_id)
    );
    """)
    conn.commit()

def month_key():
    return dt.datetime.now(UTC).strftime("%Y%m")

def get_used(conn):
    row = conn.execute("SELECT requests_used FROM api_usage WHERE yyyymm=?", (month_key(),)).fetchone()
    return row[0] if row else 0

def add_used(conn, n=1):
    mk = month_key()
    row = conn.execute("SELECT requests_used FROM api_usage WHERE yyyymm=?", (mk,)).fetchone()
    if row:
        conn.execute("UPDATE api_usage SET requests_used=? WHERE yyyymm=?", (row[0]+n, mk))
    else:
        conn.execute("INSERT INTO api_usage(yyyymm,requests_used) VALUES(?,?)", (mk, n))
    conn.commit()

def american_to_prob(a):
    try:
        a = int(a)
    except: return None
    return 100/(a+100) if a>0 else (-a)/((-a)+100)

def fetch(api_key):
    params = dict(apiKey=api_key, regions=REGIONS, markets=",".join(MARKETS),
                  oddsFormat=ODDS_FORMAT, bookmakers=BOOKMAKER)
    url = f"{API_BASE}/{SPORT}/odds?{urlencode(params)}"
    r = requests.get(url, timeout=25); r.raise_for_status()
    return r.json()

def upsert_games(conn, events):
    for ev in events:
        conn.execute("""INSERT INTO games(game_id,commence_time,home_team,away_team)
                        VALUES(?,?,?,?)
                        ON CONFLICT(game_id) DO UPDATE SET
                          commence_time=excluded.commence_time,
                          home_team=excluded.home_team,
                          away_team=excluded.away_team;""",
                     (ev.get("id"), ev.get("commence_time"), ev.get("home_team"), ev.get("away_team")))
    conn.commit()

def save_snap(conn, events):
    ts = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows=[]
    for ev in events:
        gid = ev.get("id")
        dk = next((b for b in (ev.get("bookmakers") or []) if b.get("key")==BOOKMAKER), None)
        if not dk: continue
        for m in dk.get("markets") or []:
            mk = m.get("key")
            for o in m.get("outcomes") or []:
                side=(o.get("name") or "").lower()
                point=o.get("point"); price=o.get("price")
                rows.append((ts,gid,mk,side,point,price,american_to_prob(price),
                             json.dumps({"market":mk,"outcome":o}, ensure_ascii=False)))
    if rows:
        conn.executemany("""INSERT INTO odds_snapshots
            (ts_utc,game_id,market,side,point,price_american,implied_prob,raw_json)
            VALUES(?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
    return len(rows)

def main():
    key = load_api_key()
    conn = db(); setup(conn)
    used = get_used(conn)
    if used >= MONTHLY_CAP:
        print(f"[SAFE-STOP] Monthly cap reached ({used}/{MONTHLY_CAP}).")
        return
    try:
        data = fetch(key); add_used(conn,1)
    except requests.HTTPError as e:
        print("HTTP error from TheOddsAPI:", e); return
    except Exception as e:
        print("Failed to fetch odds:", e); return
    upsert_games(conn, data)
    n = save_snap(conn, data)
    print(f"Snapshot saved — {n} rows. Used {get_used(conn)}/{MONTHLY_CAP}. DB: {Path(DB_PATH).resolve()}")
    for row in conn.execute("""SELECT ts_utc,market,side,point,price_american,ROUND(implied_prob,3)
                               FROM odds_snapshots ORDER BY snapshot_id DESC LIMIT 10;"""):
        print(row)

if __name__=="__main__": main()
