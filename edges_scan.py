# edges_scan.py — value-bet scan + Discord alerts (no-vig fair vs DK implied)
# Env (in .env):
#   DISCORD_WEBHOOK_URL=...
#   EDGE_THRESHOLD=0.03
#   ALWAYS_SEND_TOP_N=3

import os, json, sqlite3
from pathlib import Path
import requests

# ---------- Defaults (overridable via .env) ----------
UNIT_DOLLARS      = 3.00      # your unit size
EDGE_THRESHOLD    = 0.03      # alert if fair - implied >= 3%
ALWAYS_SEND_TOP_N = 3         # send top N preview to Discord if no alerts (0=disable)
KELLY_FRACTION    = 0.25      # use 25% Kelly for safety
STATE_FILE        = Path("last_alerted.json")
ENV_PATH          = Path(".env")
DB_PATH           = "odds.db"
DISCORD_MAX       = 1900      # keep margin under 2000 char limit
# -----------------------------------------------------

# ---- Tiny .env loader (no external deps) ----
def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and (k not in os.environ):
            os.environ[k] = v

load_dotenv(ENV_PATH)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
# override tunables from env if provided
try:
    EDGE_THRESHOLD = float(os.environ.get("EDGE_THRESHOLD", EDGE_THRESHOLD))
except Exception:
    pass
try:
    ALWAYS_SEND_TOP_N = int(os.environ.get("ALWAYS_SEND_TOP_N", ALWAYS_SEND_TOP_N))
except Exception:
    pass
# --------------------------------------------

def american_to_prob(a: int) -> float:
    return 100 / (a + 100) if a > 0 else (-a) / ((-a) + 100)

def american_to_decimal(a: int) -> float:
    return 1 + (a / 100) if a > 0 else 1 + (100 / (-a))

def kelly_fraction(p: float, dec: float) -> float:
    b = dec - 1.0
    q = 1 - p
    f = (p * b - q) / b
    return f if 0.0 < f < 1.0 else (0.0 if f <= 0 else 1.0)

