# app.py — NFL Odds Dashboard (DraftKings)
# - Latest board (ML/Spreads/Totals) from the most recent snapshot
# - No-vig edges vs DK implied, with $3 stake suggestion (25% Kelly cap)
# - Filters: market, team; adjustable edge threshold
# - Game drill-down view for the selected matchup
#
# Run: streamlit run app.py

import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import streamlit as st

DB_PATH = "odds.db"
UNIT_DOLLARS = 3.00
DEFAULT_EDGE_THRESHOLD = float(os.environ.get("EDGE_THRESHOLD", "0.03"))

# ---------- Helpers ----------
def utc_to_et(ts: str) -> str:
    """Convert 'YYYY-MM-DDTHH:MM:SSZ' to local ET nice string."""
    # our snapshots end with Z; convert to +00:00 for fromisoformat
    ts_iso = ts.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(ts_iso).astimezone(ZoneInfo("America/New_York"))
    return dt_utc.strftime("%b %d, %Y %I:%M %p ET")

def american_to_prob(a: int) -> float:
    return 100 / (a + 100) if a > 0 else (-a) / ((-a) + 100)

def american_to_decimal(a: int) -> float:
    return 1 + (a / 100) if a > 0 else 1 + (100 / (-a))

def kelly_fraction(p: float, dec: float) -> float:
    b = dec - 1.0
    q = 1 - p
    f = (p * b - q) / b
    return max(0.0, min(f, 1.0))

# ---------- Data access ----------
@st.cache_data(ttl=60)
def get_latest_ts():
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT ts_utc FROM odds_snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        return row[0] if row else None

@st.cache_data(ttl=60)
def load_latest_board(ts_utc: str) -> pd.DataFrame:
    q = """
    SELECT g.home_team, g.away_team, s.game_id, s.market, s.side, s.point,
           s.price_american, s.implied_prob
    FROM odds_snapshots s
    JOIN games g ON g.game_id = s.game_id
    WHERE s.ts_utc = ?
    """
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(q, c, params=(ts_utc,))
    return df

