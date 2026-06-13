import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from utils.db import get_engine

st.set_page_config(page_title="Market Material Index", layout="wide")
st.title("📈 Market Material Index")

engine = get_engine()


##Cached data loaders 
@st.cache_data(ttl=300)
def load_surcharge_data():
    df = pd.read_sql(text("""
        SELECT sm.part_number, sm.supplier, sm.destination_plant,
               sm.material, sm.base_material, sm.year, sm.month,
               sm.index_cost, sm.part_surcharge, sm.total_surcharge,
               sm.quantity, sm.total_surcharge_cost,
               sm.surcharge_weight_lbs, sm.raw_material_base_cost,
               mcm.commodity_code,
               cm.commodity_name AS market
        FROM surcharge_monthly sm
        LEFT JOIN parts_master pm ON sm.part_number = pm.part_number
        LEFT JOIN material_commodity_map mcm ON pm.material_from_drawing = mcm.material_from_drawing
        LEFT JOIN commodity_master cm ON mcm.commodity_code = cm.commodity_code
    """), engine)
    df["supplier"] = df["supplier"].str.strip().str.replace(r'\s+INC$', '', regex=True)
    df["date"] = pd.to_datetime(
        df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2) + "-01"
    )
    df["market"] = df["market"].fillna("Unmapped")
    df["commodity_code"] = df["commodity_code"].fillna("NONE")
    return df


@st.cache_data(ttl=300)
def load_commodity_prices():
    return pd.read_sql(text("""
        SELECT cm.commodity_code, cm.commodity_name,
               cp.price_date, cp.price AS price_usd
        FROM commodity_prices cp
        JOIN commodity_master cm ON cp.commodity_id = cm.commodity_id
        ORDER BY cm.commodity_code, cp.price_date
    """), engine)


data = load_surcharge_data()
commodity_prices = load_commodity_prices()

if data.empty:
    st.warning("No surcharge data found.")
    st.stop()


##Helper 
def safe_index(options_list, value, default=0):
    try:
        return options_list.index(value)
    except ValueError:
        return default


##Session state
FILTER_KEYS = ["sel_base", "sel_material", "sel_supplier", "sel_market", "sel_part"]
for k in FILTER_KEYS:
    if k not in st.session_state:
        if k in ("sel_supplier", "sel_market"):
            st.session_state[k] = []
        else:
            st.session_state[k] = "All"


##Cross-filter
def cross_filter(df, exclude):
    temp = df.copy()
    filters = {
        "sel_base":     ("base_material",  st.session_state["sel_base"]),
        "sel_material": ("material",       st.session_state["sel_material"]),
        "sel_supplier": ("supplier",       st.session_state["sel_supplier"]),
        "sel_market":   ("market",         st.session_state["sel_market"]),
        "sel_part":     ("part_number",    st.session_state["sel_part"]),
    }
    for key, (col, val) in filters.items():
        if key == exclude:
            continue
        if isinstance(val, list):
            if val:
                temp = temp[temp[col].isin(val)]
        elif val != "All":
            temp = temp[temp[col] == val]
    return temp


##Compute cross-filtered options
base_opts = sorted(cross_filter(data, "sel_base")["base_material"].dropna().unique().tolist())
mat_opts  = sorted(cross_filter(data, "sel_material")["material"].dropna().unique().tolist())
sup_opts  = sorted(cross_filter(data, "sel_supplier")["supplier"].dropna().unique().tolist())
mkt_opts  = sorted(cross_filter(data, "sel_market")["market"].dropna().unique().tolist())
part_opts = sorted(cross_filter(data, "sel_part")["part_number"].dropna().unique().tolist())

# Validate stale selections
if st.session_state["sel_base"] != "All" and st.session_state["sel_base"] not in base_opts:
    st.session_state["sel_base"] = "All"
if st.session_state["sel_material"] != "All" and st.session_state["sel_material"] not in mat_opts:
    st.session_state["sel_material"] = "All"
