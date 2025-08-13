# app.py — NFL Odds Dashboard (DraftKings) — pretty edition with logos
# Run: streamlit run app.py

import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import streamlit as st

DB_PATH = "odds.db"
UNIT_DOLLARS = 3.00
DEFAULT_EDGE_THRESHOLD = float(os.environ.get("EDGE_THRESHOLD", "0.03"))

# ---------- Team logos (ESPN CDN) ----------
# Maps TheOddsAPI full team names -> ESPN slug (lowercase)
ESPN_ABBR = {
    "Arizona Cardinals": "ari", "Atlanta Falcons": "atl", "Baltimore Ravens": "bal",
    "Buffalo Bills": "buf", "Carolina Panthers": "car", "Chicago Bears": "chi",
    "Cincinnati Bengals": "cin", "Cleveland Browns": "cle", "Dallas Cowboys": "dal",
    "Denver Broncos": "den", "Detroit Lions": "det", "Green Bay Packers": "gb",
    "Houston Texans": "hou", "Indianapolis Colts": "ind", "Jacksonville Jaguars": "jax",
    "Kansas City Chiefs": "kc", "Las Vegas Raiders": "lv", "Los Angeles Chargers": "lac",
    "Los Angeles Rams": "lar", "Miami Dolphins": "mia", "Minnesota Vikings": "min",
    "New England Patriots": "ne", "New Orleans Saints": "no", "New York Giants": "nyg",
    "New York Jets": "nyj", "Philadelphia Eagles": "phi", "Pittsburgh Steelers": "pit",
    "San Francisco 49ers": "sf", "Seattle Seahawks": "sea", "Tampa Bay Buccaneers": "tb",
    "Tennessee Titans": "ten", "Washington Commanders": "wsh", "Cleveland Cavaliers": "cle"  # safety dup
}
def logo_url(team: str) -> str | None:
    abbr = ESPN_ABBR.get(team)
    if not abbr: return None
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/{abbr}.png"

# ---------- Helpers ----------
def utc_to_et(ts: str) -> str:
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

def market_badge(mkt: str) -> str:
    if mkt == "h2h": return "ML"
    if mkt == "spreads": return "Spread"
    if mkt == "totals": return "Total"
    return mkt

# ---------- Data ----------
@st.cache_data(ttl=60)
def get_latest_ts():
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT ts_utc FROM odds_snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        return row[0] if row else None

@st.cache_data(ttl=60)
def load_board(ts_utc: str) -> pd.DataFrame:
    q = """
    SELECT g.home_team, g.away_team, s.game_id, s.market, s.side, s.point,
           s.price_american, s.implied_prob
    FROM odds_snapshots s
    JOIN games g ON g.game_id = s.game_id
    WHERE s.ts_utc = ?
    """
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(q, c, params=(ts_utc,))
    df["side"] = df["side"].str.lower()
    return df

