
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


# Shared data loaders
@st.cache_data(ttl=300)
def load_data():
    df = pd.read_sql(text("""
        SELECT sm.part_number, sm.supplier, sm.material, sm.base_material,
               sm.year, sm.month, sm.index_cost,
               sm.part_surcharge, sm.total_surcharge,
               sm.total_surcharge_cost, sm.quantity,
               sm.surcharge_weight_lbs, sm.raw_material_base_cost,
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
def load_commodity_prices():
    return pd.read_sql(text("""
        SELECT cm.commodity_code, cm.commodity_name,
               cp.price_date, cp.price AS price_usd
        FROM commodity_prices cp
        JOIN commodity_master cm ON cp.commodity_id = cm.commodity_id
    """), engine)


@st.cache_data(ttl=300)
def load_classification():
    return pd.read_sql(text("""
        SELECT material_from_drawing, base_material_name,
               primary_commodity, secondary_commodity, confidence
        FROM material_classification_ai
    """), engine)


COLORS = [
    "#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6",
    "#EC4899", "#06B6D4", "#F97316", "#6366F1", "#14B8A6",
    "#E11D48", "#7C3AED", "#0EA5E9", "#D97706", "#059669",
]


def render():
    st.markdown('<p class="main-header">🧪 Material Analysis</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Compare materials side-by-side or deep-dive into one</p>',
                unsafe_allow_html=True)

    data = load_data()
    commodity_prices = load_commodity_prices()
    classifications = load_classification()

    if data.empty:
        st.warning("No surcharge data found.")
        return

    # Two tabs
    tab1, tab2 = st.tabs(["Compare Materials", "Individual Explorer"])


    # TAB 1: COMPARE MATERIALS
    with tab1:
        all_materials = sorted(data["base_material"].dropna().unique().tolist())
        all_suppliers = sorted(data["supplier"].dropna().unique().tolist())
        all_markets = sorted(data[data["market"] != "Unmapped"]["market"].dropna().unique().tolist())

        st.markdown("### What do you want to compare?")
        st.caption("Type to search — select materials, suppliers, and market benchmarks")

        col1, col2 = st.columns(2)
        with col1:
            selected_materials = st.multiselect(
                "Materials",
                all_materials, default=[],
                placeholder="Type a material name...",
                key="cmp_materials"
            )
        with col2:
            selected_suppliers = st.multiselect(
                "Suppliers (leave empty for all)",
                all_suppliers, default=[],
                placeholder="Type a supplier name...",
                key="cmp_suppliers"
            )

        selected_markets = st.multiselect(
            "Market Benchmarks",
            all_markets, default=[],
            placeholder="Type a commodity name...",
            key="cmp_markets"
        )

       
        # Filter
        filtered = data[data["base_material"].isin(selected_materials)]
        if selected_suppliers:
            filtered = filtered[filtered["supplier"].isin(selected_suppliers)]

        if filtered.empty:
            st.warning("No data for selected combination.")
            return

        # Date range
        min_d, max_d = filtered["date"].min().date(), filtered["date"].max().date()
        if min_d >= max_d:
            st.warning("Not enough date range.")
            return

        date_range = st.slider("Date Range", min_value=min_d, max_value=max_d,
                                value=(min_d, max_d), format="MMM YYYY", key="cmp_dates")

        filtered = filtered[
            (filtered["date"].dt.date >= date_range[0]) &
            (filtered["date"].dt.date <= date_range[1])
        ]

        if filtered.empty:
            st.warning("No data in date range.")
            return

        # Comparison Chart
        fig = go.Figure()

        chart_data = filtered.groupby(["base_material", "supplier", "date"]).agg(
            avg_cost=("index_cost", "mean")
        ).reset_index().dropna(subset=["avg_cost"])

        color_idx = 0
        for mat in selected_materials:
            mat_data = chart_data[chart_data["base_material"] == mat]
            for sup in mat_data["supplier"].unique():
                sdf = mat_data[mat_data["supplier"] == sup].sort_values("date")
                if sdf.empty or sdf["avg_cost"].iloc[0] == 0:
                    continue
                base = sdf["avg_cost"].iloc[0]
                sdf["indexed"] = (sdf["avg_cost"] / base) * 100

                fig.add_trace(go.Scatter(
                    x=sdf["date"], y=sdf["indexed"],
                    mode="lines+markers",
                    name=f"{mat} — {sup}",
                    line=dict(width=2, color=COLORS[color_idx % len(COLORS)]),
                    marker=dict(size=4),
                    hovertemplate=(
                        f"<b>{mat}</b><br>Supplier: {sup}<br>"
                        "%{x|%b %Y}<br>Index: %{y:.1f}<br>"
                        "Cost: $%{customdata:.4f}/lb<extra></extra>"
                    ),
                    customdata=sdf["avg_cost"]
                ))
                color_idx += 1

        # Market benchmarks
        if selected_markets and not commodity_prices.empty:
            mkt_map = data[["market", "commodity_code"]].drop_duplicates()
            mkt_map = mkt_map[mkt_map["market"] != "Unmapped"]
            mkt_code = dict(zip(mkt_map["market"], mkt_map["commodity_code"]))

            for mkt in selected_markets:
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
                pr["indexed"] = (pr["price_usd"] / base) * 100

                fig.add_trace(go.Scatter(
                    x=pr["price_date"], y=pr["indexed"],
                    mode="lines",
                    name=f" {mkt}",
                    line=dict(width=3, dash="dash", color="#1F2937"),
                    hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>$%{customdata:.2f}<extra></extra>",
                    customdata=pr["price_usd"]
                ))

        fig.add_hline(y=100, line_dash="dot", line_color="gray", opacity=0.5,
                      annotation_text="Base = 100", annotation_position="bottom right")

        title = " vs ".join(selected_materials[:3])
        if len(selected_materials) > 3:
            title += f" + {len(selected_materials) - 3} more"

        fig.update_layout(
            title=dict(text=f"Material Comparison — {title}",
                       font=dict(size=22, color="black", family="Arial")),
            xaxis=dict(title=dict(text="Date", font=dict(size=18, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            yaxis=dict(title=dict(text="Index (Start = 100)", font=dict(size=18, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5,
                        font=dict(size=10)),
            height=650, template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Summary table
        with st.expander("Summary Statistics"):
            summary = filtered.groupby(["base_material", "supplier"]).agg(
                avg_index=("index_cost", "mean"),
                min_index=("index_cost", "min"),
                max_index=("index_cost", "max"),
                total_surcharge=("total_surcharge_cost", "sum"),
                total_qty=("quantity", "sum"),
                months=("month", "nunique")
            ).reset_index().round(4)

            st.dataframe(summary.style.format({
                "avg_index": "${:.4f}", "min_index": "${:.4f}", "max_index": "${:.4f}",
                "total_surcharge": "${:,.2f}", "total_qty": "{:,.0f}",
            }), use_container_width=True, hide_index=True)

        with st.expander(" Export"):
            csv = filtered.to_csv(index=False)
            st.download_button("Download CSV", csv, "material_comparison.csv", "text/csv")

    # TAB 2: INDIVIDUAL EXPLORER
    
    with tab2:
        all_mats = sorted(data["base_material"].dropna().unique().tolist())

        # Search + radio selector
        search = st.text_input("Search material", "", placeholder="Type to filter...",
                               key="exp_search")

        if search:
            filtered_mats = [m for m in all_mats if search.lower() in m.lower()]
        else:
            filtered_mats = all_mats

        if not filtered_mats:
            st.warning("No materials match your search.")
            return

        selected_mat = st.selectbox("Select a material", filtered_mats, key="exp_mat")

        if not selected_mat:
            return

        mat_data = data[data["base_material"] == selected_mat]
        if mat_data.empty:
            st.warning(f"No data for {selected_mat}.")
            return

        #Info cards
        suppliers_count = mat_data["supplier"].nunique()
        parts_count = mat_data["part_number"].nunique()
        total_surcharge = mat_data["total_surcharge_cost"].sum()
        total_qty = mat_data["quantity"].sum()
        market = mat_data["market"].mode().iloc[0] if not mat_data["market"].mode().empty else "Unmapped"
        commodity_code = mat_data["commodity_code"].mode().iloc[0] if not mat_data["commodity_code"].mode().empty else "NONE"

        class_info = classifications[
            classifications["base_material_name"].str.upper() == selected_mat.upper()
        ]
        base_name = class_info["base_material_name"].iloc[0] if not class_info.empty else "—"
        primary_comm = class_info["primary_commodity"].iloc[0] if not class_info.empty else "—"

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Material", selected_mat)
        c2.metric("Base Name", base_name)
        c3.metric("Suppliers", suppliers_count)
        c4.metric("Parts", parts_count)
        c5.metric("Commodity", primary_comm)

        c6, c7, c8 = st.columns(3)
        c6.metric("Total Surcharge", f"${total_surcharge:,.2f}")
        c7.metric("Total Quantity", f"{total_qty:,.0f}")
        c8.metric("Market", market)

        st.markdown("---")

        #Chart 1: Indexed Supplier Price Trends
        st.subheader("Supplier Price Trends (Indexed)")

        chart_data = mat_data.groupby(["supplier", "date"]).agg(
            avg_cost=("index_cost", "mean")
        ).reset_index().dropna(subset=["avg_cost"])

        available_sups = sorted(chart_data["supplier"].unique().tolist())
        selected_sups = st.multiselect(
            "Toggle suppliers on/off", available_sups,
            default=available_sups[:5], key="exp_suppliers"
        )

        fig = go.Figure()

        for i, sup in enumerate(selected_sups):
            sdf = chart_data[chart_data["supplier"] == sup].sort_values("date")
            if sdf.empty or sdf["avg_cost"].iloc[0] == 0:
                continue
            base = sdf["avg_cost"].iloc[0]
            sdf["indexed"] = (sdf["avg_cost"] / base) * 100

            fig.add_trace(go.Scatter(
                x=sdf["date"], y=sdf["indexed"],
                mode="lines+markers",
                name=sup,
                line=dict(width=2, color=COLORS[i % len(COLORS)]),
                marker=dict(size=4),
                hovertemplate=(
                    f"<b>{sup}</b><br>%{{x|%b %Y}}<br>"
                    "Index: %{y:.1f}<br>Cost: $%{customdata:.4f}/lb<extra></extra>"
                ),
                customdata=sdf["avg_cost"]
            ))

        # Commodity benchmark
        if commodity_code != "NONE" and not commodity_prices.empty:
            pr = commodity_prices[commodity_prices["commodity_code"] == commodity_code].copy()
            if not pr.empty:
                pr["price_date"] = pd.to_datetime(pr["price_date"])
                min_d = mat_data["date"].min()
                max_d = mat_data["date"].max()
                pr = pr[(pr["price_date"] >= min_d) & (pr["price_date"] <= max_d)].sort_values("price_date")
                if not pr.empty and pr["price_usd"].iloc[0] != 0:
                    base = pr["price_usd"].iloc[0]
                    pr["indexed"] = (pr["price_usd"] / base) * 100
                    fig.add_trace(go.Scatter(
                        x=pr["price_date"], y=pr["indexed"],
                        mode="lines",
                        name=f"🔷 {market} (benchmark)",
                        line=dict(width=3, dash="dash", color="#1F2937"),
                        hovertemplate="%{x|%b %Y}<br>Index: %{y:.1f}<br>$%{customdata:.2f}<extra></extra>",
                        customdata=pr["price_usd"]
                    ))

        fig.add_hline(y=100, line_dash="dot", line_color="gray", opacity=0.5,
                      annotation_text="Base = 100", annotation_position="bottom right")

        fig.update_layout(
            title=dict(text=f"{selected_mat} — Supplier Price Index",
                       font=dict(size=20, color="black", family="Arial")),
            xaxis=dict(title=dict(text="Date", font=dict(size=18, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            yaxis=dict(title=dict(text="Index (Start = 100)", font=dict(size=18, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
            height=550, template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)

        #Chart 2: Monthly Surcharge Bar
        st.subheader("Monthly Surcharge Cost")

        monthly = mat_data.groupby(["date", "supplier"]).agg(
            surcharge=("total_surcharge_cost", "sum")
        ).reset_index()

        fig2 = go.Figure()
        for i, sup in enumerate(selected_sups):
            sdf = monthly[monthly["supplier"] == sup].sort_values("date")
            if sdf.empty:
                continue
            fig2.add_trace(go.Bar(
                x=sdf["date"], y=sdf["surcharge"],
                name=sup, marker_color=COLORS[i % len(COLORS)],
                hovertemplate=f"<b>{sup}</b><br>" + "%{x|%b %Y}<br>$%{y:,.2f}<extra></extra>"
            ))

        fig2.update_layout(
            barmode="group",
            title=dict(text=f"{selected_mat} — Monthly Surcharge Cost",
                       font=dict(size=20, color="black")),
            xaxis=dict(title=dict(text="Date", font=dict(size=16, color="black", family="Arial Black")),
                       tickfont=dict(size=12, color="black")),
            yaxis=dict(title=dict(text="Surcharge ($)", font=dict(size=16, color="black", family="Arial Black")),
                       tickfont=dict(size=12, color="black")),
            height=400, template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig2, use_container_width=True)

        #Chart 3: Supplier Share Pie
        st.subheader("Supplier Share by Quantity")

        share = mat_data.groupby("supplier").agg(
            total_qty=("quantity", "sum")
        ).reset_index().sort_values("total_qty", ascending=False)

        if not share.empty and share["total_qty"].sum() > 0:
            fig3 = go.Figure(go.Pie(
                labels=share["supplier"], values=share["total_qty"],
                hole=0.4,
                hovertemplate="<b>%{label}</b><br>Qty: %{value:,.0f}<br>Share: %{percent}<extra></extra>"
            ))
            fig3.update_layout(
                title=dict(text=f"{selected_mat} — Supplier Volume Share",
                           font=dict(size=18, color="black")),
                height=400,
            )
            st.plotly_chart(fig3, use_container_width=True)

        #Detail Table
        with st.expander("Supplier Detail Table"):
            detail = mat_data.groupby("supplier").agg(
                avg_index=("index_cost", "mean"),
                avg_surcharge=("total_surcharge", "mean"),
                total_cost=("total_surcharge_cost", "sum"),
                total_qty=("quantity", "sum"),
                parts=("part_number", "nunique"),
                months=("date", "nunique"),
                avg_weight=("surcharge_weight_lbs", "mean"),
                avg_base=("raw_material_base_cost", "mean"),
            ).reset_index().sort_values("total_cost", ascending=False).round(4)

            st.dataframe(detail.style.format({
                "avg_index": "${:.4f}", "avg_surcharge": "${:.4f}",
                "total_cost": "${:,.2f}", "total_qty": "{:,.0f}",
                "avg_weight": "{:.4f}", "avg_base": "${:.4f}",
            }), use_container_width=True, hide_index=True)

        with st.expander("Export"):
            csv = mat_data.to_csv(index=False)
            st.download_button("Download CSV", csv,
                              f"material_{selected_mat.replace(' ', '_')}.csv", "text/csv")
