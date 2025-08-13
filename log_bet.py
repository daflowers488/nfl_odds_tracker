# log_bet.py — quick CLI to insert a bet you placed
import sqlite3, datetime as dt

DB = "odds.db"

def iso_now_utc():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

def list_games_latest(conn):
    row = conn.execute("SELECT ts_utc FROM odds_snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    if not row:
        print("No snapshots yet. Run odds_pull.py first."); return None, []
    ts = row[0]
    games = conn.execute("""
      SELECT DISTINCT g.game_id, g.away_team, g.home_team
      FROM odds_snapshots s
      JOIN games g ON g.game_id = s.game_id
      WHERE s.ts_utc = ?
      ORDER BY g.away_team, g.home_team
    """, (ts,)).fetchall()
    return ts, games

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ts, games = list_games_latest(conn)
    if not games: return

    print(f"Latest snapshot: {ts}")
    for i, g in enumerate(games, 1):
        print(f"{i:2d}. {g['away_team']} @ {g['home_team']}  ({g['game_id']})")
    idx = int(input("Select game #: ").strip())
    g = games[idx-1]
    game_id = g["game_id"]
    matchup = f"{g['away_team']} @ {g['home_team']}"

    market = input("Market [h2h/spreads/totals]: ").strip().lower()
    if market not in ("h2h","spreads","totals"):
        print("Bad market."); return

    if market in ("h2h","spreads"):
        side = input("Side [home/away]: ").strip().lower()
        if side not in ("home","away"): print("Bad side."); return
    else:
        side = input("Side [over/under]: ").strip().lower()
        if side not in ("over","under"): print("Bad side."); return

    line = None
    if market in ("spreads","totals"):
        try:
            line = float(input("Line (e.g., -2.5 or 44.5): ").strip())
        except:
            print("Bad line."); return

    try:
        price = int(input("Price (American, e.g., -110 or +120): ").strip())
        stake = float(input("Stake USD (e.g., 3): ").strip())
    except:
        print("Bad price/stake."); return

    note = input("Note (optional): ").strip()
    ts_placed = iso_now_utc()

    conn.execute("""
      INSERT INTO bets (ts_placed_utc, game_id, matchup, market, side, line,
                        price_american, stake_usd, source, note, result)
      VALUES (?,?,?,?,?,?,?,?,?,?, 'pending')
    """, (ts_placed, game_id, matchup, market, side, line, price, stake, "manual", note))
    conn.commit()
    conn.close()
    print("✅ Bet saved.")

if __name__ == "__main__":
    main()