def compute_edges(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (gid, mkt), grp in df.groupby(["game_id", "market"]):
        if mkt in ("h2h", "spreads"):
            if not {"home","away"}.issubset(set(grp["side"])): continue
            rows = [grp[grp["side"]=="home"].iloc[0], grp[grp["side"]=="away"].iloc[0]]
        elif mkt == "totals":
            if not {"over","under"}.issubset(set(grp["side"])): continue
            rows = [grp[grp["side"]=="over"].iloc[0], grp[grp["side"]=="under"].iloc[0]]
        else:
            continue

        def imp(r):
            return r["implied_prob"] if pd.notna(r["implied_prob"]) else american_to_prob(int(r["price_american"]))
        p1, p2 = imp(rows[0]), imp(rows[1])
        tot = p1 + p2
        if tot <= 0: continue

        fair_probs = [p1/tot, p2/tot]
        for r, f in zip(rows, fair_probs):
            implied = imp(r)
            edge = f - implied
            price = int(r["price_american"])
            dec = american_to_decimal(price)
            frac = kelly_fraction(f, dec) * 0.25
            stake_dollars = min(UNIT_DOLLARS, round(frac * 100, 2))
            out.append({
                "matchup": f"{r['away_team']} @ {r['home_team']}",
                "away_team": r["away_team"], "home_team": r["home_team"],
                "game_id": r["game_id"], "market": r["market"], "side": r["side"],
                "line": r["point"], "price": price,
                "implied": round(implied, 3), "fair": round(f, 3), "edge": round(edge, 3),
                "stake_$": stake_dollars
            })
    if not out:
        return pd.DataFrame(columns=["matchup","away_team","home_team","game_id","market","side","line","price","implied","fair","edge","stake_$"])
    return pd.DataFrame(out).sort_values("edge", ascending=False).reset_index(drop=True)

@st.cache_data(ttl=60)
def load_history(away, home, market):
    q = """
    SELECT s.snapshot_id, s.ts_utc, s.market, s.side, s.point, s.price_american
    FROM odds_snapshots s
    JOIN games g ON g.game_id = s.game_id
    WHERE g.away_team = ? AND g.home_team = ? AND s.market = ?
    ORDER BY s.snapshot_id ASC
    """
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(q, c, params=(away, home, market))
    if df.empty: return df
    df["ts_local"] = pd.to_datetime(df["ts_utc"].str.replace("Z","+00:00")).dt.tz_convert("America/New_York")
    return df

# ---------- UI ----------
st.set_page_config(page_title="NFL Odds Tracker", layout="wide")
st.title("🏈 NFL Odds Tracker — DraftKings")

if not Path(DB_PATH).exists():
    st.error("No database found. Run `odds_pull.py` to create `odds.db`.")
    st.stop()

# Sidebar controls (with auto-refresh)
with st.sidebar:
    st.header("Controls")
    auto_ms = st.number_input("Auto-refresh (ms)", min_value=0, value=120000, step=1000, help="0 = off")
    show_logos = st.checkbox("Show team logos", value=True)
    edge_thr = st.slider("Edge threshold (no-vig vs implied)", 0.0, 0.10, DEFAULT_EDGE_THRESHOLD, 0.005, format="%.3f")
    markets = st.multiselect("Markets", ["h2h","spreads","totals"], default=["h2h","spreads","totals"])
    st.caption(f"Unit: ${UNIT_DOLLARS:.2f} • Kelly: 25%")

# Auto refresh via meta tag
if auto_ms and auto_ms > 0:
    auto_sec = max(1, int(auto_ms // 1000))
    st.markdown(f"<meta http-equiv='refresh' content='{auto_sec}'>", unsafe_allow_html=True)

ts = get_latest_ts()
if not ts:
    st.warning("No snapshots yet. Run `odds_pull.py` to pull your first snapshot.")
    st.stop()

st.caption(f"Latest snapshot: `{ts}`  •  {utc_to_et(ts)}")

board = load_board(ts)
edges = compute_edges(board)

# Build logo columns
if show_logos:
    edges["away_logo"] = edges["away_team"].map(logo_url)
    edges["home_logo"] = edges["home_team"].map(logo_url)

teams = sorted(set(board["home_team"]).union(board["away_team"]))
tabs = st.tabs(["📋 Board", "🎯 Edges", "📈 History", "⚙️ Settings"])

# -------- Board Tab --------
with tabs[0]:
    team_filter = st.multiselect("Filter teams", teams, placeholder="Select teams (optional)")
    b = board[board["market"].isin(markets)].copy()
    if team_filter:
        b = b[(b["home_team"].isin(team_filter)) | (b["away_team"].isin(team_filter))]

    if show_logos:
        b["away_logo"] = b["away_team"].map(logo_url)
        b["home_logo"] = b["home_team"].map(logo_url)

    # reorder + rename columns
    cols = []
    if show_logos:
        cols += ["away_logo","away_team","home_logo","home_team"]
    else:
        cols += ["away_team","home_team"]
    cols += ["market","side","point","price_american","implied_prob"]
    show = (b[cols].rename(columns={
        "point":"line","price_american":"price","implied_prob":"implied"
    }).reset_index(drop=True))

    if show_logos:
        st.dataframe(
            show,
            use_container_width=True,
            height=460,
            column_config={
                "away_logo": st.column_config.ImageColumn("Away", width="small"),
                "home_logo": st.column_config.ImageColumn("Home", width="small"),
                "market": st.column_config.Column("Market", help="ML / Spread / Total", width="small"),
                "implied": st.column_config.NumberColumn("Implied", format="%.3f"),
                "price": st.column_config.NumberColumn("Price"),
                "line": st.column_config.NumberColumn("Line"),
            }
        )
    else:
        st.dataframe(show, use_container_width=True, height=460)

# -------- Edges Tab --------
with tabs[1]:
    team_filter2 = st.multiselect("Filter teams", teams, key="edges_teams", placeholder="Select teams (optional)")
    e = edges[edges["market"].isin(markets)].copy()
    if team_filter2:
        patt = "|".join([pd.regex.escape(t) for t in team_filter2])
        e = e[e["matchup"].str.contains(patt, case=False, regex=True)]
    e_hit = e[e["edge"] >= edge_thr].copy()

    st.subheader("Value Edges (no-vig)")

    if e_hit.empty:
        st.info("No picks ≥ threshold right now. Try lowering the slider or check closer to kickoff.")
    else:
        # formatting columns
        e_hit["edge_pct_num"] = (e_hit["edge"]*100).clip(lower=0, upper=10)  # cap bar at 10%
        e_hit["edge %"] = (e_hit["edge"]*100).round(1).astype(str) + "%"
        e_hit["implied %"] = (e_hit["implied"]*100).round(1).astype(str) + "%"
        e_hit["fair %"] = (e_hit["fair"]*100).round(1).astype(str) + "%"
        e_hit["Market"] = e_hit["market"].map(market_badge)

        # column order
        cols = []
        if show_logos:
            cols += ["away_logo","away_team","home_logo","home_team"]
        else:
            cols += ["matchup"]
        cols += ["Market","side","line","price","implied %","fair %","edge %","stake_$","edge_pct_num"]

        table = e_hit[cols].reset_index(drop=True)

        # build column config (images + progress bar)
        col_config = {
            "Market": st.column_config.Column("Market", width="small"),
            "side": st.column_config.Column("Side", width="small"),
            "line": st.column_config.NumberColumn("Line", width="small"),
            "price": st.column_config.NumberColumn("Price", width="small"),
            "implied %": st.column_config.Column("Implied"),
            "fair %": st.column_config.Column("Fair"),
            "edge %": st.column_config.Column("Edge"),
            "stake_$": st.column_config.NumberColumn("Stake ($)", width="small"),
            "edge_pct_num": st.column_config.ProgressColumn("Edge bar", help="Capped at 10%", min_value=0, max_value=10),
        }
        if show_logos:
            col_config.update({
                "away_logo": st.column_config.ImageColumn("Away", width="small"),
                "home_logo": st.column_config.ImageColumn("Home", width="small"),
                "away_team": st.column_config.Column("Away Team"),
                "home_team": st.column_config.Column("Home Team"),
            })
        else:
            col_config.update({"matchup": st.column_config.Column("Matchup")})

        st.dataframe(table, use_container_width=True, height=420, column_config=col_config, hide_index=True)

        # Export helpers
        export_cols = ["matchup","market","side","line","price","implied","fair","edge","stake_$"]
        csv = e_hit[export_cols].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download edges (CSV)", csv, file_name="edges.csv", mime="text/csv")

        # Copy-able text block
        lines = []
        for _, r in e_hit.iterrows():
            line_part = "" if pd.isna(r["line"]) else f" {r['line']}"
            lines.append(f"[{r['market']}] {r['matchup']} — {r['side'].upper()}{line_part}  {r['price']}  edge≈{r['edge']:.3f}  stake ${r['stake_$']}")
        st.text_area("Copy picks", value="\n".join(lines), height=120)

# -------- History Tab --------
with tabs[2]:
    st.subheader("Line Movement")
    matchups = sorted({f"{a} @ {h}" for a,h in zip(board["away_team"], board["home_team"])})
    sel = st.selectbox("Choose a matchup", matchups if matchups else ["(no games)"])
    mkt_choice = st.radio("Market", ["spreads","totals"], horizontal=True)
    if sel and sel != "(no games)":
        away, _, home = sel.partition(" @ ")
        hist = load_history(away, home, mkt_choice)
        if hist.empty:
            st.info("No history yet for this matchup/market. Let the scheduler collect a few snapshots.")
        else:
            if mkt_choice == "spreads":
                want = hist[hist["side"].isin(["home","away"])].copy()
                label_map = {"home":"Home","away":"Away"}
            else:
                want = hist[hist["side"].isin(["over","under"])].copy()
                label_map = {"over":"Over","under":"Under"}
            want["label"] = want["side"].map(label_map)
            chart_df = want.pivot(index="ts_local", columns="label", values="point").copy()
            st.line_chart(chart_df)
            st.dataframe(want[["ts_local","label","point","price_american"]].rename(
                columns={"ts_local":"time (ET)","point":"line","price_american":"price"}), use_container_width=True)

# -------- Settings Tab --------
with tabs[3]:
    st.markdown("### Info")
    st.write(f"- Database: `{Path(DB_PATH).resolve()}`")
    st.write(f"- Latest snapshot: `{ts}`  •  {utc_to_et(ts)}")
    st.write(f"- Unit size: ${UNIT_DOLLARS:.2f}  •  Kelly fraction: 25%")
    st.write("- Edges = no-vig fair − DK implied")
    st.caption("Tip: keep this tab open while your scheduled tasks run; revisit the Edges tab and export the shortlist.")
