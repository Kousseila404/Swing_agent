"""
╔══════════════════════════════════════════════════════════════════╗
║  SWING QUANT V19 — PROP FIRM TERMINAL                           ║
║  Dashboard de suivi des performances en conditions Prop Firm.   ║
║                                                                  ║
║  Sections :                                                      ║
║    [TOP]   En-tête + KPIs (Capital, Win Rate, PF, Drawdown)    ║
║    [CHART] Courbe d'équité avec seuil d'élimination Prop Firm  ║
║    [TABLE] Historique des trades (du plus récent au plus vieux) ║
║                                                                  ║
║  Source de données : data/trade_journal.csv                      ║
║  Lancement : streamlit run dashboard.py                          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# CONSTANTES PROP FIRM (cohérentes avec config.py V17)
# ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL  = 100_000.0   # Capital de départ (USD)
WIN_AMOUNT       = 500.0       # Gain fixe par WIN  (+RR 2:1 sur 250$)
LOSS_AMOUNT      = -250.0      # Perte fixe par LOSS (0.25% de 100k)
PROP_FIRM_FLOOR  = 95_000.0   # Seuil d'élimination Prop Firm (−5%)
CSV_PATH         = Path("data") / "trade_journal.csv"

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION STREAMLIT
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Prop Firm Terminal — Swing Quant V19",
    page_icon="🦅",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS Dark Terminal ──────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* ── Fond global ── */
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"] {
        background-color: #0D1117;
    }
    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background-color: #161B22;
        border-right: 1px solid #21262D;
    }
    /* ── Cards métriques ── */
    [data-testid="metric-container"] {
        background: #161B22;
        border: 1px solid #30363D;
        border-radius: 10px;
        padding: 18px 22px;
        transition: border-color 0.2s;
    }
    [data-testid="metric-container"]:hover {
        border-color: #58A6FF;
    }
    /* Label métrique */
    [data-testid="metric-container"] label {
        color: #8B949E !important;
        font-size: 0.7rem !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
    }
    /* Valeur métrique */
    [data-testid="metric-container"] [data-testid="metric-value"] {
        color: #E6EDF3 !important;
        font-size: 1.85rem !important;
        font-weight: 700 !important;
    }
    /* ── Titres ── */
    h1, h2, h3 { color: #E6EDF3 !important; }
    /* ── Séparateur ── */
    hr { border-color: #21262D !important; margin: 16px 0; }
    /* ── Dataframe ── */
    [data-testid="stDataFrame"] iframe {
        border-radius: 8px;
    }
    /* ── Texte général ── */
    p, li, span { color: #C9D1D9; }
    code {
        background: #161B22;
        border: 1px solid #30363D;
        border-radius: 4px;
        padding: 2px 6px;
        color: #58A6FF;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────
# CHARGEMENT DES DONNÉES
# ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_journal():
    """
    Charge le journal de trades depuis data/trade_journal.csv.
    Cache TTL = 30s → se rafraîchit automatiquement à chaque scan.

    Returns:
        DataFrame avec les colonnes du journal, ou None si absent/vide.
    """
    if not CSV_PATH.exists():
        return None
    try:
        df = pd.read_csv(CSV_PATH)
        if df.empty:
            return None
        # Parse la colonne Date — supporte plusieurs formats
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        return df
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# MOTEUR DE CALCUL DES MÉTRIQUES
# ─────────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    """
    Calcule toutes les métriques Prop Firm depuis le journal de trades.

    PnL simulé (cohérent avec les règles de risque V17) :
        WIN  → +500$ (TP atteint  : RR 2:1 × 250$ risqués)
        LOSS → −250$ (SL déclenché : 0.25% du capital de 100k$)
        OPEN → ignoré (trade non clôturé)

    Args:
        df: DataFrame chargé depuis trade_journal.csv.

    Returns:
        Dict contenant : capital, win_rate, profit_factor,
        max_drawdown_pct, max_drawdown_usd, stats de trades,
        equity_curve (pd.Series), equity_dates (list), closed_df.
    """
    _empty = {
        "capital":          INITIAL_CAPITAL,
        "win_rate":         0.0,
        "profit_factor":    0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_usd": 0.0,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "open_trades":      int((df["Status"] == "OPEN").sum()) if "Status" in df.columns else 0,
        "equity_curve":     pd.Series([INITIAL_CAPITAL], dtype=float),
        "equity_dates":     [datetime.now()],
        "closed_df":        pd.DataFrame(),
    }

    if "Status" not in df.columns:
        return _empty

    # ── Sélectionne uniquement les trades clôturés ─────────────────
    closed = df[df["Status"].isin(["WIN", "LOSS"])].copy()

    if closed.empty:
        return _empty

    # ── Tri chronologique ──────────────────────────────────────────
    closed = closed.sort_values("Date", na_position="last").reset_index(drop=True)

    # ── PnL par trade ──────────────────────────────────────────────
    closed["PnL"] = closed["Status"].map({"WIN": WIN_AMOUNT, "LOSS": LOSS_AMOUNT})

    # ── Courbe d'équité (cumul des PnL + capital initial) ──────────
    equity_vals  = INITIAL_CAPITAL + closed["PnL"].cumsum().values
    # Prepend le point initial (capital avant tout trade)
    equity_full  = pd.Series(
        np.concatenate([[INITIAL_CAPITAL], equity_vals]),
        dtype=float,
    )

    # Dates de la courbe : un point fictif avant le 1er trade + dates réelles
    first_date   = closed["Date"].iloc[0] - pd.Timedelta(days=1)
    equity_dates = [first_date] + list(closed["Date"])

    # ── Statistiques ───────────────────────────────────────────────
    wins    = int((closed["Status"] == "WIN").sum())
    losses  = int((closed["Status"] == "LOSS").sum())
    total   = wins + losses

    win_rate = (wins / total * 100) if total > 0 else 0.0

    gross_wins   = wins * WIN_AMOUNT
    gross_losses = losses * abs(LOSS_AMOUNT)
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    # ── Max Drawdown ───────────────────────────────────────────────
    # Drawdown = (valeur_actuelle − pic_historique) / pic_historique
    running_max   = equity_full.cummax()
    drawdown_usd  = equity_full - running_max
    drawdown_pct  = (drawdown_usd / running_max * 100)

    max_dd_pct = float(drawdown_pct.min())   # ≤ 0
    max_dd_usd = float(drawdown_usd.min())   # ≤ 0

    return {
        "capital":          float(equity_full.iloc[-1]),
        "win_rate":         win_rate,
        "profit_factor":    profit_factor,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_usd": max_dd_usd,
        "total_trades":     total,
        "wins":             wins,
        "losses":           losses,
        "open_trades":      int((df["Status"] == "OPEN").sum()),
        "equity_curve":     equity_full,
        "equity_dates":     equity_dates,
        "closed_df":        closed,
    }


# ─────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        """
        <h2 style="color:#E6EDF3; font-size:1.2rem; font-weight:700; margin-bottom:4px;">
            🦅 Prop Firm Terminal
        </h2>
        <p style="color:#6E7681; font-size:0.75rem; margin-top:0;">SwingAgent V19</p>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Paramètres du compte ───────────────────────────────────────
    st.markdown("**⚙️ Paramètres du compte**")
    st.markdown(
        f"""
        <div style="background:#0D1117; border-radius:8px; padding:12px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                <span style="color:#8B949E; font-size:0.8rem;">Capital</span>
                <span style="color:#E6EDF3; font-size:0.8rem; font-weight:600;">
                    ${INITIAL_CAPITAL:,.0f}
                </span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                <span style="color:#8B949E; font-size:0.8rem;">Risque / trade</span>
                <span style="color:#E6EDF3; font-size:0.8rem; font-weight:600;">0.25% ($250)</span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                <span style="color:#8B949E; font-size:0.8rem;">Gain / WIN</span>
                <span style="color:#00D4AA; font-size:0.8rem; font-weight:600;">+$500</span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                <span style="color:#8B949E; font-size:0.8rem;">Perte / LOSS</span>
                <span style="color:#FF4444; font-size:0.8rem; font-weight:600;">−$250</span>
            </div>
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#8B949E; font-size:0.8rem;">Seuil élimination</span>
                <span style="color:#FF4444; font-size:0.8rem; font-weight:600;">
                    ${PROP_FIRM_FLOOR:,.0f} (−5%)
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Source de données ──────────────────────────────────────────
    st.markdown("**📂 Source de données**")
    csv_exists = CSV_PATH.exists()
    if csv_exists:
        csv_size  = CSV_PATH.stat().st_size
        csv_mtime = datetime.fromtimestamp(CSV_PATH.stat().st_mtime).strftime("%H:%M:%S")
        st.markdown(
            f"""
            <div style="background:#0D1117; border-radius:8px; padding:10px; font-size:0.75rem;">
                <div style="color:#00D4AA;">✅ {CSV_PATH.name}</div>
                <div style="color:#6E7681; margin-top:4px;">
                    Taille : {csv_size:,} octets<br>
                    Modifié : {csv_mtime}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="color:#FF4444; font-size:0.8rem;">❌ trade_journal.csv introuvable</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Bouton rafraîchissement manuel ────────────────────────────
    if st.button("🔄 Rafraîchir les données", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ── Lancement du scan ─────────────────────────────────────────
    st.markdown(
        """
        <p style="color:#6E7681; font-size:0.72rem; margin-top:16px; text-align:center;">
            Scan : <code>python main.py</code><br>
            Auto-refresh toutes les 30 secondes
        </p>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────
# EN-TÊTE PRINCIPAL
# ─────────────────────────────────────────────────────────────────
col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    st.markdown(
        """
        <h1 style="
            color:#E6EDF3; font-size:2rem; font-weight:800;
            margin-bottom:2px; letter-spacing:-0.02em;
        ">
            🦅 Swing Quant — Prop Firm Terminal
        </h1>
        <p style="color:#8B949E; margin-top:0; font-size:0.9rem;">
            Suivi des performances live · Capital 100k$ · 0.25% risqué par trade
        </p>
        """,
        unsafe_allow_html=True,
    )
with col_h2:
    st.markdown(
        f"""
        <div style="text-align:right; padding-top:14px;">
            <span style="
                background:#161B22; border:1px solid #30363D;
                color:#8B949E; padding:5px 12px;
                border-radius:20px; font-size:0.72rem;
            ">
                {datetime.now().strftime('%Y-%m-%d %H:%M')}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<hr style="margin:8px 0 20px 0;">', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# CHARGEMENT + GARDE DE SÉCURITÉ (empty state)
# ─────────────────────────────────────────────────────────────────
df = load_journal()

if df is None:
    st.markdown(
        f"""
        <div style="
            background:#161B22; border:1px dashed #30363D;
            border-radius:14px; padding:72px 40px;
            text-align:center; margin-top:32px;
        ">
            <div style="font-size:3.5rem; margin-bottom:12px;">⏳</div>
            <h2 style="color:#8B949E; font-weight:600; margin-bottom:8px;">
                En attente des premiers trades...
            </h2>
            <p style="color:#6E7681; max-width:500px; margin:0 auto; line-height:1.7;">
                Le journal <code>data/trade_journal.csv</code> n'existe pas encore.<br>
                Chaque alerte Telegram génère automatiquement une ligne.<br><br>
                Lancez le scanner : <code>python main.py</code>
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ── Calcul des métriques ───────────────────────────────────────────
metrics = compute_metrics(df)

# ─────────────────────────────────────────────────────────────────
# ALERTES PROP FIRM (prioritaires, avant les KPIs)
# ─────────────────────────────────────────────────────────────────
capital = metrics["capital"]

if capital <= PROP_FIRM_FLOOR:
    st.error(
        "🚨 **ALERTE CRITIQUE — PROP FIRM** · Capital inférieur au seuil d'élimination "
        f"(${PROP_FIRM_FLOOR:,.0f}). **Arrêtez immédiatement tout trading.**"
    )
elif capital <= PROP_FIRM_FLOOR + 2_000:
    st.warning(
        f"⚠️ **ATTENTION** · Capital à **${capital:,.2f}** — vous approchez dangereusement "
        f"du seuil d'élimination (${PROP_FIRM_FLOOR:,.0f}). Réduisez votre exposition."
    )

if metrics["open_trades"] > 0:
    st.info(
        f"📂 **{metrics['open_trades']} trade(s) OPEN** en attente de clôture "
        "— non comptabilisés dans l'équité."
    )

# ─────────────────────────────────────────────────────────────────
# LIGNE 1 — KPIs
# ─────────────────────────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)

# ── KPI 1 : Capital Actuel ─────────────────────────────────────
capital_delta     = capital - INITIAL_CAPITAL
capital_delta_pct = (capital_delta / INITIAL_CAPITAL) * 100
k1.metric(
    label="💰 Capital Actuel",
    value=f"${capital:,.2f}",
    delta=f"{capital_delta:+,.2f}$ ({capital_delta_pct:+.2f}%)",
    delta_color="normal",
)

# ── KPI 2 : Win Rate ──────────────────────────────────────────
win_emoji = "🔥" if metrics["win_rate"] >= 60 else ("✅" if metrics["win_rate"] >= 50 else "⚠️")
k2.metric(
    label=f"🎯 Win Rate  {win_emoji}",
    value=f"{metrics['win_rate']:.1f}%",
    delta=f"{metrics['wins']}W — {metrics['losses']}L — {metrics['total_trades']} trades",
    delta_color="off",
)

# ── KPI 3 : Profit Factor ─────────────────────────────────────
pf = metrics["profit_factor"]
pf_str   = f"{pf:.2f}" if pf != float("inf") else "∞"
pf_delta = (
    "Excellent (≥ 2.0)"   if pf >= 2.0
    else "Acceptable (≥ 1.5)" if pf >= 1.5
    else "Insuffisant (< 1.5)"
)
k3.metric(
    label="⚖️ Profit Factor",
    value=pf_str,
    delta=pf_delta,
    delta_color="off",
)

# ── KPI 4 : Max Drawdown (mis en évidence) ────────────────────
dd_pct = metrics["max_drawdown_pct"]
dd_usd = metrics["max_drawdown_usd"]
k4.metric(
    label="📉 Max Drawdown",
    value=f"{dd_pct:.2f}%",
    delta=f"{dd_usd:,.2f}$",
    delta_color="normal",    # négatif → rouge = signal de danger
)

st.markdown('<hr style="margin:20px 0;">', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# LIGNE 2 — EQUITY CURVE
# ─────────────────────────────────────────────────────────────────
st.subheader("📈 Courbe d'Équité")

equity_vals  = metrics["equity_curve"]
equity_dates = metrics["equity_dates"]

# Couleur de la ligne : verte si au-dessus du capital initial, orange sinon
last_val    = float(equity_vals.iloc[-1])
line_color  = "#00D4AA" if last_val >= INITIAL_CAPITAL else "#F0A500"
fill_color  = "rgba(0, 212, 170, 0.07)" if last_val >= INITIAL_CAPITAL else "rgba(240, 165, 0, 0.06)"

# ── Construction du graphique ──────────────────────────────────
fig = go.Figure()

# ── Aire sous la courbe d'équité ──────────────────────────────
fig.add_trace(go.Scatter(
    x=equity_dates,
    y=equity_vals.tolist(),
    mode="lines+markers",
    name="Équité",
    line=dict(color=line_color, width=2.5),
    marker=dict(
        size=7,
        color=line_color,
        line=dict(width=1.5, color="#0D1117"),
    ),
    fill="tozeroy",
    fillcolor=fill_color,
    hovertemplate=(
        "<b>%{x|%Y-%m-%d}</b><br>"
        "Capital : <b>$%{y:,.2f}</b><extra></extra>"
    ),
))

# ── Ligne rouge pointillée — Seuil d'élimination Prop Firm ────
fig.add_hline(
    y=PROP_FIRM_FLOOR,
    line=dict(color="#FF4444", width=1.8, dash="dash"),
    annotation_text=f"  ⛔ Seuil élimination : ${PROP_FIRM_FLOOR:,.0f}",
    annotation_position="top left",
    annotation_font=dict(color="#FF4444", size=11),
)

# ── Ligne grise pointillée — Capital initial de référence ─────
fig.add_hline(
    y=INITIAL_CAPITAL,
    line=dict(color="#30363D", width=1.2, dash="dot"),
    annotation_text=f"  Capital initial : ${INITIAL_CAPITAL:,.0f}",
    annotation_position="bottom left",
    annotation_font=dict(color="#6E7681", size=10),
)

# ── Zone rouge de danger entre 95k et 100k ────────────────────
fig.add_hrect(
    y0=PROP_FIRM_FLOOR,
    y1=INITIAL_CAPITAL,
    fillcolor="rgba(255, 68, 68, 0.04)",
    line_width=0,
    layer="below",
)

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#0D1117",
    plot_bgcolor="#0D1117",
    xaxis=dict(
        gridcolor="#21262D",
        title=None,
        tickfont=dict(color="#8B949E", size=11),
        tickformat="%d %b",
        showgrid=True,
    ),
    yaxis=dict(
        gridcolor="#21262D",
        title="Capital (USD)",
        tickformat="$,.0f",
        tickfont=dict(color="#8B949E", size=11),
        range=[
            min(float(equity_vals.min()) * 0.997, PROP_FIRM_FLOOR * 0.997),
            float(equity_vals.max()) * 1.003 + 500,
        ],
    ),
    hovermode="x unified",
    margin=dict(l=0, r=0, t=16, b=0),
    height=380,
    showlegend=False,
)

st.plotly_chart(fig, use_container_width=True)

# ── Statistiques résumées sous le graphique ────────────────────
pnl_total = capital - INITIAL_CAPITAL
pnl_color = "#00D4AA" if pnl_total >= 0 else "#FF4444"
pnl_sign  = "+" if pnl_total >= 0 else ""

st.markdown(
    f"""
    <div style="
        display:flex; gap:32px; flex-wrap:wrap;
        background:#161B22; border:1px solid #30363D;
        border-radius:8px; padding:14px 20px; margin-top:6px;
    ">
        <div>
            <span style="color:#6E7681; font-size:0.72rem; text-transform:uppercase;">PnL Total</span><br>
            <span style="color:{pnl_color}; font-size:1rem; font-weight:700;">
                {pnl_sign}${pnl_total:,.2f}
            </span>
        </div>
        <div>
            <span style="color:#6E7681; font-size:0.72rem; text-transform:uppercase;">
                Marge jusqu'au seuil
            </span><br>
            <span style="color:#E6EDF3; font-size:1rem; font-weight:700;">
                ${capital - PROP_FIRM_FLOOR:,.2f}
            </span>
        </div>
        <div>
            <span style="color:#6E7681; font-size:0.72rem; text-transform:uppercase;">
                Trades avant élimination
            </span><br>
            <span style="color:#E6EDF3; font-size:1rem; font-weight:700;">
                {int((capital - PROP_FIRM_FLOOR) / abs(LOSS_AMOUNT))} losses consécutives max
            </span>
        </div>
        <div>
            <span style="color:#6E7681; font-size:0.72rem; text-transform:uppercase;">
                Performance
            </span><br>
            <span style="color:{pnl_color}; font-size:1rem; font-weight:700;">
                {pnl_sign}{(pnl_total / INITIAL_CAPITAL * 100):.2f}%
            </span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<hr style="margin:20px 0;">', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# LIGNE 3 — HISTORIQUE DES TRADES
# ─────────────────────────────────────────────────────────────────
col_title, col_count = st.columns([4, 1])
with col_title:
    st.subheader("📋 Historique des Trades")
with col_count:
    st.markdown(
        f"""
        <div style="text-align:right; padding-top:14px;">
            <span style="
                background:#161B22; border:1px solid #30363D;
                color:#8B949E; padding:4px 12px;
                border-radius:20px; font-size:0.75rem;
            ">
                {len(df)} trade(s)
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── Préparation du DataFrame d'affichage ──────────────────────────
display_df = df.sort_values("Date", ascending=False, na_position="last").copy()

# Date reformatée
display_df["Date"] = pd.to_datetime(display_df["Date"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")

# Colonne PnL simulé
def _pnl_label(status: str) -> str:
    if status == "WIN":
        return f"+${WIN_AMOUNT:.0f}"
    if status == "LOSS":
        return f"${LOSS_AMOUNT:.0f}"
    return "—"

display_df["PnL ($)"] = display_df.get("Status", pd.Series(dtype=str)).apply(
    lambda s: _pnl_label(s) if isinstance(s, str) else "—"
)

# ── Colonnes à afficher (dans l'ordre souhaité) ────────────────────
_ordered_cols = ["Date", "Ticker", "Entry", "Stop_Loss", "Take_Profit", "Size", "RR", "Status", "PnL ($)"]
display_df = display_df[[c for c in _ordered_cols if c in display_df.columns]]

# ── Formatage numérique ────────────────────────────────────────────
_format_dict: dict = {}
for col in ["Entry", "Stop_Loss", "Take_Profit"]:
    if col in display_df.columns:
        _format_dict[col] = "${:.4f}"
if "RR" in display_df.columns:
    _format_dict["RR"] = "{:.2f}"

# ── Coloration des lignes ─────────────────────────────────────────
_STATUS_BG = {
    "WIN":  "background-color: rgba(0, 212, 170, 0.07);",
    "LOSS": "background-color: rgba(255, 68, 68, 0.07);",
    "OPEN": "background-color: rgba(240, 165, 0, 0.05);",
}

def _color_row(row: pd.Series) -> list[str]:
    status = row.get("Status", "") if hasattr(row, "get") else ""
    style  = _STATUS_BG.get(status, "")
    return [style] * len(row)

# ── Coloration des cellules Status et PnL ────────────────────────
def _color_cell(val: object) -> str:
    if not isinstance(val, str):
        return ""
    if val == "WIN"   or val.startswith("+"):  return "color: #00D4AA; font-weight: 700;"
    if val == "LOSS"  or val.startswith("-"):  return "color: #FF4444; font-weight: 700;"
    if val == "OPEN":                          return "color: #F0A500; font-weight: 600;"
    return ""

_style_cols = [c for c in ["Status", "PnL ($)"] if c in display_df.columns]

styled = (
    display_df.style
    .apply(_color_row, axis=1)
    .map(_color_cell, subset=_style_cols)
    .format(_format_dict, na_rep="—")
)

st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Message si aucun trade fermé ─────────────────────────────────
if metrics["total_trades"] == 0:
    st.markdown(
        """
        <div style="
            background:#161B22; border:1px dashed #30363D;
            border-radius:10px; padding:32px; text-align:center; margin-top:12px;
        ">
            <p style="color:#6E7681; margin:0;">
                Aucun trade clôturé (WIN / LOSS) pour l'instant.<br>
                Les trades <b style="color:#F0A500;">OPEN</b> apparaissent dans le tableau
                mais ne contribuent pas aux métriques de performance.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <hr style="margin: 28px 0 12px 0;">
    <div style="text-align:center; color:#6E7681; font-size:0.72rem; padding-bottom:12px;">
        SwingAgent V19 — Prop Firm Terminal &nbsp;·&nbsp;
        Capital initial : $100,000 &nbsp;·&nbsp;
        Risque : 0.25%/trade ($250) &nbsp;·&nbsp;
        Seuil élimination : $95,000 (−5%) &nbsp;·&nbsp;
        Source : <code>data/trade_journal.csv</code>
    </div>
    """,
    unsafe_allow_html=True,
)
