
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path

st.set_page_config(page_title="48h Delivery Threshold Simulator", layout="wide", page_icon="🚚")

COUNTRY_MAP = {
    "SE01": "Sweden 🇸🇪",
    "DK01": "Denmark 🇩🇰",
    "NO01": "Norway 🇳🇴",
    "FI01": "Finland 🇫🇮",
}
BASE_THRESH = {"SE01": 700, "DK01": 1500, "NO01": 2500, "FI01": 2500}
COLORS = {"SE01": "#1f77b4", "DK01": "#2ca02c", "NO01": "#ff7f0e", "FI01": "#9467bd"}
GROUP_KEYS_STEP2 = ["Shipment Number", "Ship-To Party", "Sales Org"]
DEFAULT_FILE = Path(__file__).parent / "NordicsExtract.xlsx"


def fmt_t(kg: float) -> str:
    return f"{kg/1000:,.1f} t"


@st.cache_data(show_spinner="Reading Excel file…")
def load_data(source, cache_key: str) -> pd.DataFrame:
    """
    Load raw line-level data.

    Correct business logic:
    - Step 1 is evaluated per order line (Net Weight).
    - Step 2 is evaluated after grouping by Shipment Number + Ship-To Party.
    """
    df_raw = pd.read_excel(source, sheet_name="ZDELEXAS raw data Nordics", engine="openpyxl")
    df = df_raw.copy()

    df["Net Weight"] = pd.to_numeric(df["Net Weight"], errors="coerce").fillna(0.0)
    if "Shipping Conditions" in df.columns:
        df["Shipping Conditions"] = pd.to_numeric(df["Shipping Conditions"], errors="coerce")
    else:
        df["Shipping Conditions"] = np.nan
    df["Delivery Date"] = pd.to_datetime(df["Delivery Date"], errors="coerce")

    # Step 1 uses raw line weight only
    df["line_weight"] = df["Net Weight"]

    # Step 2 uses grouped shipment + ship-to weight
    step2_group = (
        df.groupby(GROUP_KEYS_STEP2, dropna=False)
        .agg(
            shipment_shipto_weight=("Net Weight", "sum"),
            lines_in_group=("Net Weight", "size"),
            first_delivery_date=("Delivery Date", "min"),
        )
        .reset_index()
    )
    df = df.merge(step2_group, on=GROUP_KEYS_STEP2, how="left")
    return df


def compute_metrics(line_df: pd.DataFrame, thresholds: dict) -> dict:
    results = {}
    for org, sim_threshold in thresholds.items():
        base_threshold = BASE_THRESH[org]
        sub = line_df.loc[line_df["Sales Org"] == org].copy()

        # Step 2 target at baseline threshold
        step2_base_mask = sub["shipment_shipto_weight"] >= base_threshold
        target_kg = float(sub.loc[step2_base_mask, "line_weight"].sum())

        # Step 1 at baseline threshold
        step1_base_mask = sub["line_weight"] >= base_threshold
        step1_base_kg = float(sub.loc[step1_base_mask, "line_weight"].sum())

        # Weight added by Step 2 grouping only
        step2_only_mask = step2_base_mask & (~step1_base_mask)
        step2_only_kg = float(sub.loc[step2_only_mask, "line_weight"].sum())

        # Step 1 simulation with user slider threshold
        step1_sim_mask = sub["line_weight"] >= sim_threshold
        step1_sim_kg = float(sub.loc[step1_sim_mask, "line_weight"].sum())

        actual_sc5_kg = float(sub.loc[sub["Shipping Conditions"] == 5, "line_weight"].sum())
        qualifying_groups = int(sub.loc[step2_base_mask, GROUP_KEYS_STEP2].drop_duplicates().shape[0])

        results[org] = {
            "target_kg": target_kg,
            "step1_base_kg": step1_base_kg,
            "step2_only_kg": step2_only_kg,
            "step1_sim_kg": step1_sim_kg,
            "gap_kg": step1_sim_kg - target_kg,
            "actual_sc5_kg": actual_sc5_kg,
            "qualifying_groups": qualifying_groups,
        }
    return results


