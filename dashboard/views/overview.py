
import streamlit as st
import pandas as pd
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


def render():
    st.markdown('<p class="main-header">Overview</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Platform health and key metrics at a glance</p>',
                unsafe_allow_html=True)

    ##KPI Row 1: Data Coverage
    with engine.connect() as conn:
        parts = conn.execute(text("SELECT COUNT(*) FROM parts_master")).scalar()
        suppliers = conn.execute(text(
            "SELECT COUNT(DISTINCT supplier) FROM surcharge_monthly")).scalar()
        materials = conn.execute(text(
            "SELECT COUNT(DISTINCT material_from_drawing) FROM parts_master WHERE material_from_drawing IS NOT NULL")).scalar()
        commodities = conn.execute(text("SELECT COUNT(*) FROM commodity_master")).scalar()
        surcharge_rows = conn.execute(text("SELECT COUNT(*) FROM surcharge_monthly")).scalar()
        fx_rates = conn.execute(text("SELECT COUNT(*) FROM fx_rates")).scalar()
        commodity_prices = conn.execute(text("SELECT COUNT(*) FROM commodity_prices")).scalar()

    st.subheader("Data Coverage")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Parts", f"{parts:,}")
    c2.metric("Suppliers", f"{suppliers:,}")
    c3.metric("Materials", f"{materials:,}")
    c4.metric("Commodities", f"{commodities}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Surcharge Records", f"{surcharge_rows:,}")
    c6.metric("FX Rates", f"{fx_rates:,}")
    c7.metric("Commodity Prices", f"{commodity_prices:,}")
    c8.metric("Classification", "52/52")

    st.markdown("---")

    ##KPI Row 2: Spend Summary
    st.subheader("Spend Summary")
    spend_df = pd.read_sql(text("""
        SELECT year,
               ROUND(SUM(total_surcharge_cost), 0) AS total_surcharge,
               SUM(quantity) AS total_qty,
               COUNT(DISTINCT supplier) AS suppliers,
               COUNT(DISTINCT part_number) AS parts
        FROM surcharge_monthly
        GROUP BY year
        ORDER BY year
    """), engine)

    if not spend_df.empty:
        cols = st.columns(len(spend_df))
        for i, (_, row) in enumerate(spend_df.iterrows()):
            with cols[i]:
                st.metric(f"Year {int(row['year'])}", f"${row['total_surcharge']:,.0f}")
                st.caption(f"{int(row['total_qty']):,} parts · {int(row['suppliers'])} suppliers")

    st.markdown("---")

    ##Top Suppliers
    st.subheader("Top 10 Suppliers by Surcharge Cost")
    top_sup = pd.read_sql(text("""
        SELECT supplier, ROUND(SUM(total_surcharge_cost), 2) AS total_cost,
               COUNT(DISTINCT part_number) AS parts
        FROM surcharge_monthly
        GROUP BY supplier
        ORDER BY total_cost DESC
        LIMIT 10
    """), engine)

    if not top_sup.empty:
        top_sup.index = range(1, len(top_sup) + 1)
        st.dataframe(top_sup.style.format({
            "total_cost": "${:,.2f}",
        }), use_container_width=True)

    ##Top Materials
    st.subheader("Top 10 Materials by Surcharge Cost")
    top_mat = pd.read_sql(text("""
        SELECT material, ROUND(SUM(total_surcharge_cost), 2) AS total_cost,
               COUNT(DISTINCT part_number) AS parts
        FROM surcharge_monthly
        WHERE material IS NOT NULL
        GROUP BY material
        ORDER BY total_cost DESC
        LIMIT 10
    """), engine)

    if not top_mat.empty:
        top_mat.index = range(1, len(top_mat) + 1)
        st.dataframe(top_mat.style.format({
            "total_cost": "${:,.2f}",
        }), use_container_width=True)

    ##Commodity Coverage
    st.subheader("Commodity Data Coverage")
    cov_df = pd.read_sql(text("""
        SELECT cm.commodity_name, COUNT(cp.price_id) AS data_points,
               MIN(cp.price_date) AS earliest, MAX(cp.price_date) AS latest
        FROM commodity_master cm
        LEFT JOIN commodity_prices cp ON cm.commodity_id = cp.commodity_id
        GROUP BY cm.commodity_name
        ORDER BY cm.commodity_name
    """), engine)

    if not cov_df.empty:
        st.dataframe(cov_df, use_container_width=True)