st.session_state["sel_supplier"] = [s for s in st.session_state["sel_supplier"] if s in sup_opts]
st.session_state["sel_market"] = [m for m in st.session_state["sel_market"] if m in mkt_opts]
if st.session_state["sel_part"] != "All" and st.session_state["sel_part"] not in part_opts:
    st.session_state["sel_part"] = "All"


##Sidebar
st.sidebar.header("🔍 Filters")

# 1. Base Material
base_options = ["All"] + base_opts
selected_base = st.sidebar.selectbox(
    "🔩 Base Material", base_options,
    index=safe_index(base_options, st.session_state["sel_base"])
)
st.session_state["sel_base"] = selected_base

# 2. Material (sub-material)
mat_options = ["All"] + mat_opts
selected_material = st.sidebar.selectbox(
    "🧪 Material", mat_options,
    index=safe_index(mat_options, st.session_state["sel_material"])
)
st.session_state["sel_material"] = selected_material

# 3. Part Number
part_options = ["All"] + part_opts
selected_part = st.sidebar.selectbox(
    "🔧 Part Number", part_options,
    index=safe_index(part_options, st.session_state["sel_part"])
)
st.session_state["sel_part"] = selected_part

st.sidebar.markdown("---")
st.sidebar.subheader("📈 Chart Lines")

# 4. Market (multiselect — commodity benchmarks to show on chart)
default_mkts = st.session_state["sel_market"]
if not default_mkts:
    default_mkts = [m for m in mkt_opts if m != "Unmapped"][:3]

selected_markets = st.sidebar.multiselect(
    "🌍 Market (Commodity Lines)",
    mkt_opts,
    default=default_mkts,
    help="Select commodity benchmarks to overlay on chart"
)
st.session_state["sel_market"] = selected_markets

# 5. Supplier (multiselect — supplier lines to show on chart)
default_sups = st.session_state["sel_supplier"]
if not default_sups:
    default_sups = sup_opts[:3] if len(sup_opts) >= 3 else sup_opts

selected_suppliers = st.sidebar.multiselect(
    "🏭 Supplier (Supplier Lines)",
    sup_opts,
    default=default_sups,
    help="Select suppliers to show on chart"
)
st.session_state["sel_supplier"] = selected_suppliers

if not selected_suppliers and not selected_markets:
    st.info("Select at least one supplier or market from the sidebar.")
    st.stop()


##Apply base/material/part filters to data 
filtered = data.copy()

if selected_base != "All":
    filtered = filtered[filtered["base_material"] == selected_base]
if selected_material != "All":
    filtered = filtered[filtered["material"] == selected_material]
if selected_part != "All":
    filtered = filtered[filtered["part_number"] == selected_part]

if filtered.empty:
    st.warning("No data matches the selected filters.")
    st.stop()


##Date range slider
min_date = filtered["date"].min().date()
max_date = filtered["date"].max().date()

if min_date >= max_date:
    st.warning("Not enough date range for selected filters.")
    st.stop()

date_range = st.sidebar.slider(
    "📅 Date Range",
    min_value=min_date,
    max_value=max_date,
    value=(min_date, max_date),
    format="MMM YYYY"
)

filtered = filtered[
    (filtered["date"].dt.date >= date_range[0]) &
    (filtered["date"].dt.date <= date_range[1])
]

if filtered.empty:
    st.warning("No data in selected date range.")
    st.stop()


##Build chart
fig = go.Figure()

##SUPPLIER LINES (solid)
supplier_data = filtered[filtered["supplier"].isin(selected_suppliers)] if selected_suppliers else pd.DataFrame()

if not supplier_data.empty:
    chart_data = supplier_data.groupby(["supplier", "date"]).agg(
        avg_index_cost=("index_cost", "mean")
    ).reset_index().dropna(subset=["avg_index_cost"])

    for supplier in selected_suppliers:
        sdf = chart_data[chart_data["supplier"] == supplier].sort_values("date").copy()
        if sdf.empty or sdf["avg_index_cost"].iloc[0] == 0:
            continue

        base_val = sdf["avg_index_cost"].iloc[0]
        sdf["indexed"] = (sdf["avg_index_cost"] / base_val) * 100

        fig.add_trace(go.Scatter(
            x=sdf["date"], y=sdf["indexed"],
            mode="lines+markers",
            name=f"🏭 {supplier}",
            line=dict(width=2),
            marker=dict(size=5),
            hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>Cost: $%{customdata:.4f}/lb<extra></extra>",
            customdata=sdf["avg_index_cost"]
        ))