def find_threshold_for_target(line_df: pd.DataFrame, org: str, target_kg: float) -> float | None:
    """Highest Step 1 threshold (<= base) that still captures at least the target weight."""
    base_t = BASE_THRESH[org]
    sub = line_df.loc[line_df["Sales Org"] == org].copy()
    weights = np.sort(sub["line_weight"].dropna().astype(float).values)[::-1]
    if len(weights) == 0:
        return None

    cumsum = np.cumsum(weights)
    for w, cs in zip(weights, cumsum):
        if w > base_t:
            continue
        if cs >= target_kg:
            return float(w)
    return None


def build_sensitivity_df(line_df: pd.DataFrame, org: str) -> pd.DataFrame:
    base = BASE_THRESH[org]
    sub = line_df.loc[line_df["Sales Org"] == org].copy()
    min_t = max(5, int(base * 0.05))
    step = max(5, int(base * 0.01))
    thresholds = np.arange(min_t, base + 1, step)
    step1_kg = [float(sub.loc[sub["line_weight"] >= t, "line_weight"].sum()) / 1000 for t in thresholds]
    return pd.DataFrame({"threshold": thresholds, "step1_weight_t": step1_kg})


with st.sidebar:
    st.title("⚙️ Settings")

    use_default = DEFAULT_FILE.exists()
    if use_default:
        if st.button(
            "📂 Load NordicsExtract.xlsx",
            use_container_width=True,
            help="Load the NordicsExtract.xlsx file stored in the same folder as this script.",
        ):
            st.session_state["use_default_file"] = True
        if st.session_state.get("use_default_file"):
            st.success("✅ Using NordicsExtract.xlsx")

    uploaded = st.file_uploader(
        "Or upload a different Excel file" if use_default else "📂 Upload the Excel file",
        type=["xlsx"],
        on_change=lambda: st.session_state.update({"use_default_file": False}),
    )

    st.markdown("---")
    st.subheader("Step 1 thresholds (kg)")
    st.caption(
        "Step 1 is checked on each order line only. Lower the threshold to recover the 48h weight "
        "that Step 2 currently creates by grouping shipment + ship-to."
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
            help=(
                f"Current baseline: {base} kg. Step 1 uses line weight only. "
                "Move left to lower the threshold and capture more line weight in 48h."
            ),
        )

    st.markdown("---")
    st.caption("Corrected logic: Step 1 = order line only · Step 2 = grouped Shipment Number + Ship-To Party")


st.title("🚚 48h Delivery Threshold Simulator — Corrected Step 1 vs Step 2 Logic")
st.markdown(
    """
**Corrected business logic**

- **Step 1**: threshold checked on each **order line weight** individually.
- **Step 2**: weight first grouped by **Shipment Number + Ship-To Party**, then compared to the threshold.

This simulator shows how much Step 1 threshold reduction is needed to recover the 48h weight that Step 2 currently creates through grouping.
"""
)

if st.session_state.get("use_default_file") and DEFAULT_FILE.exists():
    data_source = DEFAULT_FILE
    cache_key = "default_NordicsExtract_corrected"
elif uploaded is not None:
    data_source = uploaded
    cache_key = uploaded.name
else:
    st.info("👈 Click **Load NordicsExtract.xlsx** or upload a file in the sidebar to begin.")
    st.stop()

line_df = load_data(data_source, cache_key)
metrics = compute_metrics(line_df, sim_thresholds)

total_target = sum(m["target_kg"] for m in metrics.values())
total_step2_only = sum(m["step2_only_kg"] for m in metrics.values())
total_step1_base = sum(m["step1_base_kg"] for m in metrics.values())
total_step1_sim = sum(m["step1_sim_kg"] for m in metrics.values())
total_sc5 = sum(m["actual_sc5_kg"] for m in metrics.values())
total_gap = total_step1_sim - total_target

st.markdown("### 📊 Overall summary")
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.metric(
        "Step 2 48h weight @ current thresholds",
        fmt_t(total_target),
        help=(
            "Corrected 48h target using Step 2 logic: raw lines are grouped by Shipment Number + "
            "Ship-To Party before comparing the grouped weight to the threshold."
        ),
    )
