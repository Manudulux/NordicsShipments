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
COUNTRY_MAP   = {"SE01": "Sweden 🇸🇪", "DK01": "Denmark 🇩🇰", "NO01": "Norway 🇳🇴", "FI01": "Finland 🇫🇮"}
BASE_THRESH   = {"SE01": 700, "DK01": 1500, "NO01": 2500, "FI01": 2500}
COLORS        = {"SE01": "#1f77b4", "DK01": "#2ca02c", "NO01": "#ff7f0e", "FI01": "#9467bd"}

# ── Data loading ───────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Reading Excel file…")
def load_data(file_bytes):
    df_raw = pd.read_excel(file_bytes, sheet_name="ZDELEXAS raw data Nordics")
    # Aggregate to delivery level: one row per delivery
    del_df = (
        df_raw
        .groupby(["Delivery", "Ship-To Party", "Delivery Date", "Sales Org", "Shipping Conditions"])
        .agg(delivery_weight=("Net Weight", "sum"))
        .reset_index()
    )
    del_df["Delivery Date"] = pd.to_datetime(del_df["Delivery Date"])
    return del_df


def count_flagged(del_df: pd.DataFrame, thresholds: dict) -> dict:
    """Count deliveries flagged as 48h (weight >= threshold) per Sales Org."""
    result = {}
    for org, thresh in thresholds.items():
        sub = del_df[del_df["Sales Org"] == org]
        result[org] = int((sub["delivery_weight"] >= thresh).sum())
    return result


def find_matching_threshold(del_df: pd.DataFrame, org: str, target_count: int) -> float | None:
    """Binary-search the threshold value that flags exactly target_count deliveries."""
    sub = del_df[del_df["Sales Org"] == org]["delivery_weight"].sort_values(ascending=False).values
    if target_count <= 0 or target_count > len(sub):
        return None
    return float(sub[target_count - 1])


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    uploaded = st.file_uploader("📂 Upload the Excel file", type=["xlsx"])

    st.markdown("---")
    st.subheader("Step 1 thresholds (kg)")
    st.caption(
        "Lower the threshold → more deliveries are flagged 48h at order creation.  \n"
        "Goal: lower enough to keep total 48h stable after removing the 3rd-party check."
    )

    sim_thresholds = {}
    for org, label in COUNTRY_MAP.items():
        base = BASE_THRESH[org]
        sim_thresholds[org] = st.slider(
            label,
            min_value=max(1, int(base * 0.05)),
            max_value=base,                         # can only go DOWN from baseline
            value=base,
            step=5,
            help=f"Baseline: {base} kg. Slide left to lower threshold and flag more deliveries as 48h.",
        )

    st.markdown("---")
    st.caption("**SC=5** = 48h delivery  |  **SC=2** = 24h delivery")

# ── Main content ───────────────────────────────────────────────────────────────
st.title("🚚 48h Delivery Threshold Simulator — Nordics")
st.markdown(
    "**Goal:** Find the minimum threshold reduction at Step 1 (order creation) "
    "that keeps the total number of 48h deliveries stable after deactivating the "
    "3rd-party hourly aggregation check."
)

if uploaded is None:
    st.info("👈 Upload the Excel file in the sidebar to begin.")
    st.stop()

del_df = load_data(uploaded.read())

# ── Compute key numbers ────────────────────────────────────────────────────────
# Baseline: actual SC=5 in data (step1 + 3rd party combined)
baseline_sc5 = {
    org: int((del_df[del_df["Sales Org"] == org]["Shipping Conditions"] == 5).sum())
    for org in BASE_THRESH
}
baseline_total = sum(baseline_sc5.values())

# Step1-only at original thresholds (no 3rd party)
step1_base_counts = count_flagged(del_df, BASE_THRESH)
step1_base_total  = sum(step1_base_counts.values())

# 3rd-party contribution = baseline - step1-only (at original thresholds)
gap = {org: baseline_sc5[org] - step1_base_counts[org] for org in BASE_THRESH}
# Note: gap can be negative if step1 at baseline flags more than actual SC=5
# (real system may filter some; we show it as-is)

# Simulated: step1-only with user thresholds
sim_counts = count_flagged(del_df, sim_thresholds)
sim_total  = sum(sim_counts.values())

# ── Top KPIs ───────────────────────────────────────────────────────────────────
st.markdown("### 📊 Overall summary")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Current 48h deliveries",
        f"{baseline_total:,}",
        help="Actual SC=5 in your data (Step 1 + 3rd-party combined)",
    )
