import sys
import argparse
import pandas as pd
from sqlalchemy import text
from loguru import logger
from utils.db import get_engine

engine = get_engine()

def get_part_info(part_number: str) -> dict:
    #get parts_master info + material_classification
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT pm.part_number, pm.material_from_drawing, pm.cad_weight_lbs, pm.classification, mca.base_material_name, mca.primary_commodity, mca.secondary_commodity
            FROM parts_master pm
            LEFT JOIN material_classification_ai mca ON pm.material_from_drawing = mca.material_from_drawing
            WHERE pm.part_number = :pn
        """), {"pn": part_number}).fetchone()

    if not row:
        return None
    return dict(row._mapping)

def get_suppliers_for_part(part_number: str) -> pd.DataFrame:
    #Get all suppliers for a part from surcharge_monthly
    return pd.read_sql(text("""
        SELECT DISTINCT supplier, destination_plant, material, base_material
        FROM surcharge_monthly
        WHERE part_number = :pn
    """), engine, params={"pn": part_number})

def get_surcharge_summary(part_number: str, supplier: str, year: int) -> dict:
    #Get annual surcharge summary for part-supplier-year combo
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                supplier,
                COUNT(*) AS months_with_data,
                ROUND(AVG(surcharge_weight_lbs), 4) AS avg_weight_lbs,
                ROUND(AVG(raw_material_base_cost), 4) AS avg_base_cost_per_lb,
                ROUND(AVG(index_cost), 4) AS avg_index_cost,
                ROUND(AVG(part_surcharge), 4) AS avg_part_surcharge,
                ROUND(AVG(scrap_surcharge), 4) AS avg_scrap_surcharge,
                ROUND(AVG(total_surcharge), 4) AS avg_total_surcharge,
                ROUND(SUM(total_surcharge_cost), 2) AS annual_surcharge_cost,
                ROUND(SUM(quantity), 0) AS annual_quantity
            FROM surcharge_monthly
            WHERE part_number = :pn AND supplier = :sup AND year = :yr
        """), {"pn": part_number, "sup": supplier, "yr": year}).fetchone()
    if not row:
        return None
    return dict(row._mapping)

def get_unit_cost(part_number: str, supplier_id: str = None) -> float:
    #Get latest unit cost from supplier_parts
    sql = """
        SELECT unit_cost
        FROM supplier_parts
        WHERE part_number = :pn AND unit_cost IS NOT NULL
    """

    params = {"pn": part_number}

    if supplier_id:
        sql +=  "AND supplier_id = :sid"
        params["sid"] = supplier_id
    
    sql += "ORDER BY snapshot_date DESC LIMIT 1"

    with engine.connect() as conn:
        row = conn.execute(text(sql), params).fetchone()
    return float(row[0]) if row else None

def get_tariff_rate(origin: str, destination: str) -> float:
    #Get tariff rate if available
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT rate_pct FROM tariff_rates
            WHERE origin_country = :orig AND destination_country = :dest
            ORDER BY effective_date DESC LIMIT 1
        """), {"orig": origin, "dest": destination}).fetchone()
    
    return float(row[0]) / 100 if row else 0.0



def get_logistics_rate(supplier: str, part_number: str = None) -> float:
    """Get logistics rate — try supplier match first, then COO-based match"""
    with engine.connect() as conn:
        # Try direct supplier match
        row = conn.execute(text("""
            SELECT rate_per_unit FROM logistics_rates
            WHERE supplier = :sup
            ORDER BY effective_date DESC LIMIT 1
        """), {"sup": supplier}).fetchone()

        if row:
            return float(row[0])

        # Fallback: match by COO region from supplier_parts
        if part_number:
            coo_row = conn.execute(text("""
                SELECT coo_code FROM supplier_parts
                WHERE part_number = :pn
                  AND coo_code IS NOT NULL AND TRIM(coo_code) != ''
                ORDER BY snapshot_date DESC LIMIT 1
            """), {"pn": part_number}).fetchone()

            if coo_row and coo_row[0]:
                coo = coo_row[0].strip().upper()
                # Find any logistics rate for a supplier from same COO
                rate_row = conn.execute(text("""
                    SELECT AVG(lr.rate_per_unit) AS avg_rate
                    FROM logistics_rates lr
                    WHERE lr.origin IN (
                        SELECT DISTINCT sp.coo_name
                        FROM supplier_parts sp
                        WHERE sp.coo_code = :coo
                    )
                """), {"coo": coo}).fetchone()

                if rate_row and rate_row[0]:
                    return float(rate_row[0])

    return 0.0



def get_scrap_recovery(material: str, weight_lbs: float) -> float:
    #Get scrap recovery value if available
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT recovery_pct, scrap_value_per_lb FROM scrap_recovery_rates
            WHERE material_from_drawing = :mat
            ORDER BY effective_date DESC LIMIT 1
        """), {"mat": material}).fetchone()

    if not row:
        return 0.0

    recovery_pct = float(row[0]) / 100
    scrap_value = float(row[1])
    return float(weight_lbs) * recovery_pct * scrap_value