with c2:
    st.metric(
        "Step 1 alone @ current thresholds",
        fmt_t(total_step1_base),
        help="Current Step 1 coverage using line-by-line threshold checks only.",
    )
with c3:
    st.metric(
        "Step 2-only contribution",
        fmt_t(total_step2_only),
        help=(
            "Weight that gets 48h under Step 2 grouping but not under Step 1 at the current threshold, "
            "because the individual order lines are below the threshold."
        ),
    )
with c4:
    st.metric(
        "Step 1 @ new thresholds",
        fmt_t(total_step1_sim),
        delta=f"{fmt_t(abs(total_gap))} {'above' if total_gap > 0 else 'below'} Step 2 target",
        delta_color="inverse" if total_gap < 0 else ("off" if total_gap == 0 else "normal"),
        help="Simulated Step 1 coverage after lowering thresholds; still line-by-line only.",
    )
with c5:
    st.metric(
        "Actual SC=5 in source",
        fmt_t(total_sc5),
        help="Reference only: total source-file weight already marked as SC=5.",
    )

if total_target == 0:
    st.warning("No Step 2-qualified weight found at the baseline thresholds.")
elif abs(total_gap) < max(total_target * 0.005, 1.0):
    st.success("✅ Near-perfect match: Step 1 thresholds now recover the Step 2 48h target.")
elif total_gap < 0:
    st.warning(
        f"⬇️ Step 1 is still **{fmt_t(abs(total_gap))} short** of the Step 2 target. Lower one or more Step 1 thresholds further."
    )
else:
    st.info(
        f"⬆️ Step 1 captures **{fmt_t(total_gap)} more** than the Step 2 target. Raise thresholds slightly to fine-tune."
    )

st.divider()

tab1, tab2, tab3 = st.tabs(["📋 Country breakdown", "📈 Sensitivity curves", "🎯 Auto-find thresholds"])

with tab1:
    rows = []
    for org, label in COUNTRY_MAP.items():
        country = label.split()[0]
        m = metrics[org]
        rows += [
            {"Country": country, "Category": "Step 2 target (grouped shipment + ship-to)", "Weight (t)": m["target_kg"] / 1000},
            {"Country": country, "Category": "Step 1 @ current threshold (line only)", "Weight (t)": m["step1_base_kg"] / 1000},
            {"Country": country, "Category": "Step 1 @ new threshold (line only)", "Weight (t)": m["step1_sim_kg"] / 1000},
            {"Country": country, "Category": "Step 2-only contribution", "Weight (t)": m["step2_only_kg"] / 1000},
        ]

    bar_df = pd.DataFrame(rows)
    fig = px.bar(
        bar_df,
        x="Country",
        y="Weight (t)",
        color="Category",
        barmode="group",
        color_discrete_map={
            "Step 2 target (grouped shipment + ship-to)": "#636efa",
            "Step 1 @ current threshold (line only)": "#ef553b",
            "Step 1 @ new threshold (line only)": "#00cc96",
            "Step 2-only contribution": "#ab63fa",
        },
        title="48h weight by country using corrected Step 1 / Step 2 logic",
    )
    fig.update_layout(legend_title_text="", height=460, legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, use_container_width=True)

    tbl_rows = []
    for org, label in COUNTRY_MAP.items():
        m = metrics[org]
        base = BASE_THRESH[org]
        new_t = sim_thresholds[org]
        gap = m["gap_kg"]
        tbl_rows.append(
            {
                "Country": label,
                "Baseline threshold (kg)": base,
                "New Step 1 threshold (kg)": new_t,
                "Reduction (kg)": base - new_t,
                "Reduction (%)": f"{(base - new_t) / base * 100:.1f}%",
                "Step 2 target (t)": round(m["target_kg"] / 1000, 1),
                "Step 1 @ current (t)": round(m["step1_base_kg"] / 1000, 1),
                "Step 2-only contribution (t)": round(m["step2_only_kg"] / 1000, 1),
                "Step 1 @ new (t)": round(m["step1_sim_kg"] / 1000, 1),
                "Gap vs Step 2 target (t)": round(gap / 1000, 1),
                "Qualifying step-2 groups": m["qualifying_groups"],
                "Actual SC=5 (t)": round(m["actual_sc5_kg"] / 1000, 1),
            }
        )
    tbl = pd.DataFrame(tbl_rows).set_index("Country")

    def color_gap(v):
        if not isinstance(v, (int, float)):
            return ""
        return "color: green" if abs(v) < 0.1 else "color: red"

    st.dataframe(tbl.style.map(color_gap, subset=["Gap vs Step 2 target (t)"]), use_container_width=True)
    st.caption(
        "**Step 2 target** = group by Shipment Number + Ship-To Party, compare grouped weight to threshold.\n"
        "**Step 1** = compare each order line weight directly to threshold.\n"
        "**Step 2-only contribution** = weight that qualifies only because Step 2 groups multiple lines together."
    )