with c2:
    st.metric(
        "Step 1 only @ baseline threshold",
        f"{step1_base_total:,}",
        delta=f"{step1_base_total - baseline_total:+,} vs actual",
        delta_color="off",
        help="How many deliveries Step 1 alone flags at the current (unmodified) thresholds",
    )
with c3:
    delta_sim = sim_total - baseline_total
    st.metric(
        "Simulated 48h (new thresholds)",
        f"{sim_total:,}",
        delta=f"{delta_sim:+,} vs target",
        delta_color="inverse" if delta_sim != 0 else "off",
        help="How many deliveries your adjusted Step 1 thresholds would flag as 48h",
    )
with c4:
    match_pct = sim_total / baseline_total * 100 if baseline_total else 0
    color = "normal" if abs(match_pct - 100) < 5 else "off"
    st.metric(
        "Match vs target",
        f"{match_pct:.1f}%",
        help="100% = simulated 48h count exactly matches current baseline",
    )

# ── Progress bar showing how close we are ─────────────────────────────────────
diff_abs = abs(sim_total - baseline_total)
if diff_abs == 0:
    st.success("✅ Perfect match! Your new thresholds replicate the current 48h volume exactly.")
elif sim_total < baseline_total:
    still_needed = baseline_total - sim_total
    st.warning(
        f"⬇️ **{still_needed:,} more deliveries** need to be captured. "
        "Try lowering one or more thresholds further."
    )
else:
    over = sim_total - baseline_total
    st.info(
        f"⬆️ Simulated count is **{over:,} above target**. "
        "You could raise thresholds slightly to fine-tune."
    )

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 Country breakdown", "📈 Sensitivity curves", "🎯 Auto-find thresholds"])

# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Country breakdown
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    # Bar chart
    rows = []
    for org, label in COUNTRY_MAP.items():
        country = label.split()[0]
        rows += [
            {"Country": country, "Category": "Actual 48h (Step1 + 3rd party)", "Count": baseline_sc5[org]},
            {"Country": country, "Category": "Step 1 only @ baseline threshold", "Count": step1_base_counts[org]},
            {"Country": country, "Category": "Step 1 only @ new threshold (simulated)", "Count": sim_counts[org]},
        ]
    bar_df = pd.DataFrame(rows)

    fig = px.bar(
        bar_df, x="Country", y="Count", color="Category", barmode="group",
        color_discrete_map={
            "Actual 48h (Step1 + 3rd party)":          "#636efa",
            "Step 1 only @ baseline threshold":         "#ef553b",
            "Step 1 only @ new threshold (simulated)":  "#00cc96",
        },
        title="48h delivery counts per country",
    )
    fig.update_layout(legend_title_text="", height=400, legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, use_container_width=True)

    # Detail table
    tbl_rows = []
    for org, label in COUNTRY_MAP.items():
        base  = BASE_THRESH[org]
        new_t = sim_thresholds[org]
        act   = baseline_sc5[org]
        s1b   = step1_base_counts[org]
        sim   = sim_counts[org]
        tbl_rows.append({
            "Country":                   label,
            "Baseline threshold (kg)":   base,
            "New threshold (kg)":        new_t,
            "Reduction (kg)":            base - new_t,
            "Reduction (%)":             f"{(base - new_t) / base * 100:.1f}%",
            "Actual 48h (target)":       act,
            "Step1-only @ baseline":     s1b,
            "3rd-party contribution":    act - s1b,
            "Simulated 48h":             sim,
            "Gap vs target":             sim - act,
        })

    tbl = pd.DataFrame(tbl_rows).set_index("Country")
    st.dataframe(
        tbl.style.applymap(
            lambda v: "color: green" if isinstance(v, int) and v == 0 else
                      "color: red"   if isinstance(v, int) and v != 0 else "",
            subset=["Gap vs target"],
        ),
        use_container_width=True,
    )

    st.caption(
        "**3rd-party contribution** = deliveries currently 48h that Step 1 alone (at baseline threshold) would miss.  \n"
        "**Gap vs target** = 0 means your new threshold exactly compensates for removing the 3rd-party check."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Sensitivity curves
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown(
        "Each curve shows how many deliveries would be flagged as 48h "
        "as you slide Step 1's threshold **down** from the current baseline. "
        "The dashed blue line is your target (current total SC=5)."
    )

    chosen_orgs = st.multiselect(
        "Countries to display",
        list(COUNTRY_MAP.keys()),
        default=list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    fig2 = go.Figure()

    for org in chosen_orgs:
        base  = BASE_THRESH[org]
        sub   = del_df[del_df["Sales Org"] == org]
        target = baseline_sc5[org]

        # Sweep from 5% to 100% of baseline threshold
        t_vals  = np.arange(max(5, int(base * 0.05)), base + 1, max(5, int(base * 0.01)))
        c_vals  = [(sub["delivery_weight"] >= t).sum() for t in t_vals]

        fig2.add_trace(go.Scatter(
            x=t_vals, y=c_vals,
            mode="lines",
            name=COUNTRY_MAP[org].split()[0],
            line=dict(color=COLORS[org], width=2.5),
        ))

        # Mark the current slider position
        cur_thresh = sim_thresholds[org]
        cur_count  = (sub["delivery_weight"] >= cur_thresh).sum()
        fig2.add_trace(go.Scatter(
            x=[cur_thresh], y=[cur_count],
            mode="markers",
            marker=dict(color=COLORS[org], size=12, symbol="circle-open", line=dict(width=2)),
            name=f"{COUNTRY_MAP[org].split()[0]} — current slider",
            showlegend=True,
        ))

        # Target line per country
        fig2.add_hline(
            y=target,
            line_dash="dot", line_color=COLORS[org], opacity=0.4,
            annotation_text=f"Target {COUNTRY_MAP[org].split()[0]} ({target})",
            annotation_position="right",
        )

        # Vertical line at baseline threshold
        fig2.add_vline(
            x=base, line_dash="dash", line_color=COLORS[org], opacity=0.3,
        )

    fig2.update_layout(
        title="Sensitivity: threshold → 48h delivery count (per country)",
        xaxis_title="Step 1 threshold (kg)  ◀ lower = more 48h deliveries",
        yaxis_title="Number of 48h deliveries",
        height=500,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
    )
    fig2.update_xaxes(autorange="reversed")   # lower threshold on the right = makes direction intuitive
    st.plotly_chart(fig2, use_container_width=True)

    st.caption(
        "**Reading the chart:** move right-to-left (lower threshold) → count rises. "
        "The open circle marks your current slider position. "
        "Align the circle with the dotted target line to find the right threshold."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Auto-find thresholds
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown(
        "This tool **automatically calculates** the exact Step 1 threshold per country "
        "needed to replicate the current 48h count without the 3rd-party check. "
        "Use this as your recommended starting point."
    )

    st.markdown("#### Results")

    auto_rows = []
    for org, label in COUNTRY_MAP.items():
        base   = BASE_THRESH[org]
        target = baseline_sc5[org]
        opt    = find_matching_threshold(del_df, org, target)

        if opt is not None:
            red_kg  = base - opt
            red_pct = red_kg / base * 100
            auto_rows.append({
                "Country":                   label,
                "Current threshold (kg)":    base,
                "Recommended threshold (kg)": int(np.floor(opt)),
                "Reduction (kg)":            int(np.ceil(red_kg)),
                "Reduction (%)":             f"{red_pct:.1f}%",
                "Target 48h count":          target,
                "Note": "✅ Lowers threshold" if red_kg > 0 else "⚠️ Needs to raise threshold",
            })
        else:
            auto_rows.append({
                "Country": label, "Current threshold (kg)": base,
                "Recommended threshold (kg)": "N/A", "Reduction (kg)": "N/A",
                "Reduction (%)": "N/A", "Target 48h count": target, "Note": "⚠️ Could not compute",
            })

    st.dataframe(pd.DataFrame(auto_rows).set_index("Country"), use_container_width=True)

    st.markdown("---")
    st.markdown(
        "#### Weight distribution — see where the threshold falls"
    )
    org_sel = st.selectbox(
        "Select country",
        list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    sub_hist = del_df[del_df["Sales Org"] == org_sel]
    base_t   = BASE_THRESH[org_sel]
    new_t    = sim_thresholds[org_sel]
    opt_t    = find_matching_threshold(del_df, org_sel, baseline_sc5[org_sel])

    # Cap x-axis at 99th percentile for readability
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

    # Vertical lines
    fig3.add_vline(
        x=base_t, line_dash="dash", line_color="red", line_width=2,
        annotation_text=f"Current threshold ({base_t} kg)", annotation_position="top right",
    )
    if new_t != base_t:
        fig3.add_vline(
            x=new_t, line_dash="dot", line_color="green", line_width=2,
            annotation_text=f"Slider ({new_t} kg)", annotation_position="top left",
        )
    if opt_t and abs(opt_t - base_t) > 1:
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
        "Orange bars = deliveries currently flagged as 48h (SC=5). "
        "Move the **green threshold line** leftward (lower kg) to capture more of the blue bars as 48h."
    )

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Baseline thresholds: SE=700 kg · DK=1,500 kg · NO=2,500 kg · FI=2,500 kg  |  "
    "Weight aggregated at delivery level  |  SC=5 = 48h · SC=2 = 24h"
)