def compute_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Return a row per actionable side with fair, implied, edge, and stake."""
    out = []
    for (gid, mkt), grp in df.groupby(["game_id", "market"]):
        if mkt in ("h2h", "spreads"):
            if not {"home", "away"}.issubset(set(grp["side"].str.lower())):
                continue
            rows = [grp[grp["side"].str.lower()=="home"].iloc[0],
                    grp[grp["side"].str.lower()=="away"].iloc[0]]
        elif mkt == "totals":
            if not {"over", "under"}.issubset(set(grp["side"].str.lower())):
                continue
            rows = [grp[grp["side"].str.lower()=="over"].iloc[0],
                    grp[grp["side"].str.lower()=="under"].iloc[0]]
        else:
            continue

        # implied probs (with vig)
        def imp(row):
            return (row["implied_prob"]
                    if pd.notna(row["implied_prob"])
                    else american_to_prob(int(row["price_american"])))
        p1, p2 = imp(rows[0]), imp(rows[1])
        tot = p1 + p2
        if tot <= 0:
            continue

        fair = [p1/tot, p2/tot]
        for row, f in zip(rows, fair):
            implied = imp(row)
            edge = f - implied
            price = int(row["price_american"])
            dec = american_to_decimal(price)
            frac = kelly_fraction(f, dec) * 0.25  # 25% Kelly
            stake_dollars = min(UNIT_DOLLARS, round(frac * 100, 2))

            out.append({
                "matchup": f"{row['away_team']} @ {row['home_team']}",
                "game_id": row["game_id"],
                "market": row["market"],
                "side": str(row["side"]).lower(),
                "line": row["point"],
                "price": price,
                "implied": round(implied, 3),
                "fair": round(f, 3),
                "edge": round(edge, 3),
                "stake_$": stake_dollars
            })
    if not out:
        return pd.DataFrame(columns=["matchup","game_id","market","side","line","price","implied","fair","edge","stake_$"])
    edf = pd.DataFrame(out).sort_values("edge", ascending=False).reset_index(drop=True)
    return edf

# ---------- UI ----------
st.set_page_config(page_title="NFL Odds Tracker", layout="wide")
st.title("🏈 NFL Odds Tracker — DraftKings")

if not Path(DB_PATH).exists():
    st.error("No database found. Run `odds_pull.py` to create `odds.db`.")
    st.stop()

ts = get_latest_ts()
if not ts:
    st.warning("No snapshots yet. Run `odds_pull.py` to pull your first snapshot.")
    st.stop()

st.caption(f"Latest snapshot: `{ts}`  •  {utc_to_et(ts)}")

board = load_latest_board(ts)
edges = compute_edges(board)

# Sidebar controls
st.sidebar.header("Filters")
markets = st.sidebar.multiselect("Markets", ["h2h","spreads","totals"], default=["h2h","spreads","totals"])
teams = sorted(set(board["home_team"]).union(board["away_team"]))
team_filter = st.sidebar.multiselect("Teams", teams, placeholder="Select teams (optional)")
edge_thr = st.sidebar.slider("Edge threshold (no-vig vs implied)", 0.0, 0.1, DEFAULT_EDGE_THRESHOLD, 0.005, format="%.3f")

# Filter board & edges
board_f = board[board["market"].isin(markets)].copy()
if team_filter:
    board_f = board_f[(board_f["home_team"].isin(team_filter)) | (board_f["away_team"].isin(team_filter))]

edges_f = edges[edges["market"].isin(markets)].copy()
if team_filter:
    edges_f = edges_f[edges_f["matchup"].str.contains("|".join([pd.regex.escape(t) for t in team_filter]), case=False, regex=True)]
edges_hit = edges_f[edges_f["edge"] >= edge_thr].copy()

# Layout
col1, col2 = st.columns([1,1])

with col1:
    st.subheader("Latest Board")
    show_board = board_f[["away_team","home_team","market","side","point","price_american","implied_prob"]].rename(
        columns={"point":"line","price_american":"price","implied_prob":"implied"}
    ).reset_index(drop=True)
    st.dataframe(show_board, use_container_width=True, height=420)

with col2:
    st.subheader("Value Edges (no-vig)")
    if edges_hit.empty:
        st.info("No picks ≥ threshold right now. Try lowering the slider or check closer to kickoff.")
    else:
        show_edges = edges_hit[["matchup","market","side","line","price","implied","fair","edge","stake_$"]].copy()
        show_edges["edge"] = (show_edges["edge"]*100).round(1).astype(str) + "%"
        show_edges["implied"] = (show_edges["implied"]*100).round(1).astype(str) + "%"
        show_edges["fair"] = (show_edges["fair"]*100).round(1).astype(str) + "%"
        st.dataframe(show_edges.reset_index(drop=True), use_container_width=True, height=420)

st.markdown("---")

# Drill-down
st.subheader("Game Drill-Down (latest snapshot)")
matchups = sorted(edges["matchup"].unique()) if not edges.empty else sorted({f"{a} @ {h}" for a,h in zip(board["away_team"], board["home_team"])})
sel = st.selectbox("Choose a matchup", matchups if matchups else ["(no games)"])
if sel and sel != "(no games)":
    away, _, home = sel.partition(" @ ")
    drill = board[(board["away_team"]==away) & (board["home_team"]==home)].copy()
    drill = drill[["market","side","point","price_american","implied_prob"]].rename(
        columns={"point":"line","price_american":"price","implied_prob":"implied"}
    ).sort_values(["market","side"]).reset_index(drop=True)
    st.dataframe(drill, use_container_width=True)

# Actions row
c1, c2, c3 = st.columns([1,1,1])
with c1:
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()
with c2:
    st.caption(f"Unit size: ${UNIT_DOLLARS:.2f} • Kelly: 25%")
with c3:
    st.caption("Edges = no-vig fair − DK implied")

st.caption("Tip: keep this tab open while your scheduled jobs run; click Refresh to catch new snapshots.")
