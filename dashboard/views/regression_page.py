
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


def render():
    st.markdown('<p class="main-header">Supplier-Commodity Regression</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">How much does the commodity market drive supplier pricing?</p>',
                unsafe_allow_html=True)

    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import r2_score, mean_absolute_error

    ##Material selector
    materials = pd.read_sql(text("""
        SELECT DISTINCT pm.material_from_drawing, mcm.commodity_code, cm.commodity_name
        FROM parts_master pm
        JOIN material_commodity_map mcm ON pm.material_from_drawing = mcm.material_from_drawing
        JOIN commodity_master cm ON mcm.commodity_code = cm.commodity_code
        JOIN surcharge_monthly sm ON pm.part_number = sm.part_number
        WHERE sm.index_cost IS NOT NULL AND sm.index_cost != 0
        ORDER BY pm.material_from_drawing
    """), engine)

    if materials.empty:
        st.warning("No materials with both commodity mapping and supplier data.")
        return

    selected_mat = st.selectbox("Select Material",
        materials["material_from_drawing"].unique().tolist())

    mat_info = materials[materials["material_from_drawing"] == selected_mat].iloc[0]
    commodity_code = mat_info["commodity_code"]
    commodity_name = mat_info["commodity_name"]

    st.info(f"Primary commodity: **{commodity_name}** ({commodity_code})")

    ##Get supplier data
    supplier_df = pd.read_sql(text("""
        SELECT sm.year, sm.month, sm.supplier, AVG(sm.index_cost) AS avg_index_cost
        FROM surcharge_monthly sm
        JOIN parts_master pm ON sm.part_number = pm.part_number
        WHERE pm.material_from_drawing = :mat AND sm.index_cost IS NOT NULL AND sm.index_cost != 0
        GROUP BY sm.year, sm.month, sm.supplier
    """), engine, params={"mat": selected_mat})

    supplier_df["date"] = pd.to_datetime(
        supplier_df["year"].astype(str) + "-" + supplier_df["month"].astype(str).str.zfill(2) + "-01")

    ##Get commodity data
    comm_df = pd.read_sql(text("""
        SELECT cp.price_date AS date, cp.price AS commodity_price
        FROM commodity_prices cp
        JOIN commodity_master cm ON cp.commodity_id = cm.commodity_id
        WHERE cm.commodity_code = :code
    """), engine, params={"code": commodity_code})
    comm_df["date"] = pd.to_datetime(comm_df["date"])
    comm_df = comm_df.set_index("date").resample("MS").mean().reset_index()

    ##Results table
    results = []
    suppliers = supplier_df["supplier"].unique()

    for sup in suppliers:
        sdf = supplier_df[supplier_df["supplier"] == sup]
        merged = sdf.merge(comm_df, on="date", how="inner")
        if len(merged) < 6:
            results.append({"Supplier": sup, "R²": None, "MAE": None,
                           "Correlation": None, "N": len(merged), "Link": "Too few pts"})
            continue

        X = merged[["commodity_price"]].values
        y = merged["avg_index_cost"].values
        reg = LinearRegression().fit(X, y)
        y_pred = reg.predict(X)
        r2 = r2_score(y, y_pred)
        mae = mean_absolute_error(y, y_pred)
        corr = np.corrcoef(merged["commodity_price"], merged["avg_index_cost"])[0, 1]

        if r2 > 0.7:   link = "Strong"
        elif r2 > 0.4: link = "Moderate"
        elif r2 > 0.1: link = "Weak"
        else:           link = "None"

        results.append({"Supplier": sup, "R²": r2, "MAE": mae,
                        "Correlation": corr, "N": len(merged), "Link": link})

    rdf = pd.DataFrame(results)

    ##KPI 
    valid = rdf[rdf["R²"].notna()]
    c1, c2, c3 = st.columns(3)
    c1.metric("Suppliers Analyzed", len(valid))
    if not valid.empty:
        c2.metric("Avg R²", f"{valid['R²'].mean():.3f}")
        strong = len(valid[valid["R²"] > 0.7])
        c3.metric("Strong Links", f"{strong}/{len(valid)}")

    st.markdown("---")

    ##Table
    st.subheader("Regression Results")
    styled = rdf.style.format({
        "R²": "{:.3f}", "MAE": "${:.4f}", "Correlation": "{:.3f}"
    }, na_rep="—")
    st.dataframe(styled, use_container_width=True, hide_index=True)

    ##Scatter plot for best supplier
    if not valid.empty:
        best_sup = valid.sort_values("R²", ascending=False).iloc[0]["Supplier"]
        st.subheader(f"📈 Scatter — {best_sup} vs {commodity_name}")

        sdf = supplier_df[supplier_df["supplier"] == best_sup]
        merged = sdf.merge(comm_df, on="date", how="inner")

        X = merged[["commodity_price"]].values
        y = merged["avg_index_cost"].values
        reg = LinearRegression().fit(X, y)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=merged["commodity_price"], y=merged["avg_index_cost"],
            mode="markers", name="Data Points",
            marker=dict(size=10, color="#3B82F6"),
            hovertemplate="Commodity: $%{x:.2f}<br>Supplier: $%{y:.4f}<extra></extra>"
        ))

        x_range = np.linspace(X.min(), X.max(), 50)
        y_line = reg.predict(x_range.reshape(-1, 1))
        fig.add_trace(go.Scatter(
            x=x_range, y=y_line, mode="lines", name="Regression Line",
            line=dict(color="#EF4444", width=2, dash="dash")
        ))

        fig.update_layout(
            xaxis=dict(title=dict(text=f"{commodity_name} ($/unit)",
                       font=dict(size=16, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            yaxis=dict(title=dict(text="Supplier Index Cost ($/lb)",
                       font=dict(size=16, color="black", family="Arial Black")),
                       tickfont=dict(size=14, color="black")),
            height=450, template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.caption("R² > 0.7 = commodity market drives supplier pricing → good candidate for hedging")
    st.caption("R² < 0.3 = supplier price decoupled from market → negotiate harder on unit cost")
