import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path

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

def fmt_t(kg):
    return f"{kg/1000:,.1f} t"

# ── Data loading ───────────────────────────────────────────────────────────────
DEFAULT_FILE = Path(__file__).parent / "NordicsExtract.xlsx"

@st.cache_data(show_spinner="Reading Excel file…")
def load_data(_source, cache_key: str):
    """Load from a file path or an uploaded file object; cache_key tells Streamlit what changed."""
    df_raw = pd.read_excel(_source, sheet_name="ZDELEXAS raw data Nordics")
    del_df = (
        df_raw
        .groupby(["Delivery", "Ship-To Party", "Delivery Date", "Sales Org", "Shipping Conditions"])
        .agg(delivery_weight=("Net Weight", "sum"))
        .reset_index()
    )
    del_df["Delivery Date"] = pd.to_datetime(del_df["Delivery Date"])
    return del_df


def compute_metrics(del_df: pd.DataFrame, thresholds: dict) -> dict:
    """
    For each country, compute:
      - target_kg  : weight currently shipped in 48h (SC=5) — what we want to preserve
      - thirdp_kg  : weight in SC=5 deliveries that are BELOW the current threshold
                     (these are added by the 3rd party; removing it loses them)
      - step1_kg   : weight flagged by Step 1 alone at the given (possibly new) threshold
                     = sum of delivery_weight for all deliveries with weight >= threshold,
                       regardless of current SC
      - gap_kg     : step1_kg - target_kg  (positive = over, negative = under)
    """
    result = {}
    for org, thresh in thresholds.items():
        sub       = del_df[del_df["Sales Org"] == org]
        base_t    = BASE_THRESH[org]

        # Total weight in 48h today (SC=5) — the preservation target
        target_kg = float(sub.loc[sub["Shipping Conditions"] == 5, "delivery_weight"].sum())

        # 3rd-party contribution: SC=5 deliveries that are BELOW the base threshold
        # (they are currently 48h only because the 3rd party flipped them)
        thirdp_kg = float(
            sub.loc[(sub["Shipping Conditions"] == 5) & (sub["delivery_weight"] < base_t),
                    "delivery_weight"].sum()
        )

        # Weight that Step 1 alone would flag at the NEW threshold
        step1_kg = float(sub.loc[sub["delivery_weight"] >= thresh, "delivery_weight"].sum())

        result[org] = {
            "target_kg":  target_kg,
            "thirdp_kg":  thirdp_kg,
            "step1_kg":   step1_kg,
            "gap_kg":     step1_kg - target_kg,
        }
    return result


def find_threshold_for_target(del_df: pd.DataFrame, org: str, target_kg: float) -> float | None:
    """
    Find the HIGHEST threshold (at or below the base) such that
    sum of delivery_weight for all deliveries >= threshold equals target_kg.
    
    Logic: sort all delivery weights descending. The flagged weight at threshold t
    equals the sum of all weights >= t. As t decreases, more deliveries are included
    and the sum grows. We binary-search for the t that hits target_kg.
    """
    sub     = del_df[del_df["Sales Org"] == org]
    base_t  = BASE_THRESH[org]
    weights = np.sort(sub["delivery_weight"].values)[::-1]  # descending
    cumsum  = np.cumsum(weights)

    # At each index i, threshold = weights[i] captures i+1 deliveries with total = cumsum[i]
    # We want smallest i such that cumsum[i] >= target_kg, and weights[i] <= base_t
    for i, (w, cs) in enumerate(zip(weights, cumsum)):
        if cs >= target_kg:
            if w <= base_t:
                return float(w)
            else:
                # We've hit the target but threshold is still above base — keep going
                continue
    return None  # target unreachable even at threshold → 0


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")

    # Option 1: load the default file bundled with the app
    use_default = DEFAULT_FILE.exists()
    if use_default:
        if st.button("📂 Load NordicsExtract.xlsx", use_container_width=True,
                     help="Load the NordicsExtract.xlsx file stored in the same folder as this script."):
            st.session_state["use_default_file"] = True
        if st.session_state.get("use_default_file"):
            st.success("✅ Using NordicsExtract.xlsx")

    # Option 2: upload a different file
    uploaded = st.file_uploader(
        "Or upload a different Excel file" if use_default else "📂 Upload the Excel file",
        type=["xlsx"],
        on_change=lambda: st.session_state.update({"use_default_file": False}),
    )

    st.markdown("---")
    st.subheader("Step 1 thresholds (kg)")
    st.caption(
        "**Lower** the threshold → more deliveries flagged 48h at order creation → "
        "more kg in 48h. Goal: lower enough to recover the weight that the 3rd-party "
        "check currently adds, so total 48h weight stays stable."
    )

    sim_thresholds = {}
    for org, label in COUNTRY_MAP.items():
        base = BASE_THRESH[org]
        sim_thresholds[org] = st.slider(
            label,
            min_value=max(1, int(base * 0.05)),
            max_value=base,          # can only go DOWN from baseline
            value=base,
            step=5,
            help=f"Current baseline: {base} kg. Slide LEFT to lower the threshold and capture more weight in 48h.",
        )

    st.markdown("---")
    st.caption("**SC=5** = 48h delivery  |  **SC=2** = 24h delivery")

