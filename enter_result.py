# enter_result.py — mark a bet as win/loss/push and compute P&L
import sqlite3

DB = "odds.db"

def payout_american(stake, price):
    # returns net P&L (profit positive; loss negative; push 0)
    if price > 0:
        return stake * (price/100.0)
    else:
        return stake * (100.0/abs(price))

def list_pending(conn):
    rows = conn.execute("""
      SELECT bet_id, ts_placed_utc, matchup, market, side, line, price_american, stake_usd, result
      FROM bets
      WHERE result IS NULL OR result='pending'
      ORDER BY bet_id DESC
    """).fetchall()
    return rows

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = list_pending(conn)
    if not rows:
        print("No pending bets.")
        return
    print("Pending bets:")
    for r in rows:
        ln = "" if r["line"] is None else f" {r['line']}"
        print(f"{r['bet_id']:4d} | {r['ts_placed_utc']} | {r['matchup']} [{r['market']}] {r['side']}{ln} {r['price_american']}  stake ${r['stake_usd']}")

    bet_id = int(input("Enter bet_id to grade: ").strip())
    bet = conn.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
    if not bet:
        print("Not found."); return

    res = input("Result [win/loss/push/void]: ").strip().lower()
    if res not in ("win","loss","push","void"):
        print("Bad result."); return

    pnl = 0.0
    if res == "win":
        pnl = payout_american(bet["stake_usd"], bet["price_american"])
    elif res == "loss":
        pnl = -bet["stake_usd"]
    elif res == "push":
        pnl = 0.0
    elif res == "void":
        pnl = 0.0

    conn.execute("UPDATE bets SET result=?, pnl_usd=? WHERE bet_id=?", (res, round(pnl,2), bet_id))
    conn.commit()
    conn.close()
    print(f"✅ Updated bet {bet_id}: {res} (PnL ${round(pnl,2)})")

if __name__ == "__main__":
    main()