with tab2:
    st.markdown(
        "Each curve shows how much **weight Step 1 alone** captures when the Step 1 threshold is checked on "
        "**raw order line weight only**. The dotted line is the **Step 2 target** (grouped shipment + ship-to at the current threshold)."
    )
    chosen_orgs = st.multiselect(
        "Countries to display",
        list(COUNTRY_MAP.keys()),
        default=list(COUNTRY_MAP.keys()),
        format_func=lambda x: COUNTRY_MAP[x],
    )

    fig2 = go.Figure()
    for org in chosen_orgs:
        curve_df = build_sensitivity_df(line_df, org)
        target_t = metrics[org]["target_kg"] / 1000
        cur_t = sim_thresholds[org]
        cur_w = float(line_df.loc[(line_df["Sales Org"] == org) & (line_df["line_weight"] >= cur_t), "line_weight"].sum()) / 1000
        base_t = BASE_THRESH[org]

        fig2.add_trace(
            go.Scatter(
                x=curve_df["threshold"],
                y=curve_df["step1_weight_t"],
                mode="lines",
                name=COUNTRY_MAP[org].split()[0],
                line=dict(color=COLORS[org], width=2.5),
            )
        )
        fig2.add_trace(
            go.Scatter(
                x=[cur_t],
                y=[cur_w],
                mode="markers",
                marker=dict(color=COLORS[org], size=12, symbol="circle-open", line=dict(width=2.5)),
                name=f"{COUNTRY_MAP[org].split()[0]} — slider",
                showlegend=True,
            )
        )
        fig2.add_hline(
            y=target_t,
            line_dash="dot",
            line_color=COLORS[org],
            opacity=0.5,
            annotation_text=f"Step 2 target {COUNTRY_MAP[org].split()[0]} ({target_t:,.1f} t)",
            annotation_position="right",
        )
        fig2.add_vline(x=base_t, line_dash="dash", line_color=COLORS[org], opacity=0.2)

    fig2.update_xaxes(autorange="reversed")
    fig2.update_layout(
        title="Step 1 threshold → weight captured in 48h (per line only)",
        xaxis_title="Step 1 threshold (kg) ◀ slide left to capture more line weight",
        yaxis_title="Weight in 48h (tonnes)",
        height=520,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "X-axis is reversed: moving **left** = lower Step 1 threshold = more line weight captured.\n"
        "Use the dotted line as the Step 2 target you want Step 1 to recover."
    )