# ── Main ───────────────────────────────────────────────────────────────────────
st.title("🚚 48h Delivery Threshold Simulator — Nordics")
st.markdown(
    "**Objective:** find the minimum Step 1 threshold reduction that keeps the total "
    "**weight shipped in 48h** stable after deactivating the 3rd-party hourly aggregation check.\n\n"
    "The 3rd party currently flags additional deliveries (below the Step 1 threshold) as 48h. "
    "Removing it loses that weight from 48h. Lowering Step 1's threshold captures more deliveries "
    "directly to compensate."
)

# Resolve data source: default file takes priority if the button was used
if st.session_state.get("use_default_file") and DEFAULT_FILE.exists():
    data_source = DEFAULT_FILE
    cache_key   = "default_NordicsExtract"
elif uploaded is not None:
    data_source = uploaded
    cache_key   = uploaded.name
else:
    st.info("👈 Click **Load NordicsExtract.xlsx** or upload a file in the sidebar to begin.")
    st.stop()

del_df = load_data(data_source, cache_key)
metrics = compute_metrics(del_df, sim_thresholds)

total_target  = sum(m["target_kg"]  for m in metrics.values())
total_thirdp  = sum(m["thirdp_kg"]  for m in metrics.values())
total_step1   = sum(m["step1_kg"]   for m in metrics.values())
total_gap     = total_step1 - total_target

# ── KPIs ───────────────────────────────────────────────────────────────────────
st.markdown("### 📊 Overall summary")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Current 48h weight (target)",
        fmt_t(total_target),
        help="Total weight in SC=5 deliveries today. This is what we want to preserve.",
    )
with c2:
    st.metric(
        "3rd-party contribution",
        fmt_t(total_thirdp),
        help="Weight in SC=5 deliveries that are below the Step 1 threshold — added by the 3rd party. "
             "This is what you lose if you deactivate it without adjusting Step 1.",
    )
with c3:
    st.metric(
        "Step 1 only @ new thresholds",
        fmt_t(total_step1),
        delta=f"{fmt_t(abs(total_gap))} {'above' if total_gap > 0 else 'below'} target",
        delta_color="inverse" if total_gap < 0 else ("off" if total_gap == 0 else "normal"),
        help="Weight that Step 1 alone would flag as 48h at your adjusted thresholds.",
    )
with c4:
    match_pct = total_step1 / total_target * 100 if total_target else 0
    st.metric("Match vs target", f"{match_pct:.1f}%",
              help="100% = Step 1 alone, at your new thresholds, captures exactly the current 48h weight.")

# ── Status banner ──────────────────────────────────────────────────────────────
if abs(total_gap) < total_target * 0.005:
    st.success("✅ Near-perfect match! Your thresholds recover the 3rd-party weight with Step 1 alone.")
elif total_gap < 0:
    st.warning(
        f"⬇️ Still **{fmt_t(abs(total_gap))} short** of target — mainly the 3rd-party contribution "
        f"({fmt_t(total_thirdp)}) isn't recovered yet. Lower one or more thresholds further."
    )
