import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="48h Delivery Threshold Simulator", layout="wide", page_icon="🚚")

st.markdown("""
<style>
[data-testid="stMetricDelta"] svg { display: none; }
.block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────────
COUNTRY_MAP = {"SE01": "Sweden 🇸🇪", "DK01": "Denmark 🇩🇰", "NO01": "Norway 🇳🇴", "FI01": "Finland 🇫🇮"}
BASE_THRESH = {"SE01": 700, "DK01": 1500, "NO01": 2500, "FI01": 2500}
COLORS      = {"SE01": "#1f77b4", "DK01": "#2ca02c", "NO01": "#ff7f0e", "FI01": "#9467bd"}

def fmt_kg(v):
    """Format a weight value as tonnes with one decimal, e.g. 12 345 kg → '12.3 t'."""
    return f"{v/1000:,.1f} t"

# ── Data loading ───────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Reading Excel file…")
def load_data(_uploaded_file, file_name):
    df_raw = pd.read_excel(_uploaded_file, sheet_name="ZDELEXAS raw data Nordics")
    del_df = (
        df_raw
        .groupby(["Delivery", "Ship-To Party", "Delivery Date", "Sales Org", "Shipping Conditions"])
        .agg(delivery_weight=("Net Weight", "sum"))
        .reset_index()
    )
    del_df["Delivery Date"] = pd.to_datetime(del_df["Delivery Date"])
    return del_df


def weight_flagged(del_df: pd.DataFrame, thresholds: dict) -> dict:
    """Total kg in deliveries flagged as 48h (delivery_weight >= threshold) per Sales Org."""
    result = {}
    for org, thresh in thresholds.items():
        sub = del_df[del_df["Sales Org"] == org]
        result[org] = float(sub.loc[sub["delivery_weight"] >= thresh, "delivery_weight"].sum())
    return result


def find_matching_threshold(del_df: pd.DataFrame, org: str, target_kg: float) -> float | None:
    """
    Find the lowest threshold such that the total weight of flagged deliveries
    is >= target_kg.  Works by sorting deliveries heaviest-first and walking
    down until the cumulative weight crosses the target.
    """
    sub = del_df[del_df["Sales Org"] == org][["delivery_weight"]].copy()
    sub = sub.sort_values("delivery_weight", ascending=False).reset_index(drop=True)
    cumsum = sub["delivery_weight"].cumsum().values
    weights = sub["delivery_weight"].values

    # Find first index where cumulative weight >= target
    idx = np.searchsorted(cumsum, target_kg)
    if idx >= len(weights):
        return None
    # The threshold is the weight of the delivery at that index
    return float(weights[idx])


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    uploaded = st.file_uploader("📂 Upload the Excel file", type=["xlsx"])

    st.markdown("---")
    st.subheader("Step 1 thresholds (kg)")
    st.caption(
        "Lower the threshold → more deliveries flagged 48h at order creation → more kg in 48h.  \n"
        "Goal: lower enough so total 48h **weight** stays the same after removing the 3rd-party check."
    )

    sim_thresholds = {}
    for org, label in COUNTRY_MAP.items():
        base = BASE_THRESH[org]
        sim_thresholds[org] = st.slider(
            label,
            min_value=max(1, int(base * 0.05)),
            max_value=base,
            value=base,
            step=5,
            help=f"Baseline: {base} kg. Slide left to lower the threshold.",
        )

    st.markdown("---")
    st.caption("**SC=5** = 48h delivery  |  **SC=2** = 24h delivery")

# ── Main content ───────────────────────────────────────────────────────────────
st.title("🚚 48h Delivery Threshold Simulator — Nordics")
st.markdown(
    "**Goal:** Find the minimum threshold reduction at Step 1 (order creation) "
    "that keeps the total **weight shipped in 48h** stable after deactivating the "
    "3rd-party hourly aggregation check."
)

if uploaded is None:
    st.info("👈 Upload the Excel file in the sidebar to begin.")
    st.stop()

del_df = load_data(uploaded, uploaded.name)

# ── Compute key numbers ────────────────────────────────────────────────────────
# Baseline: actual kg in SC=5 deliveries (Step 1 + 3rd-party combined)
baseline_kg = {
    org: float(del_df.loc[(del_df["Sales Org"] == org) & (del_df["Shipping Conditions"] == 5), "delivery_weight"].sum())
    for org in BASE_THRESH
}
baseline_total_kg = sum(baseline_kg.values())

# Step1-only at original thresholds (how much weight would be flagged without 3rd party)
step1_base_kg    = weight_flagged(del_df, BASE_THRESH)
step1_base_total = sum(step1_base_kg.values())

# Simulated: Step1-only with user-adjusted thresholds
sim_kg    = weight_flagged(del_df, sim_thresholds)
sim_total = sum(sim_kg.values())

# ── Top KPIs ───────────────────────────────────────────────────────────────────
st.markdown("### 📊 Overall summary")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Current 48h weight (target)",
        fmt_kg(baseline_total_kg),
        help="Total kg in SC=5 deliveries in your data (Step 1 + 3rd-party combined). This is what we want to preserve.",
    )
with c2:
    delta2 = step1_base_total - baseline_total_kg
    st.metric(
        "Step 1 only @ baseline threshold",
        fmt_kg(step1_base_total),
        delta=f"{fmt_kg(abs(delta2))} {'below' if delta2 < 0 else 'above'} target",
        delta_color="off",
        help="How much weight Step 1 alone flags at the current (unmodified) thresholds — shows the 3rd-party gap.",
    )
with c3:
    delta3 = sim_total - baseline_total_kg
    st.metric(
        "Simulated 48h weight (new thresholds)",
        fmt_kg(sim_total),
        delta=f"{fmt_kg(abs(delta3))} {'below' if delta3 < 0 else 'above'} target",
        delta_color="inverse" if delta3 != 0 else "off",
        help="Total kg your adjusted Step 1 thresholds would flag as 48h.",
    )
with c4:
    match_pct = sim_total / baseline_total_kg * 100 if baseline_total_kg else 0
    st.metric(
        "Match vs target",
        f"{match_pct:.1f}%",
        help="100% = simulated 48h weight exactly matches the current baseline weight.",
    )

# ── Status banner ──────────────────────────────────────────────────────────────
diff_kg = sim_total - baseline_total_kg
if abs(diff_kg) < baseline_total_kg * 0.005:   # within 0.5%
    st.success("✅ Near-perfect match! Your new thresholds preserve the 48h weight volume.")
elif diff_kg < 0:
    st.warning(
        f"⬇️ Still **{fmt_kg(abs(diff_kg))} short** of the target. "
        "Try lowering one or more thresholds further."
    )
else:
    st.info(
        f"⬆️ Simulated weight is **{fmt_kg(diff_kg)} above target**. "
        "You could raise thresholds slightly to fine-tune."
    )

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Country breakdown", "📈 Sensitivity curves", "🎯 Auto-find thresholds"])

# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Country breakdown
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    rows = []
    for org, label in COUNTRY_MAP.items():
        country = label.split()[0]
        rows += [
            {"Country": country, "Category": "Actual 48h weight (Step1 + 3rd party)", "Weight (t)": baseline_kg[org] / 1000},
            {"Country": country, "Category": "Step 1 only @ baseline threshold",      "Weight (t)": step1_base_kg[org] / 1000},
            {"Country": country, "Category": "Step 1 only @ new threshold (simulated)","Weight (t)": sim_kg[org] / 1000},
        ]
    bar_df = pd.DataFrame(rows)

    fig = px.bar(
        bar_df, x="Country", y="Weight (t)", color="Category", barmode="group",
        color_discrete_map={
            "Actual 48h weight (Step1 + 3rd party)":           "#636efa",
            "Step 1 only @ baseline threshold":                 "#ef553b",
            "Step 1 only @ new threshold (simulated)":         "#00cc96",
        },
        title="Weight shipped in 48h per country (tonnes)",
        labels={"Weight (t)": "Weight (tonnes)"},
    )
    fig.update_layout(legend_title_text="", height=400, legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, use_container_width=True)

    # Detail table
    tbl_rows = []
    for org, label in COUNTRY_MAP.items():
        base  = BASE_THRESH[org]
        new_t = sim_thresholds[org]
        act   = baseline_kg[org]
        s1b   = step1_base_kg[org]
        sim   = sim_kg[org]
        gap_kg = sim - act
        tbl_rows.append({
            "Country":                        label,
            "Baseline threshold (kg)":        base,
            "New threshold (kg)":             new_t,
            "Reduction (kg)":                 base - new_t,
            "Reduction (%)":                  f"{(base - new_t) / base * 100:.1f}%",
            "Actual 48h weight (t)":          round(act / 1000, 1),
            "Step1-only @ baseline (t)":      round(s1b / 1000, 1),
            "3rd-party contribution (t)":     round((act - s1b) / 1000, 1),
            "Simulated 48h weight (t)":       round(sim / 1000, 1),
            "Gap vs target (t)":              round(gap_kg / 1000, 1),
        })

    tbl = pd.DataFrame(tbl_rows).set_index("Country")

    def color_gap(v):
        if not isinstance(v, (int, float)):
            return ""
        if abs(v) < 0.1:
            return "color: green"
        return "color: red"

    st.dataframe(
        tbl.style.map(color_gap, subset=["Gap vs target (t)"]),
        use_container_width=True,
    )
    st.caption(
        "**3rd-party contribution** = tonnes currently shipped 48h that Step 1 alone (at baseline threshold) would miss.  \n"
        "**Gap vs target** = 0 t means your new threshold exactly compensates for removing the 3rd-party check."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Sensitivity curves
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown(
        "Each curve shows how many **tonnes** would be shipped in 48h "
        "as you slide Step 1's threshold **down**. "
        "The dotted line is your target weight per country."
    )

    chosen_orgs = st.multiselect(
        "Countries to display",
        list(COUNTRY_MAP.keys()),
        default=list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    fig2 = go.Figure()

    for org in chosen_orgs:
        base   = BASE_THRESH[org]
        sub    = del_df[del_df["Sales Org"] == org]
        target = baseline_kg[org] / 1000   # convert to tonnes for display

        t_vals = np.arange(max(5, int(base * 0.05)), base + 1, max(5, int(base * 0.01)))
        w_vals = [
            sub.loc[sub["delivery_weight"] >= t, "delivery_weight"].sum() / 1000
            for t in t_vals
        ]

        fig2.add_trace(go.Scatter(
            x=t_vals, y=w_vals,
            mode="lines",
            name=COUNTRY_MAP[org].split()[0],
            line=dict(color=COLORS[org], width=2.5),
        ))

        # Current slider marker
        cur_thresh  = sim_thresholds[org]
        cur_weight  = sub.loc[sub["delivery_weight"] >= cur_thresh, "delivery_weight"].sum() / 1000
        fig2.add_trace(go.Scatter(
            x=[cur_thresh], y=[cur_weight],
            mode="markers",
            marker=dict(color=COLORS[org], size=12, symbol="circle-open", line=dict(width=2)),
            name=f"{COUNTRY_MAP[org].split()[0]} — slider position",
            showlegend=True,
        ))

        # Target line
        fig2.add_hline(
            y=target,
            line_dash="dot", line_color=COLORS[org], opacity=0.5,
            annotation_text=f"Target {COUNTRY_MAP[org].split()[0]} ({target:,.1f} t)",
            annotation_position="right",
        )

        # Baseline threshold marker
        fig2.add_vline(x=base, line_dash="dash", line_color=COLORS[org], opacity=0.25)

    fig2.update_layout(
        title="Sensitivity: threshold → weight shipped in 48h (per country)",
        xaxis_title="Step 1 threshold (kg)  ◀ lower = more weight in 48h",
        yaxis_title="Weight in 48h deliveries (tonnes)",
        height=500,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
    )
    fig2.update_xaxes(autorange="reversed")
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "**Reading the chart:** the x-axis is reversed so moving right = lower threshold = more weight captured.  \n"
        "Align the open circle (your current slider) with the dotted target line."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Auto-find thresholds
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown(
        "This tool **automatically calculates** the exact Step 1 threshold per country "
        "needed to preserve the current 48h **weight** without the 3rd-party check."
    )

    auto_rows = []
    for org, label in COUNTRY_MAP.items():
        base       = BASE_THRESH[org]
        target_kg  = baseline_kg[org]
        opt        = find_matching_threshold(del_df, org, target_kg)

        if opt is not None:
            red_kg  = base - opt
            red_pct = red_kg / base * 100
            auto_rows.append({
                "Country":                        label,
                "Current threshold (kg)":         base,
                "Recommended threshold (kg)":     int(np.floor(opt)),
                "Reduction (kg)":                 int(np.ceil(red_kg)),
                "Reduction (%)":                  f"{red_pct:.1f}%",
                "Target 48h weight (t)":          round(target_kg / 1000, 1),
                "Note": "✅ Lower threshold" if red_kg > 0 else "⚠️ No reduction needed",
            })
        else:
            auto_rows.append({
                "Country": label, "Current threshold (kg)": base,
                "Recommended threshold (kg)": "N/A", "Reduction (kg)": "N/A",
                "Reduction (%)": "N/A", "Target 48h weight (t)": round(target_kg / 1000, 1),
                "Note": "⚠️ Could not compute",
            })

    st.dataframe(pd.DataFrame(auto_rows).set_index("Country"), use_container_width=True)

    st.markdown("---")
    st.markdown("#### Weight distribution — see where the threshold falls")

    org_sel = st.selectbox(
        "Select country",
        list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    sub_hist = del_df[del_df["Sales Org"] == org_sel]
    base_t   = BASE_THRESH[org_sel]
    new_t    = sim_thresholds[org_sel]
    opt_t    = find_matching_threshold(del_df, org_sel, baseline_kg[org_sel])

    cap = max(sub_hist["delivery_weight"].quantile(0.99), base_t * 1.1)

    sc5_data = sub_hist[sub_hist["Shipping Conditions"] == 5]["delivery_weight"]
    sc2_data = sub_hist[sub_hist["Shipping Conditions"] == 2]["delivery_weight"]

    fig3 = go.Figure()
    fig3.add_trace(go.Histogram(
        x=sc2_data.clip(upper=cap), name="24h deliveries (SC=2)",
        marker_color="#aec7e8", opacity=0.7, nbinsx=70,
    ))
    fig3.add_trace(go.Histogram(
        x=sc5_data.clip(upper=cap), name="48h deliveries (SC=5)",
        marker_color="#ffbb78", opacity=0.9, nbinsx=70,
    ))

    fig3.add_vline(
        x=base_t, line_dash="dash", line_color="red", line_width=2,
        annotation_text=f"Current threshold ({base_t} kg)", annotation_position="top right",
    )
    if new_t != base_t:
        fig3.add_vline(
            x=new_t, line_dash="dot", line_color="green", line_width=2,
            annotation_text=f"Slider ({new_t} kg)", annotation_position="top left",
        )
    if opt_t is not None and abs(opt_t - base_t) > 1:
        fig3.add_vline(
            x=opt_t, line_dash="longdash", line_color="purple", line_width=2,
            annotation_text=f"Recommended ({opt_t:.0f} kg)", annotation_position="top",
        )

    fig3.update_layout(
        barmode="overlay",
        title=f"Delivery weight distribution — {COUNTRY_MAP[org_sel]}",
        xaxis_title="Delivery weight (kg)",
        yaxis_title="Number of deliveries",
        xaxis_range=[0, cap],
        height=430,
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig3, use_container_width=True)
    st.caption(
        "Orange bars = deliveries currently 48h (SC=5). "
        "Lowering the threshold (moving lines left) captures more weight from the blue bars into 48h."
    )

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Baseline thresholds: SE=700 kg · DK=1,500 kg · NO=2,500 kg · FI=2,500 kg  |  "
    "Weight aggregated at delivery level  |  SC=5 = 48h · SC=2 = 24h"
)