# ---------- Discord helpers ----------
def send_discord(text: str):
    """Fire-and-forget Discord webhook (safe if unset)."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": text[:DISCORD_MAX]}, timeout=10)
    except Exception:
        pass  # don't crash on webhook errors

def emoji_for_edge(edge: float) -> str:
    return "🟢" if edge >= 0 else "🔴"

def fmt_pick_line_console(s: dict) -> str:
    gm = f"{s['away']} @ {s['home']}"
    pt = "" if s["point"] is None else f" {s['point']}"
    return (f"[{s['market']}] {gm} — {s['side'].upper()}{pt}  "
            f"price {s['price']}  fair≈{s['fair']}  edge≈{s['edge']}  "
            f"stake ${s['stake_$']}")

def fmt_pick_line_discord(s: dict) -> str:
    # Pretty one-liner for Discord (bold, emojis, backticks)
    matchup = f"**{s['away']} @ {s['home']}**"
    if s["market"] == "totals":
        bet_text = f"**{s['side'].title()} {s['point']}**"
    elif s["market"] == "spreads":
        # show + or - with point
        sign_point = s["point"]
        bet_text = f"**{s['side'].title()} {sign_point:+g}**"
    else:  # h2h
        bet_text = f"**{s['side'].upper()} ML**"
    edge_pct = f"{s['edge']*100:.1f}%"
    fair_pct = f"{s['fair']*100:.1f}%"
    return (f"• [{s['market'].title()}] {matchup}\n"
            f"   → {bet_text} — `{s['price']}` | Fair: {fair_pct} | Edge: {emoji_for_edge(s['edge'])} +{edge_pct} | Stake: ${s['stake_$']}")

def fmt_preview_line_discord(s: dict) -> str:
    # For the Top-N preview when no alerts
    matchup = f"**{s['away']} @ {s['home']}**"
    if s["market"] == "totals":
        bet_text = f"**{s['side'].title()} {s['point']}**"
    elif s["market"] == "spreads":
        bet_text = f"**{s['side'].title()} {s['point']:+g}**"
    else:
        bet_text = f"**{s['side'].upper()} ML**"
    edge_pct = f"{s['edge']*100:.1f}%"
    fair_pct = f"{s['fair']*100:.1f}%"
    imp_pct  = f"{s['implied']*100:.1f}%"
    return (f"• [{s['market'].title()}] {matchup}\n"
            f"   → {bet_text} — `{s['price']}` | Fair: {fair_pct} | Imp: {imp_pct} | Edge: {emoji_for_edge(s['edge'])} {edge_pct}")

# ---------- State ----------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ---------- DB ----------
def fetch_latest_snapshot_ts(conn) -> str | None:
    row = conn.execute(
        "SELECT ts_utc FROM odds_snapshots ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None

# ---------- Main ----------
def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    ts = fetch_latest_snapshot_ts(conn)
    if not ts:
        print("No snapshots yet. Run odds_pull.py first.")
        return

    rows = conn.execute("""
        SELECT g.home_team, g.away_team, s.game_id, s.market, s.side, s.point,
               s.price_american, s.implied_prob
        FROM odds_snapshots s
        JOIN games g ON g.game_id = s.game_id
        WHERE s.ts_utc = ? AND s.market IN ('h2h','spreads','totals')
        ORDER BY s.game_id, s.market, s.side
    """, (ts,)).fetchall()

    from collections import defaultdict
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["game_id"], r["market"])].append(r)

    suggestions = []
    all_bets = []  # for preview

    for (gid, market), group in by_key.items():
        # require both sides for no-vig
        if market in ("h2h", "spreads"):
            home = next((r for r in group if r["side"] == "home"), None)
            away = next((r for r in group if r["side"] == "away"), None)
            if not home or not away:
                continue
            pair = [home, away]
        else:  # totals
            over  = next((r for r in group if r["side"] == "over"), None)
            under = next((r for r in group if r["side"] == "under"), None)
            if not over or not under:
                continue
            pair = [over, under]

        p1 = pair[0]["implied_prob"] if pair[0]["implied_prob"] is not None else american_to_prob(int(pair[0]["price_american"]))
        p2 = pair[1]["implied_prob"] if pair[1]["implied_prob"] is not None else american_to_prob(int(pair[1]["price_american"]))
        tot = p1 + p2
        if tot <= 0:
            continue

        fair_probs = (p1 / tot, p2 / tot)

        for r, fair in zip(pair, fair_probs):
            imp = r["implied_prob"] if r["implied_prob"] is not None else american_to_prob(int(r["price_american"]))
            edge = fair - imp

            all_bets.append({
                "home":  r["home_team"],
                "away":  r["away_team"],
                "market": r["market"],
                "side":   r["side"],
                "point":  r["point"],
                "price":  int(r["price_american"]),
                "fair":   round(fair, 3),
                "implied": round(imp, 3),
                "edge":   round(edge, 3),
            })

            if edge >= EDGE_THRESHOLD:
                dec = american_to_decimal(int(r["price_american"]))
                frac = kelly_fraction(fair, dec) * KELLY_FRACTION
                stake_dollars = min(UNIT_DOLLARS, round(frac * 100, 2))  # cap to $3

                suggestions.append({
                    "home":  r["home_team"],
                    "away":  r["away_team"],
                    "market": r["market"],
                    "side":   r["side"],
                    "point":  r["point"],
                    "price":  int(r["price_american"]),
                    "fair":   round(fair, 3),
                    "edge":   round(edge, 3),
                    "stake_$": stake_dollars
                })

    suggestions.sort(key=lambda x: x["edge"], reverse=True)
    all_bets.sort(key=lambda x: x["edge"], reverse=True)

    print(f"Latest snapshot: {ts}")
    if not suggestions:
        print(f"No bets ≥ edge threshold ({EDGE_THRESHOLD:.2%}).")

        # ----- Console preview -----
        if all_bets and ALWAYS_SEND_TOP_N > 0:
            top = all_bets[:ALWAYS_SEND_TOP_N]
            print("\nTop edges (below threshold):")
            for s in top:
                pt = "" if s["point"] is None else f" {s['point']}"
                print(f" [{s['market']}] {s['away']} @ {s['home']} — {s['side'].upper()}{pt}"
                      f"  price {s['price']}  fair≈{s['fair']}  imp≈{s['implied']}  edge≈{s['edge']}")

            # ----- Discord preview -----
            if DISCORD_WEBHOOK_URL:
                header = f"👋 **Preview (below threshold)** — _no-vig vs implied_"
                lines = [fmt_preview_line_discord(s) for s in top]
                msg = f"{header}\n" + "\n".join(lines)
                send_discord(msg)
        return

    # duplicate-alert guard per snapshot
    state = load_state()
    if state.get("last_ts") == ts:
        print("Alerts already sent for this snapshot.")
        return

    # Compose and send full alert
    header = f"📊 **Value Bets (No-Vig)** — `{ts}`"
    lines = [fmt_pick_line_console(s) for s in suggestions]
    print("\n".join([f"Latest snapshot: {ts}", *lines]))  # console (plain)

    if DISCORD_WEBHOOK_URL:
        pretty_lines = [fmt_pick_line_discord(s) for s in suggestions]
        body = header + "\n\n" + "\n".join(pretty_lines)
        send_discord(body)

    state["last_ts"] = ts
    save_state(state)

if __name__ == "__main__":
    main()