else:
    st.info(f"⬆️ Step 1 captures **{fmt_t(total_gap)} more** than the target. Raise thresholds slightly to fine-tune.")

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
        m = metrics[org]
        step1_at_base = float(
            del_df.loc[(del_df["Sales Org"] == org) & (del_df["delivery_weight"] >= BASE_THRESH[org]),
                       "delivery_weight"].sum()
        )
        rows += [
            {"Country": country, "Category": "Current 48h weight (SC=5 target)",        "Weight (t)": m["target_kg"]  / 1000},
            {"Country": country, "Category": "3rd-party contribution (below threshold)", "Weight (t)": m["thirdp_kg"]  / 1000},
            {"Country": country, "Category": "Step 1 only @ new threshold (simulated)",  "Weight (t)": m["step1_kg"]   / 1000},
        ]
    bar_df = pd.DataFrame(rows)

    fig = px.bar(
        bar_df, x="Country", y="Weight (t)", color="Category", barmode="group",
        color_discrete_map={
            "Current 48h weight (SC=5 target)":        "#636efa",
            "3rd-party contribution (below threshold)": "#ef553b",
            "Step 1 only @ new threshold (simulated)":  "#00cc96",
        },
        title="Weight shipped in 48h per country (tonnes)",
    )
    fig.update_layout(legend_title_text="", height=420, legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, use_container_width=True)

    tbl_rows = []
    for org, label in COUNTRY_MAP.items():
        m     = metrics[org]
        base  = BASE_THRESH[org]
        new_t = sim_thresholds[org]
        gap   = m["gap_kg"]
        tbl_rows.append({
            "Country":                          label,
            "Baseline threshold (kg)":          base,
            "New threshold (kg)":               new_t,
            "Reduction (kg)":                   base - new_t,
            "Reduction (%)":                    f"{(base - new_t)/base*100:.1f}%",
            "48h weight target (t)":            round(m["target_kg"]  / 1000, 1),
            "3rd-party contribution (t)":       round(m["thirdp_kg"]  / 1000, 1),
            "Step 1 simulated (t)":             round(m["step1_kg"]   / 1000, 1),
            "Gap vs target (t)":                round(gap / 1000, 1),
        })

    tbl = pd.DataFrame(tbl_rows).set_index("Country")

    def color_gap(v):
        if not isinstance(v, (int, float)):
            return ""
        return "color: green" if abs(v) < 0.1 else "color: red"

    st.dataframe(tbl.style.map(color_gap, subset=["Gap vs target (t)"]), use_container_width=True)
    st.caption(
        "**3rd-party contribution:** weight currently 48h only because the 3rd party flipped it "
        "(delivery weight is below the Step 1 threshold). This is what you need to recover by lowering the threshold.  \n"
        "**Gap vs target:** negative = still short, positive = over-captured, 0 = perfect."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Sensitivity curves
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown(
        "Each curve shows how much **weight** Step 1 alone would capture in 48h "
        "as you lower the threshold. The dotted line is the weight target per country. "
        "Move the slider left until the open circle meets the dotted line."
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
        target = metrics[org]["target_kg"] / 1000

        # Sweep thresholds from 5% of base down to base (x-axis reversed → looks like sliding left)
        t_vals = np.arange(max(5, int(base * 0.05)), base + 1, max(5, int(base * 0.01)))
        w_vals = [
            sub.loc[sub["delivery_weight"] >= t, "delivery_weight"].sum() / 1000
            for t in t_vals
        ]

        fig2.add_trace(go.Scatter(
            x=t_vals, y=w_vals, mode="lines",
            name=COUNTRY_MAP[org].split()[0],
            line=dict(color=COLORS[org], width=2.5),
        ))

        # Current slider position
        cur_t = sim_thresholds[org]
        cur_w = sub.loc[sub["delivery_weight"] >= cur_t, "delivery_weight"].sum() / 1000
        fig2.add_trace(go.Scatter(
            x=[cur_t], y=[cur_w], mode="markers",
            marker=dict(color=COLORS[org], size=12, symbol="circle-open", line=dict(width=2.5)),
            name=f"{COUNTRY_MAP[org].split()[0]} — slider",
            showlegend=True,
        ))

        # Target line
        fig2.add_hline(
            y=target, line_dash="dot", line_color=COLORS[org], opacity=0.5,
            annotation_text=f"Target {COUNTRY_MAP[org].split()[0]} ({target:,.1f} t)",
            annotation_position="right",
        )
        # Baseline threshold
        fig2.add_vline(x=base, line_dash="dash", line_color=COLORS[org], opacity=0.2)

    fig2.update_xaxes(autorange="reversed")
    fig2.update_layout(
        title="Step 1 threshold → weight captured in 48h (lower threshold = more weight)",
        xaxis_title="Step 1 threshold (kg)  ◀ slide left to capture more weight",
        yaxis_title="Weight in 48h (tonnes)",
        height=500,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "X-axis is reversed: moving **left** = lower threshold = more deliveries flagged = more weight in 48h.  \n"
        "Align the open circle (your slider) with the dotted target line to find the right threshold."
    )

# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Auto-find thresholds
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown(
        "Automatically calculates the **lowest threshold that still preserves the current 48h weight** "
        "for each country — i.e. the minimum reduction needed to compensate for removing the 3rd-party check."
    )

    auto_rows = []
    for org, label in COUNTRY_MAP.items():
        base      = BASE_THRESH[org]
        target_kg = metrics[org]["target_kg"]
        thirdp_kg = metrics[org]["thirdp_kg"]
        opt_t     = find_threshold_for_target(del_df, org, target_kg)

        if opt_t is not None:
            red_kg  = base - opt_t
            red_pct = red_kg / base * 100
            auto_rows.append({
                "Country":                      label,
                "Current threshold (kg)":       base,
                "Recommended threshold (kg)":   int(np.floor(opt_t)),
                "Reduction needed (kg)":        int(np.ceil(red_kg)),
                "Reduction needed (%)":         f"{red_pct:.1f}%",
                "48h weight target (t)":        round(target_kg / 1000, 1),
                "3rd-party weight to recover (t)": round(thirdp_kg / 1000, 1),
            })
        else:
            auto_rows.append({
                "Country": label,
                "Current threshold (kg)": base,
                "Recommended threshold (kg)": "N/A",
                "Reduction needed (kg)": "N/A",
                "Reduction needed (%)": "N/A",
                "48h weight target (t)": round(target_kg / 1000, 1),
                "3rd-party weight to recover (t)": round(thirdp_kg / 1000, 1),
            })

    st.dataframe(pd.DataFrame(auto_rows).set_index("Country"), use_container_width=True)

    st.markdown("---")
    st.markdown("#### Weight distribution — visualise the threshold shift")

    org_sel = st.selectbox(
        "Select country",
        list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    sub_hist = del_df[del_df["Sales Org"] == org_sel]
    base_t   = BASE_THRESH[org_sel]
    new_t    = sim_thresholds[org_sel]
    opt_t    = find_threshold_for_target(del_df, org_sel, metrics[org_sel]["target_kg"])
    cap      = max(sub_hist["delivery_weight"].quantile(0.99), base_t * 1.1)

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
        annotation_text=f"Current threshold ({base_t} kg)",
        annotation_position="top right",
    )
    if new_t != base_t:
        fig3.add_vline(
            x=new_t, line_dash="dot", line_color="green", line_width=2,
            annotation_text=f"Slider ({new_t} kg)",
            annotation_position="top left",
        )
    if opt_t is not None and abs(opt_t - base_t) > 1:
        fig3.add_vline(
            x=opt_t, line_dash="longdash", line_color="purple", line_width=2,
            annotation_text=f"Recommended ({opt_t:.0f} kg)",
            annotation_position="top",
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
        "**Orange** = currently 48h (SC=5). **Blue** = currently 24h (SC=2).  \n"
        "Moving the threshold line **left** (lower kg) brings more blue deliveries into 48h, "
        "recovering the weight the 3rd party currently contributes."
    )

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Baseline thresholds: SE=700 kg · DK=1,500 kg · NO=2,500 kg · FI=2,500 kg  |  "
    "Weight aggregated at delivery level  |  SC=5 = 48h · SC=2 = 24h"
)