with tab3:
    st.markdown(
        "This calculates the **highest Step 1 threshold** (therefore the **minimum reduction**) that still allows "
        "Step 1, on a line-by-line basis, to recover the current Step 2 target."
    )

    auto_rows = []
    for org, label in COUNTRY_MAP.items():
        base = BASE_THRESH[org]
        target_kg = metrics[org]["target_kg"]
        step2_only_kg = metrics[org]["step2_only_kg"]
        opt_t = find_threshold_for_target(line_df, org, target_kg)
        if opt_t is not None:
            red_kg = base - opt_t
            red_pct = red_kg / base * 100
            auto_rows.append(
                {
                    "Country": label,
                    "Current threshold (kg)": base,
                    "Recommended Step 1 threshold (kg)": int(np.floor(opt_t)),
                    "Reduction needed (kg)": int(np.ceil(red_kg)),
                    "Reduction needed (%)": f"{red_pct:.1f}%",
                    "Step 2 target (t)": round(target_kg / 1000, 1),
                    "Step 2-only contribution (t)": round(step2_only_kg / 1000, 1),
                }
            )
        else:
            auto_rows.append(
                {
                    "Country": label,
                    "Current threshold (kg)": base,
                    "Recommended Step 1 threshold (kg)": "N/A",
                    "Reduction needed (kg)": "N/A",
                    "Reduction needed (%)": "N/A",
                    "Step 2 target (t)": round(target_kg / 1000, 1),
                    "Step 2-only contribution (t)": round(step2_only_kg / 1000, 1),
                }
            )

    st.dataframe(pd.DataFrame(auto_rows).set_index("Country"), use_container_width=True)

    st.markdown("---")
    st.markdown("#### Order-line weight distribution — visualise the corrected Step 1 threshold shift")
    org_sel = st.selectbox("Select country", list(COUNTRY_MAP.keys()), format_func=lambda x: COUNTRY_MAP[x], key="detail_country")

    sub_hist = line_df.loc[line_df["Sales Org"] == org_sel].copy()
    base_t = BASE_THRESH[org_sel]
    new_t = sim_thresholds[org_sel]
    opt_t = find_threshold_for_target(line_df, org_sel, metrics[org_sel]["target_kg"])
    cap = max(sub_hist["line_weight"].quantile(0.99), base_t * 1.1) if len(sub_hist) else base_t * 1.1

    step2_base_mask = sub_hist["shipment_shipto_weight"] >= base_t
    step2_target_lines = sub_hist.loc[step2_base_mask, "line_weight"]
    non_step2_lines = sub_hist.loc[~step2_base_mask, "line_weight"]

    fig3 = go.Figure()
    fig3.add_trace(
        go.Histogram(
            x=non_step2_lines.clip(upper=cap),
            name="Lines outside Step 2 target",
            marker_color="#aec7e8",
            opacity=0.7,
            nbinsx=70,
        )
    )
    fig3.add_trace(
        go.Histogram(
            x=step2_target_lines.clip(upper=cap),
            name="Lines inside Step 2 target",
            marker_color="#ffbb78",
            opacity=0.9,
            nbinsx=70,
        )
    )
    fig3.add_vline(
        x=base_t,
        line_dash="dash",
        line_color="red",
        line_width=2,
        annotation_text=f"Current Step 1 threshold ({base_t} kg)",
        annotation_position="top right",
    )
    if new_t != base_t:
        fig3.add_vline(
            x=new_t,
            line_dash="dot",
            line_color="green",
            line_width=2,
            annotation_text=f"Slider ({new_t} kg)",
            annotation_position="top left",
        )
    if opt_t is not None and abs(opt_t - base_t) > 1:
        fig3.add_vline(
            x=opt_t,
            line_dash="longdash",
            line_color="purple",
            line_width=2,
            annotation_text=f"Recommended ({opt_t:.0f} kg)",
            annotation_position="top",
        )
    fig3.update_layout(
        barmode="overlay",
        title=f"Order-line weight distribution — {COUNTRY_MAP[org_sel]}",
        xaxis_title="Order-line net weight (kg)",
        yaxis_title="Number of lines",
        xaxis_range=[0, cap],
        height=440,
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig3, use_container_width=True)
    st.caption(
        "**Orange** = order lines that belong to Step 2-qualified shipment + ship-to groups.\n"
        "**Blue** = order lines outside the Step 2 target.\n"
        "Lowering the Step 1 threshold moves more individual lines into 48h without any grouping."
    )

st.divider()
st.caption(
    "Baseline thresholds: SE=700 kg · DK=1,500 kg · NO=2,500 kg · FI=2,500 kg · "
    "Corrected logic: Step 1 = line weight only · Step 2 = grouped Shipment Number + Ship-To Party"
)