def calculate_tlc(part_number: str, year: int) -> pd.DataFrame:
    #Calculate TLC for all suppliers of a given part

    part_info = get_part_info(part_number)
    if not part_info:
        logger.error(f"Part {part_number} not found in parts_master")
        return pd.DataFrame()

    suppliers = get_suppliers_for_part(part_number)
    if suppliers.empty:
        logger.error(f"No suppliers found for part {part_number} in surcharge_monthly")
        return pd.DataFrame()

    results = []

    for _, sup_row in suppliers.iterrows():
        supplier = sup_row["supplier"]
        material = part_info.get("material_from_drawing", "")
        weight = part_info.get("cad_weight_lbs") or 0

        # 1. Base unit cost
        unit_cost = get_unit_cost(part_number)

        # 2. Surcharge data
        surcharge = get_surcharge_summary(part_number, supplier, year)
        avg_surcharge_per_part = float(surcharge["avg_total_surcharge"] or 0) if surcharge else 0
        annual_surcharge = float(surcharge["annual_surcharge_cost"] or 0) if surcharge else 0
        annual_qty = float(surcharge["annual_quantity"] or 0) if surcharge else 0


        # 3. Tariff (uses COO from supplier_parts if available)
        
       
        with engine.connect() as conn:
            coo_row = conn.execute(text("""
                SELECT coo_code FROM supplier_parts
                WHERE part_number = :pn
                  AND coo_code IS NOT NULL AND TRIM(coo_code) != ''
                  AND UPPER(TRIM(coo_code)) NOT IN ('US', '0', 'NO LISTING')
                ORDER BY snapshot_date DESC LIMIT 1
            """), {"pn": part_number}).fetchone()

            # Fallback to any COO if no foreign found
            if not coo_row:
                coo_row = conn.execute(text("""
                    SELECT coo_code FROM supplier_parts
                    WHERE part_number = :pn
                      AND coo_code IS NOT NULL AND TRIM(coo_code) != ''
                    ORDER BY snapshot_date DESC LIMIT 1
                """), {"pn": part_number}).fetchone()

        origin = coo_row[0].strip().upper() if coo_row and coo_row[0] else ""

        # Try material-specific tariff first, then country average
        with engine.connect() as conn:
            tr_row = conn.execute(text("""
                SELECT rate_pct FROM tariff_rates
                WHERE origin_country = :orig AND destination_country = 'US'
                  AND notes LIKE CONCAT(:mat, ' %%')
                ORDER BY effective_date DESC LIMIT 1
            """), {"orig": origin, "mat": material}).fetchone()

            if not tr_row:
                tr_row = conn.execute(text("""
                    SELECT rate_pct FROM tariff_rates
                    WHERE origin_country = :orig AND destination_country = 'US'
                    ORDER BY rate_pct DESC LIMIT 1
                """), {"orig": origin}).fetchone()

        tariff_rate = float(tr_row[0]) / 100 if tr_row else 0
        tariff_per_part = (unit_cost or 0) * tariff_rate


      

        # 4. Logistics
        logistics_per_part = get_logistics_rate(supplier, part_number)

        # 5. Scrap recovery
        scrap_recovery = get_scrap_recovery(material, weight) if weight else 0

        # 6. TLC per part
        base = float(unit_cost or 0)
        tlc_per_part = base + avg_surcharge_per_part + tariff_per_part + logistics_per_part - scrap_recovery

        results.append({
            "part_number": part_number,
            "supplier": supplier,
            "material": material,
            "base_material": part_info.get("base_material_name", ""),
            "commodity": part_info.get("primary_commodity", ""),
            "destination": sup_row.get("destination_plant", ""),
            "year": year,
            "unit_cost": round(base, 4),
            "avg_surcharge": round(avg_surcharge_per_part, 4),
            "tariff": round(tariff_per_part, 4),
            "logistics": round(logistics_per_part, 4),
            "scrap_recovery": round(scrap_recovery, 4),
            "tlc_per_part": round(tlc_per_part, 4),
            "annual_qty": int(annual_qty) if annual_qty else 0,
            "annual_tlc": round(tlc_per_part * (annual_qty or 0), 2),
            "months_data": surcharge["months_with_data"] if surcharge else 0,
        })

    df = pd.DataFrame(results)
    df = df.sort_values("tlc_per_part", ascending=True)
    return df