##MARKET LINES (dashed)
if selected_markets and not commodity_prices.empty:
    # Map market name → commodity_code
    market_to_code = data[["market", "commodity_code"]].drop_duplicates()
    market_to_code = market_to_code[market_to_code["market"] != "Unmapped"]
    market_code_map = dict(zip(market_to_code["market"], market_to_code["commodity_code"]))

    for market_name in selected_markets:
        if market_name == "Unmapped":
            continue

        comm_code = market_code_map.get(market_name)
        if not comm_code:
            continue

        prices = commodity_prices[commodity_prices["commodity_code"] == comm_code].copy()
        if prices.empty:
            continue

        prices["price_date"] = pd.to_datetime(prices["price_date"])
        prices = prices[
            (prices["price_date"].dt.date >= date_range[0]) &
            (prices["price_date"].dt.date <= date_range[1])
        ].sort_values("price_date")

        if prices.empty or prices["price_usd"].iloc[0] == 0:
            continue

        base_val = prices["price_usd"].iloc[0]
        prices["indexed"] = (prices["price_usd"] / base_val) * 100

        fig.add_trace(go.Scatter(
            x=prices["price_date"], y=prices["indexed"],
            mode="lines",
            name=f"🔷 {market_name}",
            line=dict(width=3, dash="dash"),
            hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>Price: $%{customdata:.2f}<extra></extra>",
            customdata=prices["price_usd"]
        ))


##Chart formatting
title_parts = []
if selected_base != "All":
    title_parts.append(selected_base)
if selected_material != "All":
    title_parts.append(selected_material)
if selected_part != "All":
    title_parts.append(f"Part {selected_part}")
title_label = " / ".join(title_parts) if title_parts else "All Materials"

fig.add_hline(y=100, line_dash="dot", line_color="gray", opacity=0.5,
              annotation_text="Base = 100", annotation_position="bottom right")

fig.update_layout(
    title=dict(
        text=f"Market Material Index — {title_label}",
        font=dict(size=22, color="black", family="Arial"),
    ),
    xaxis=dict(
        title=dict(
            text="Date",
            font=dict(size=18, color="black", family="Arial Black"),
        ),
        tickfont=dict(size=14, color="black", family="Arial"),
    ),
    yaxis=dict(
        title=dict(
            text="Index (Start = 100)",
            font=dict(size=18, color="black", family="Arial Black"),
        ),
        tickfont=dict(size=14, color="black", family="Arial"),
    ),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
    height=650,
    template="plotly_white",
)

st.plotly_chart(fig, use_container_width=True)

if not selected_markets or all(m == "Unmapped" for m in selected_markets):
    st.info("💡 Select a Market (Commodity) from the sidebar to overlay benchmark lines.")


##Summary stats
st.subheader("📊 Summary")
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Base Material", selected_base if selected_base != "All" else "All")
col2.metric("Material", selected_material if selected_material != "All" else f"{len(mat_opts)}")
col3.metric("Suppliers", len(selected_suppliers))
col4.metric("Markets", len([m for m in selected_markets if m != "Unmapped"]))
col5.metric("Parts", selected_part if selected_part != "All" else f"{len(filtered['part_number'].unique())}")


##Raw data table
with st.expander("📋 View Raw Data"):
    display_cols = ["date", "part_number", "supplier", "material",
                    "base_material", "market", "index_cost",
                    "part_surcharge", "total_surcharge", "quantity",
                    "total_surcharge_cost"]
    display_df = filtered[display_cols].sort_values(["date", "supplier"]).copy()
    display_df["date"] = display_df["date"].dt.strftime("%b %Y")
    st.dataframe(display_df, use_container_width=True)


##Export
with st.expander("📥 Export Data"):
    csv = filtered.to_csv(index=False)
    st.download_button("Download CSV", csv, "market_material_index.csv", "text/csv")