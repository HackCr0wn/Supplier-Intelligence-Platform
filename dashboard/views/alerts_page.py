
import streamlit as st
import pandas as pd
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


def render():
    st.markdown('<p class="main-header">⚠️ Price Alerts</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Commodity price alerts and monitoring</p>',
                unsafe_allow_html=True)

    ##Active alerts
    alerts = pd.read_sql(text("""
        SELECT id, mat_id, supplier_id, alert_type, alert_month,
               previous_value, current_value, change_pct,
               message, status, created_at
        FROM price_alerts
        ORDER BY created_at DESC
        LIMIT 50
    """), engine)

    if alerts.empty:
        st.info("No alerts found. Alerts are generated when prices exceed configured thresholds.")
    else:
        # Summary
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Alerts", len(alerts))
        active = len(alerts[alerts["status"] == "OPEN"])
        c2.metric("Open", active)
        resolved = len(alerts[alerts["status"] != "OPEN"])
        c3.metric("Resolved", resolved)

        st.markdown("---")

        # Filter by status
        status_filter = st.radio("Filter", ["All", "OPEN", "RESOLVED"],
                                  horizontal=True)
        if status_filter != "All":
            alerts = alerts[alerts["status"] == status_filter]

        # Format and display
        display_df = alerts.copy()
        display_df["change_pct"] = display_df["change_pct"].apply(
            lambda x: f"{x:+.2f}%" if pd.notna(x) else "—"
        )
        display_df["previous_value"] = display_df["previous_value"].apply(
            lambda x: f"${x:,.4f}" if pd.notna(x) else "—"
        )
        display_df["current_value"] = display_df["current_value"].apply(
            lambda x: f"${x:,.4f}" if pd.notna(x) else "—"
        )

        st.dataframe(display_df, use_container_width=True, hide_index=True)

    ##Latest commodity prices
    st.markdown("---")
    st.subheader("Latest Commodity Prices")

    latest = pd.read_sql(text("""
        SELECT cm.commodity_name, cm.commodity_code,
               cp.price_date, cp.price,
               cm.ticker_symbol AS fred_series
        FROM commodity_master cm
        JOIN commodity_prices cp ON cm.commodity_id = cp.commodity_id
        WHERE cp.price_date = (
            SELECT MAX(cp2.price_date)
            FROM commodity_prices cp2
            WHERE cp2.commodity_id = cm.commodity_id
        )
        ORDER BY cm.commodity_name
    """), engine)

    if not latest.empty:
        st.dataframe(latest.style.format({
            "price": "${:,.2f}"
        }), use_container_width=True, hide_index=True)
    else:
        st.warning("No commodity prices loaded.")
