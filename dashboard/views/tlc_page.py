
import streamlit as st
import pandas as pd
from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()


def render():
    st.markdown('<p class="main-header">Total Landed Cost</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Compare supplier costs: base + surcharge + tariff + logistics - scrap</p>',
                unsafe_allow_html=True)

    ##Part selector
    col1, col2 = st.columns([3, 1])

    # Get parts that exist in both surcharge_monthly and parts_master
    @st.cache_data(ttl=300)
    def get_valid_parts():
        return pd.read_sql(text("""
            SELECT DISTINCT sm.part_number, pm.material_from_drawing,
                   COUNT(DISTINCT sm.supplier) AS supplier_count
            FROM surcharge_monthly sm
            JOIN parts_master pm ON sm.part_number = pm.part_number
            GROUP BY sm.part_number, pm.material_from_drawing
            ORDER BY supplier_count DESC
        """), engine)

    valid_parts = get_valid_parts()
    if valid_parts.empty:
        st.warning("No parts found with both surcharge and master data.")
        return

    with col1:
        selected_part = st.selectbox(
            "Select Part Number",
            valid_parts["part_number"].tolist(),
            format_func=lambda x: f"{x} — {valid_parts[valid_parts['part_number']==x]['material_from_drawing'].values[0]} ({valid_parts[valid_parts['part_number']==x]['supplier_count'].values[0]} suppliers)"
        )
    with col2:
        selected_year = st.selectbox("Year", [2025, 2024, 2026])

    if not selected_part:
        return

    ##Part info
    with engine.connect() as conn:
        part_info = conn.execute(text("""
            SELECT pm.part_number, pm.material_from_drawing, pm.cad_weight_lbs,
                   mca.base_material_name, mca.primary_commodity
            FROM parts_master pm
            LEFT JOIN material_classification_ai mca
                ON pm.material_from_drawing = mca.material_from_drawing
            WHERE pm.part_number = :pn
        """), {"pn": selected_part}).fetchone()

    if not part_info:
        st.error(f"Part {selected_part} not found.")
        return

    pi = dict(part_info._mapping)

    # Info cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Material", pi.get("material_from_drawing", "N/A"))
    c2.metric("Base Material", pi.get("base_material_name", "N/A"))
    c3.metric("Commodity", pi.get("primary_commodity", "N/A"))
    c4.metric("CAD Weight", f"{pi.get('cad_weight_lbs', 0):.4f} lbs")

    st.markdown("---")

    ##Calculate TLC for all suppliers
    suppliers = pd.read_sql(text("""
        SELECT DISTINCT supplier, destination_plant, material
        FROM surcharge_monthly
        WHERE part_number = :pn
    """), engine, params={"pn": selected_part})

    results = []
    for _, sup_row in suppliers.iterrows():
        supplier = sup_row["supplier"]
        material = pi.get("material_from_drawing", "")
        weight = float(pi.get("cad_weight_lbs") or 0)

        # Unit cost
        with engine.connect() as conn:
            uc_row = conn.execute(text("""
                SELECT unit_cost FROM supplier_parts
                WHERE part_number = :pn AND unit_cost IS NOT NULL
                ORDER BY snapshot_date DESC LIMIT 1
            """), {"pn": selected_part}).fetchone()
        unit_cost = float(uc_row[0]) if uc_row else 0

        # Surcharge
        with engine.connect() as conn:
            sc_row = conn.execute(text("""
                SELECT AVG(total_surcharge) AS avg_sc, SUM(total_surcharge_cost) AS ann_sc,
                       SUM(quantity) AS ann_qty
                FROM surcharge_monthly
                WHERE part_number = :pn AND supplier = :sup AND year = :yr
            """), {"pn": selected_part, "sup": supplier, "yr": selected_year}).fetchone()
        avg_surcharge = float(sc_row[0] or 0) if sc_row else 0
        annual_qty = float(sc_row[2] or 0) if sc_row else 0

        # Tariff
        with engine.connect() as conn:
            coo_row = conn.execute(text("""
                SELECT coo_code FROM supplier_parts
                WHERE part_number = :pn AND coo_code IS NOT NULL AND TRIM(coo_code) != ''
                ORDER BY snapshot_date DESC LIMIT 1
            """), {"pn": selected_part}).fetchone()
        origin = coo_row[0].strip().upper() if coo_row and coo_row[0] else ""

        with engine.connect() as conn:
            tr_row = conn.execute(text("""
                SELECT rate_pct FROM tariff_rates
                WHERE origin_country = :orig AND destination_country = 'US'
                ORDER BY effective_date DESC LIMIT 1
            """), {"orig": origin}).fetchone()
        tariff_rate = float(tr_row[0]) / 100 if tr_row else 0
        tariff_per_part = unit_cost * tariff_rate

        # Logistics
        with engine.connect() as conn:
            lr_row = conn.execute(text("""
                SELECT AVG(lr.rate_per_unit)
                FROM logistics_rates lr
                WHERE lr.origin IN (
                    SELECT DISTINCT coo_name FROM supplier_parts
                    WHERE coo_code = :coo
                )
            """), {"coo": origin}).fetchone()
        logistics = float(lr_row[0]) if lr_row and lr_row[0] else 0

        # Scrap
        with engine.connect() as conn:
            sr_row = conn.execute(text("""
                SELECT recovery_pct, scrap_value_per_lb FROM scrap_recovery_rates
                WHERE material_from_drawing = :mat
                ORDER BY effective_date DESC LIMIT 1
            """), {"mat": material}).fetchone()
        if sr_row and weight > 0:
            scrap = float(weight) * (float(sr_row[0]) / 100) * float(sr_row[1])
        else:
            scrap = 0

        tlc = unit_cost + avg_surcharge + tariff_per_part + logistics - scrap

        results.append({
            "Supplier": supplier,
            "Destination": sup_row.get("destination_plant", ""),
            "COO": origin,
            "Unit Cost": unit_cost,
            "Surcharge": avg_surcharge,
            "Tariff": tariff_per_part,
            "Logistics": logistics,
            "Scrap Recovery": scrap,
            "TLC/Part": tlc,
            "Annual Qty": int(annual_qty),
            "Annual TLC": round(tlc * annual_qty, 2),
        })

    if not results:
        st.warning("No supplier data found.")
        return

    df = pd.DataFrame(results).sort_values("TLC/Part")

    ##Best supplier highlight
    best = df.iloc[0]
    if len(df) > 1:
        worst = df.iloc[-1]
        savings = (worst["TLC/Part"] - best["TLC/Part"]) * worst["Annual Qty"]
    else:
        savings = 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Best Supplier", best["Supplier"])
    c2.metric("Best TLC/Part", f"${best['TLC/Part']:.2f}")
    c3.metric("Potential Savings", f"${savings:,.2f}/yr")

    st.markdown("---")

    ##TLC Comparison Table
    st.subheader("Supplier Comparison")

    styled = df.style.format({
        "Unit Cost": "${:.2f}",
        "Surcharge": "${:.2f}",
        "Tariff": "${:.2f}",
        "Logistics": "${:.2f}",
        "Scrap Recovery": "${:.2f}",
        "TLC/Part": "${:.2f}",
        "Annual Qty": "{:,.0f}",
        "Annual TLC": "${:,.2f}",
    }).background_gradient(subset=["TLC/Part"], cmap="RdYlGn_r")

    st.dataframe(styled, use_container_width=True, hide_index=True)

    ##Cost breakdown chart
    if len(df) > 0:
        import plotly.graph_objects as go

        st.subheader("Cost Breakdown by Supplier")
        components = ["Unit Cost", "Surcharge", "Tariff", "Logistics"]
        colors = ["#3B82F6", "#F59E0B", "#EF4444", "#8B5CF6"]

        fig = go.Figure()
        for comp, color in zip(components, colors):
            fig.add_trace(go.Bar(
                name=comp, x=df["Supplier"], y=df[comp],
                marker_color=color
            ))

        # Scrap as negative
        fig.add_trace(go.Bar(
            name="Scrap Recovery", x=df["Supplier"], y=-df["Scrap Recovery"],
            marker_color="#10B981"
        ))

        fig.update_layout(
            barmode="stack",
            title=dict(text="Cost Component Breakdown", font=dict(size=18, color="black")),
            xaxis=dict(tickfont=dict(size=14, color="black")),
            yaxis=dict(title="$/Part", tickfont=dict(size=14, color="black")),
            height=450, template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)
