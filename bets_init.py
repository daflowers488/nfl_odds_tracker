# bets_init.py — one-time DB migration to add bets table
import sqlite3, os

DB = "odds.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS bets (
  bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_placed_utc TEXT NOT NULL,        -- when you placed the bet (UTC ISO)
  game_id TEXT NOT NULL,              -- join to games.game_id
  matchup TEXT NOT NULL,              -- "Away @ Home" snapshot
  market TEXT NOT NULL,               -- h2h | spreads | totals
  side TEXT NOT NULL,                 -- home/away or over/under
  line REAL,                          -- null for ML
  price_american INTEGER NOT NULL,    -- e.g. -110
  stake_usd REAL NOT NULL,            -- dollars risked
  source TEXT DEFAULT 'manual',       -- e.g. discord pick, manual
  note TEXT,                          -- any free text

  -- Outcome fields (fill later)
  result TEXT,                        -- win | loss | push | void | pending
  pnl_usd REAL,                       -- profit/loss for the ticket

  -- CLV fields (filled after kickoff)
  clv_reference_ts_utc TEXT,          -- last snapshot before kickoff
  clv_line REAL,
  clv_price_american INTEGER
);
""")

# helpful index
c.execute("CREATE INDEX IF NOT EXISTS idx_bets_game ON bets(game_id)")

conn.commit()
conn.close()
print("bets table ready.")
