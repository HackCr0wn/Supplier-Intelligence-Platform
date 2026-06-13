
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


def safe_index(options_list, value, default=0):
    try:
        return options_list.index(value)
    except ValueError:
        return default


def render():
    st.markdown('<p class="main-header">Market Material Index</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Indexed supplier prices vs commodity benchmarks</p>',
                unsafe_allow_html=True)

    ##Cached data
    @st.cache_data(ttl=300)
    def load_surcharge():
        df = pd.read_sql(text("""
            SELECT sm.part_number, sm.supplier, sm.destination_plant,
                   sm.material, sm.base_material, sm.year, sm.month,
                   sm.index_cost, sm.part_surcharge, sm.total_surcharge,
                   sm.quantity, sm.total_surcharge_cost,
                   mcm.commodity_code, cm.commodity_name AS market
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
    def load_comm_prices():
        return pd.read_sql(text("""
            SELECT cm.commodity_code, cm.commodity_name,
                   cp.price_date, cp.price AS price_usd
            FROM commodity_prices cp
            JOIN commodity_master cm ON cp.commodity_id = cm.commodity_id
        """), engine)

    data = load_surcharge()
    commodity_prices = load_comm_prices()

    if data.empty:
        st.warning("No surcharge data found.")
        return

    ##Session state
    FK = ["mi_base", "mi_material", "mi_supplier", "mi_market", "mi_part"]
    for k in FK:
        if k not in st.session_state:
            if k in ("mi_supplier", "mi_market"):
                st.session_state[k] = []
            else:
                st.session_state[k] = "All"

    def cross_filter(df, exclude):
        temp = df.copy()
        fmap = {
            "mi_base":     ("base_material",  st.session_state["mi_base"]),
            "mi_material": ("material",       st.session_state["mi_material"]),
            "mi_supplier": ("supplier",       st.session_state["mi_supplier"]),
            "mi_market":   ("market",         st.session_state["mi_market"]),
            "mi_part":     ("part_number",    st.session_state["mi_part"]),
        }
        for key, (col, val) in fmap.items():
            if key == exclude:
                continue
            if isinstance(val, list):
                if val:
                    temp = temp[temp[col].isin(val)]
            elif val != "All":
                temp = temp[temp[col] == val]
        return temp

    ##Options
    base_opts = sorted(cross_filter(data, "mi_base")["base_material"].dropna().unique().tolist())
    mat_opts  = sorted(cross_filter(data, "mi_material")["material"].dropna().unique().tolist())
    sup_opts  = sorted(cross_filter(data, "mi_supplier")["supplier"].dropna().unique().tolist())
    mkt_opts  = sorted(cross_filter(data, "mi_market")["market"].dropna().unique().tolist())
    part_opts = sorted(cross_filter(data, "mi_part")["part_number"].dropna().unique().tolist())

    # Validate
    if st.session_state["mi_base"] != "All" and st.session_state["mi_base"] not in base_opts:
        st.session_state["mi_base"] = "All"
    if st.session_state["mi_material"] != "All" and st.session_state["mi_material"] not in mat_opts:
        st.session_state["mi_material"] = "All"
    st.session_state["mi_supplier"] = [s for s in st.session_state["mi_supplier"] if s in sup_opts]
    st.session_state["mi_market"] = [m for m in st.session_state["mi_market"] if m in mkt_opts]
    if st.session_state["mi_part"] != "All" and st.session_state["mi_part"] not in part_opts:
        st.session_state["mi_part"] = "All"

    ##Sidebar filters
    bo = ["All"] + base_opts
    selected_base = st.sidebar.selectbox("Base Material", bo,
        index=safe_index(bo, st.session_state["mi_base"]))
    st.session_state["mi_base"] = selected_base

    mo = ["All"] + mat_opts
    selected_mat = st.sidebar.selectbox("Material", mo,
        index=safe_index(mo, st.session_state["mi_material"]))
    st.session_state["mi_material"] = selected_mat

    po = ["All"] + part_opts
    selected_part = st.sidebar.selectbox("Part Number", po,
        index=safe_index(po, st.session_state["mi_part"]))
    st.session_state["mi_part"] = selected_part

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Chart Lines**")

    default_mkts = st.session_state["mi_market"] or [m for m in mkt_opts if m != "Unmapped"][:3]
    selected_mkts = st.sidebar.multiselect("Markets", mkt_opts, default=default_mkts)
    st.session_state["mi_market"] = selected_mkts

    default_sups = st.session_state["mi_supplier"] or sup_opts[:3]
    selected_sups = st.sidebar.multiselect("Suppliers", sup_opts, default=default_sups)
    st.session_state["mi_supplier"] = selected_sups

    if not selected_sups and not selected_mkts:
        st.info("Select at least one supplier or market.")
        return

    ##Apply filters
    filtered = data.copy()
    if selected_base != "All":
        filtered = filtered[filtered["base_material"] == selected_base]
    if selected_mat != "All":
        filtered = filtered[filtered["material"] == selected_mat]
    if selected_part != "All":
        filtered = filtered[filtered["part_number"] == selected_part]

    if filtered.empty:
        st.warning("No data for selected filters.")
        return

    # Date range
    min_d, max_d = filtered["date"].min().date(), filtered["date"].max().date()
    if min_d >= max_d:
        st.warning("Not enough date range.")
        return

    date_range = st.sidebar.slider("Date Range", min_value=min_d, max_value=max_d,
        value=(min_d, max_d), format="MMM YYYY")

    filtered = filtered[
        (filtered["date"].dt.date >= date_range[0]) &
        (filtered["date"].dt.date <= date_range[1])
    ]

    if filtered.empty:
        st.warning("No data in date range.")
        return

    ##Chart
    fig = go.Figure()

    # Supplier lines
    if selected_sups:
        sup_data = filtered[filtered["supplier"].isin(selected_sups)]
        chart_data = sup_data.groupby(["supplier", "date"]).agg(
            avg_cost=("index_cost", "mean")
        ).reset_index().dropna(subset=["avg_cost"])

        for sup in selected_sups:
            sdf = chart_data[chart_data["supplier"] == sup].sort_values("date")
            if sdf.empty or sdf["avg_cost"].iloc[0] == 0:
                continue
            base = sdf["avg_cost"].iloc[0]
            sdf["idx"] = (sdf["avg_cost"] / base) * 100
            fig.add_trace(go.Scatter(
                x=sdf["date"], y=sdf["idx"], mode="lines+markers",
                name=f" {sup}", line=dict(width=2), marker=dict(size=4),
                hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>$%{customdata:.4f}/lb<extra></extra>",
                customdata=sdf["avg_cost"]
            ))

    # Market lines
    if selected_mkts and not commodity_prices.empty:
        mkt_map = data[["market", "commodity_code"]].drop_duplicates()
        mkt_map = mkt_map[mkt_map["market"] != "Unmapped"]
        mkt_code = dict(zip(mkt_map["market"], mkt_map["commodity_code"]))

        for mkt in selected_mkts:
            if mkt == "Unmapped":
                continue
            code = mkt_code.get(mkt)
            if not code:
                continue
            pr = commodity_prices[commodity_prices["commodity_code"] == code].copy()
            if pr.empty:
                continue
            pr["price_date"] = pd.to_datetime(pr["price_date"])
            pr = pr[(pr["price_date"].dt.date >= date_range[0]) &
                     (pr["price_date"].dt.date <= date_range[1])].sort_values("price_date")
            if pr.empty or pr["price_usd"].iloc[0] == 0:
                continue
            base = pr["price_usd"].iloc[0]
            pr["idx"] = (pr["price_usd"] / base) * 100
            fig.add_trace(go.Scatter(
                x=pr["price_date"], y=pr["idx"], mode="lines",
                name=f" {mkt}", line=dict(width=3, dash="dash"),
                hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>$%{customdata:.2f}<extra></extra>",
                customdata=pr["price_usd"]
            ))

    fig.add_hline(y=100, line_dash="dot", line_color="gray", opacity=0.5,
                  annotation_text="Base = 100", annotation_position="bottom right")

    title_parts = []
    if selected_base != "All": title_parts.append(selected_base)
    if selected_mat != "All": title_parts.append(selected_mat)
    title_label = " / ".join(title_parts) if title_parts else "All Materials"

    fig.update_layout(
        title=dict(text=f"Market Material Index — {title_label}",
                   font=dict(size=22, color="black", family="Arial")),
        xaxis=dict(title=dict(text="Date", font=dict(size=18, color="black", family="Arial Black")),
                   tickfont=dict(size=14, color="black")),
        yaxis=dict(title=dict(text="Index (Start = 100)", font=dict(size=18, color="black", family="Arial Black")),
                   tickfont=dict(size=14, color="black")),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        height=600, template="plotly_white",
    )

    st.plotly_chart(fig, use_container_width=True)

    # Summary
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base Material", selected_base if selected_base != "All" else "All")
    c2.metric("Material", selected_mat if selected_mat != "All" else f"{len(mat_opts)}")
    c3.metric("Suppliers", len(selected_sups))
    c4.metric("Markets", len([m for m in selected_mkts if m != "Unmapped"]))

    # Raw data
    with st.expander("View Raw Data"):
        disp = filtered[["date", "part_number", "supplier", "material",
                          "base_material", "market", "index_cost",
                          "part_surcharge", "total_surcharge", "quantity",
                          "total_surcharge_cost"]].sort_values(["date", "supplier"]).copy()
        disp["date"] = disp["date"].dt.strftime("%b %Y")
        st.dataframe(disp, use_container_width=True)

    with st.expander("Export"):
        csv = filtered.to_csv(index=False)
        st.download_button("Download CSV", csv, "market_index.csv", "text/csv")