def print_tlc_report(part_number: str, year: int):
    #Print formatted TLC comparison report

    part_info = get_part_info(part_number)
    if not part_info:
        print(f"\n Part {part_number} not found.")
        return

    print(f"\n{'=' * 90}")
    print(f" TOTAL LANDED COST REPORT — Part {part_number}")
    print(f"{'=' * 90}")
    print(f"  Material:       {part_info.get('material_from_drawing', 'N/A')}")
    print(f"  Base Material:  {part_info.get('base_material_name', 'N/A')}")
    print(f"  Commodity:      {part_info.get('primary_commodity', 'N/A')}")
    print(f"  CAD Weight:     {part_info.get('cad_weight_lbs', 'N/A')} lbs")
    print(f"  Year:           {year}")

    df = calculate_tlc(part_number, year)

    if df.empty:
        print("\n  No supplier data found.")
        return

    print(f"\n{'─' * 90}")
    print(f"  {'Supplier':<20} {'Unit$':>8} {'Surch$':>8} {'Tariff$':>8} {'Logist$':>8} {'Scrap$':>8} {'TLC/pt$':>10} {'Ann Qty':>8} {'Ann TLC$':>12}")
    print(f"{'─' * 90}")

    for _, row in df.iterrows():
        flag = "  BEST" if row.name == df.index[0] else ""
        print(f"  {row['supplier']:<20} "
              f"{row['unit_cost']:>8.2f} "
              f"{row['avg_surcharge']:>8.2f} "
              f"{row['tariff']:>8.2f} "
              f"{row['logistics']:>8.2f} "
              f"{row['scrap_recovery']:>8.2f} "
              f"{row['tlc_per_part']:>10.2f} "
              f"{row['annual_qty']:>8,} "
              f"{row['annual_tlc']:>12,.2f}"
              f"{flag}")

    print(f"{'─' * 90}")

    # Savings opportunity
    if len(df) > 1:
        best = df.iloc[0]["tlc_per_part"]
        worst = df.iloc[-1]["tlc_per_part"]
        worst_qty = df.iloc[-1]["annual_qty"]
        savings = (worst - best) * worst_qty
        print(f"\n   Savings if worst → best supplier: ${savings:,.2f}/year")

    # Data quality flags
    print(f"\n    Data Notes:")
    if df["unit_cost"].eq(0).any():
        print(f"     - Some suppliers missing unit cost (showing $0)")
    if df["tariff"].eq(0).all():
        print(f"     - Tariff rates not populated — tariff_rates table is empty")
    if df["logistics"].eq(0).all():
        print(f"     - Logistics rates not populated — logistics_rates table is empty")
    if df["scrap_recovery"].eq(0).all():
        print(f"     - Scrap recovery not populated — scrap_recovery_rates table is empty")

    print(f"\n{'=' * 90}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TLC Calculator")
    parser.add_argument("--part", required=True, help="Part number")
    parser.add_argument("--year", type=int, default=2024, help="Year (default: 2024)")
    args = parser.parse_args()

    print_tlc_report(args.part, args.year)
