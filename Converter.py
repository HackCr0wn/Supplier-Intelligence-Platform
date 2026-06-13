import pandas as pd
from pathlib import Path
from loguru import logger

files = {
    "ti_parts_2024": "C:/Users/99017494/Desktop/PROJECT/files/Supplier Parts List 2024-07_P12.xlsx",
    "t1_parts_2025": "C:/Users/99017494/Desktop/PROJECT/files/Supplier Parts List 2025_07_P12.xlsx",
    "t2_parts_jan": "C:/Users/99017494/Desktop/PROJECT/files/OE Supplier Parts List 2025-01_T2.xlsx",
    "t2_parts_dec": "C:/Users/99017494/Desktop/PROJECT/files/OE Supplier Parts List 2025-12_T2.xlsx",
    "parts_master": "C:/Users/99017494/Desktop/PROJECT/files/Parts raw materials master data.xlsx",
    "gross_weight": "C:/Users/99017494/Desktop/PROJECT/files/Suppliers parts gross weight data.xlsx",
    "t1_summary": "C:/Users/99017494/Desktop/PROJECT/files/Supplier_Summary_By_Year_T1_130526.xlsx",
    "t2_summary": "C:/Users/99017494/Desktop/PROJECT/files/Supplier_Summary_By_Year_T2.xlsx"
}

output_dir = Path("data/processed")
output_dir.mkdir(parents=True, exist_ok=True)

months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
month_num = {m: i+1 for i, m in enumerate(months)}

# 1. PARTS MASTER
# Source: Parts raw materials master data.xlsx → Material Details
def read_parts_master() -> pd.DataFrame:
    df = pd.read_excel(files["parts_master"], sheet_name="Material Details", dtype=str)
    df.columns = df.columns.str.strip()

    col_map = {
        "Buy Part Number":                  "part_number",
        "Material from Drawing":            "material_from_drawing",
        "Alternate Material From Drawing":  "alternate_material",
        "Heat Treat from Drawing":          "heat_treatment",
        "Surface Coating (Finish_1)":       "coating",
        "Class":                            "classification",
        "Sub Class":                        "sub_class",
        "Family":                           "family",
        "Form":                             "form",
        "Grade":                            "grade",
        "CAD Weight(lb)":                   "cad_weight_lbs",
    }
    
    #it only keeps column that presents in file
    present = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=present)[[v for v in present.values()]]
    df = df.dropna(subset=["part_number"])
    df["part_number"] = df["part_number"].str.strip()
    df["cad_weight_lbs"] = pd.to_numeric(df["cad_weight_lbs"], errors="coerce")

    logger.info(f"parts_master: {len(df)} rows")
    return df

# 2. PART WEIGHTS
# Source: Suppliers parts gross weight data.xlsx → Gross weights
def read_part_weights() -> pd.DataFrame:
    df = pd.read_excel(files["gross_weight"], sheet_name="Gross weights", dtype=str)
    df.columns = df.columns.str.strip()
 
    col_map = {
        "Part Number":           "part_number",
        "Supplier":              "supplier",
        "Weight from Invoice":   "weight_from_invoice",
        "Material from Drawing": "material_from_drawing",
        "Class (PBI)":           "class_pbi",
    }
 
    present = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=present)[[v for v in present.values()]]
    df = df.dropna(subset=["part_number"])
    df["part_number"] = df["part_number"].str.strip()
    df["weight_from_invoice"] = pd.to_numeric(df.get("weight_from_invoice", pd.Series()), errors="coerce")
 
    logger.info(f"part_weights: {len(df)} rows")
    return df

# 3. SUPPLIER PARTS + MONTHLY COSTS
# Sources: T1 2025_07, T2 2025-01, T2 2025-12 — all "Main Parts List" sheets
def _read_one_supplier_parts_file(
    filepath: Path,
    snapshot_date: str,
    cost_year: int,
    rcpts_year: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns: (supplier_parts_df, monthly_costs_df)
 
    cost_year  = year to assign to JanCost-DecCost columns
    rcpts_year = year to assign to JanRcpts-DecRcpts and JanRecdSpend-DecRecdSpend columns
    MRP columns use their own dates parsed from the column header.
    """
    df = pd.read_excel(filepath, sheet_name="Main Parts List", dtype=str)
    df.columns = df.columns.str.strip()
    source_file = Path(filepath).name
 
    # Normalise "Supplier Name" → "Supplier name" (T2 files capitalise differently)
    if "Supplier Name" in df.columns and "Supplier name" not in df.columns:
        df = df.rename(columns={"Supplier Name": "Supplier name"})
 
    # ── supplier_parts (static snapshot columns) ──────────────────────────────
    static_map = {
        "Part Number":   "part_number",
        "Supplier ID":   "supplier_id",
        "Supplier name": "supplier_name",
        "Plant":         "plant",
        "Commodity":     "commodity",
        "COO":           "coo_code",
        "COO Name":      "coo_name",
        "FX Type":       "fx_type",
        "Cost":          "unit_cost",
    }
    present_static = {k: v for k, v in static_map.items() if k in df.columns}
    sp = df.rename(columns=present_static)[[v for v in present_static.values()]].copy()
    sp["snapshot_date"] = snapshot_date
    sp["source_file"] = source_file
    sp = sp.dropna(subset=["part_number"])
    sp["part_number"] = sp["part_number"].str.strip()
    sp["supplier_id"]  = sp["supplier_id"].str.strip()
    sp["unit_cost"]    = pd.to_numeric(sp["unit_cost"], errors="coerce")
 
    # ── monthly_costs (unpivot Jan-Dec columns) ───────────────────────────────
    all_monthly = []
 
    def base_row(m_num, yr):
        """Create a clean base DataFrame for one month from Part Number + Supplier ID"""
        rows = df[["Part Number", "Supplier ID"]].copy()
        rows.columns = ["part_number", "supplier_id"]
        rows["year"]            = yr
        rows["month"]           = m_num
        rows["month_cost"]      = None
        rows["month_receipts"]  = None
        rows["month_recd_spend"]= None
        rows["mrp_qty"]         = None
        rows["mrp_spend"]       = None
        return rows
 
    for m in months:
        mn = month_num[m]
 
        # Cost columns → cost_year
        cost_col = f"{m}Cost"
        if cost_col in df.columns:
            rows = base_row(mn, cost_year)
            rows["month_cost"] = pd.to_numeric(df[cost_col], errors="coerce")
            all_monthly.append(rows)
 
        # Receipts + spend columns → rcpts_year
        rcpts_col = f"{m}Rcpts"
        spend_col = f"{m}RecdSpend"
        if rcpts_col in df.columns or spend_col in df.columns:
            rows = base_row(mn, rcpts_year)
            if rcpts_col in df.columns:
                rows["month_receipts"]   = pd.to_numeric(df[rcpts_col], errors="coerce")
            if spend_col in df.columns:
                rows["month_recd_spend"] = pd.to_numeric(df[spend_col], errors="coerce")
            all_monthly.append(rows)
 
    # MRP columns — date is encoded in the column header e.g. "MRPQty 8/1/2025"
    mrp_qty_cols   = {c: c.replace("MRPQty",   "").strip() for c in df.columns if c.startswith("MRPQty")}
    mrp_spend_cols = {c: c.replace("MRPSpend", "").strip() for c in df.columns if c.startswith("MRPSpend")}
 
    mrp_date_map = {}
    for col, date_str in mrp_qty_cols.items():
        mrp_date_map.setdefault(date_str, {})["qty"] = col
    for col, date_str in mrp_spend_cols.items():
        mrp_date_map.setdefault(date_str, {})["spend"] = col
 
    for date_str, cols in mrp_date_map.items():
        try:
            if str(date_str).strip().isdigit() and 1 <= int(date_str) <= 12:
                dt = pd.Timestamp(year=int(cost_year), month=int(date_str), day=1)
            else:
                dt = pd.to_datetime(date_str)

            rows = base_row(dt.month, dt.year)
            if "qty" in cols:
                rows["mrp_qty"]   = pd.to_numeric(df[cols["qty"]],   errors="coerce")
            if "spend" in cols:
                rows["mrp_spend"] = pd.to_numeric(df[cols["spend"]], errors="coerce")
            all_monthly.append(rows)
        except Exception as e:
            logger.warning(f"  Skipping MRP column '{date_str}': {e}")
 
    # Combine and clean
    if all_monthly:
        mc = pd.concat(all_monthly, ignore_index=True)
        mc["part_number"] = mc["part_number"].astype(str).str.strip()
        mc["supplier_id"] = mc["supplier_id"].astype(str).str.strip()
        mc = mc[mc["part_number"].notna() & (mc["part_number"] != "nan")]
        mc = mc[mc["supplier_id"].notna()  & (mc["supplier_id"]  != "nan")]
 
        # Drop rows where every metric column is NULL (no useful data)
        metric_cols = ["month_cost", "month_receipts", "month_recd_spend", "mrp_qty", "mrp_spend"]
        mc = mc.dropna(subset=metric_cols, how="all")
    else:
        mc = pd.DataFrame()
 
    logger.info(f"  {source_file}: supplier_parts={len(sp)}, monthly_costs={len(mc)}")
    return sp, mc
 
 
def read_all_supplier_parts() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read T1 + both T2 files and combine"""
    configs = [
        # (file_key,       snapshot_date,  cost_year, rcpts_year)
        ("t1_parts_2025",      "2025-07-01",   2025,      2024),
        ("t2_parts_jan",  "2025-01-01",   2025,      2024),
        ("t2_parts_dec",  "2025-12-01",   2026,      2025),  # cost cols = 2026 (forward budget)
    ]
 
    sp_frames, mc_frames = [], []
    for file_key, snapshot_date, cost_year, rcpts_year in configs:
        sp, mc = _read_one_supplier_parts_file(
            files[file_key], snapshot_date, cost_year, rcpts_year
        )
        sp_frames.append(sp)
        mc_frames.append(mc)
 
    supplier_parts = pd.concat(sp_frames, ignore_index=True)
    monthly_costs  = pd.concat(mc_frames, ignore_index=True)
 
    logger.info(f"supplier_parts combined: {len(supplier_parts)} rows")
    logger.info(f"monthly_costs combined:  {len(monthly_costs)} rows")
    return supplier_parts, monthly_costs

# 4. SURCHARGE MONTHLY
# Sources: T1 Summary (sheets: "2024 (2)", "2025 (2)") +
#          T2 Summary (sheets: "2024",      "2025"     )
def _normalise_surcharge_columns(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Rename inconsistent column names before any processing.
    Handles: T2 2025 New_ prefix, T1 2025 plural/renamed columns, Jan _Temp column.
    """
    rename = {}
 
    # T2 2025 — New_ prefix
    for old, new in [
        ("New_Supplier",                "Supplier"),
        ("New_Finished Part Numbers",   "Finished Part Number"),
        ("New_Invoiced Part Number",    "Invoiced Part Number"),
        ("New Destination Plant",       "Destination Plant"),
    ]:
        if old in df.columns:
            rename[old] = new
 
    # T1 2025 — plural / renamed columns
    if "Finished Part Numbers" in df.columns and "Finished Part Number" not in df.columns:
        rename["Finished Part Numbers"] = "Finished Part Number"
    if "Invoiced Number" in df.columns and "Invoiced Part Number" not in df.columns:
        rename["Invoiced Number"] = "Invoiced Part Number"
 
    # T1 2025 — Jan _Temp column
    if "Jan Part Surcharge ($/part)_Temp" in df.columns and "Jan Part Surcharge ($/part)" not in df.columns:
        rename["Jan Part Surcharge ($/part)_Temp"] = "Jan Part Surcharge ($/part)"
 
    if rename:
        logger.info(f"  [{label}] Renamed {len(rename)} columns: {list(rename.keys())}")
    df = df.rename(columns=rename)
    return df
 
 
def _unpivot_surcharge_sheet(df: pd.DataFrame, tier: str, year: int) -> pd.DataFrame:
    """Unpivot one surcharge sheet into monthly rows"""
    df = df.copy()
    df.columns = df.columns.str.strip()
 
    # Static columns — same value repeated for every month row
    static_map = {
        "Finished Part Number":                 "part_number",
        "Invoiced Part Number":                 "invoiced_part_number",
        "Supplier":                             "supplier",
        "Destination Plant":                    "destination_plant",
        "Material":                             "material",
        "Exact Raw Material Index Description": "raw_material_index",
        "Exact Scrap Index Description":        "scrap_index",
        "Part (Surcharge) Weight (lbs)":        "surcharge_weight_lbs",
        "Raw Material Base Cost ($/lb)":        "raw_material_base_cost",
        "Part Scrap Weight (lbs)":              "scrap_weight_lbs",
        "Base Material":                        "base_material",
    }
    present_static = {k: v for k, v in static_map.items() if k in df.columns}
    static_df = df.rename(columns=present_static)[[v for v in present_static.values()]].copy()
 
    for col in ["surcharge_weight_lbs", "raw_material_base_cost", "scrap_weight_lbs"]:
        if col in static_df.columns:
            static_df[col] = pd.to_numeric(static_df[col], errors="coerce")
 
    rows = []
    for m in months:
        mn = month_num[m]

        monthly_cols = {
            "index_cost":           f"{m} Index Cost ($/lb)",
            "part_surcharge":       f"{m} Part Surcharge ($/part)",
            "scrap_index_cost":     f"{m} Scrap Index Cost ($/lb)",
            "scrap_surcharge":      f"{m} Scrap Surcharge ($/part)",
            "total_surcharge":      f"Total {m} Surcharge ($/part)",
            "quantity":             f"{m} Quantity (Parts)",
            "total_surcharge_cost": f"{m} Total Surcharge Cost",
        }
 
        # Skip month if none of its columns exist in this sheet
        if not any(c in df.columns for c in monthly_cols.values()):
            continue
 
        monthly_data = static_df.copy()
        monthly_data["year"]  = year
        monthly_data["month"] = mn
 
        for field, col in monthly_cols.items():
            monthly_data[field] = pd.to_numeric(df[col], errors="coerce") if col in df.columns else None
 
        monthly_data["source_tier"] = tier
        rows.append(monthly_data)
 
    if not rows:
        return pd.DataFrame()
 
    result = pd.concat(rows, ignore_index=True)
    result = result.dropna(subset=["part_number", "supplier"])
    result = result[result["part_number"].astype(str) != "nan"]
    return result
 
 
def read_surcharge_monthly() -> pd.DataFrame:
    frames = []
 
    # T1 Summary
    for sheet, year in [("2024 (2)", 2024), ("2025 (2)", 2025)]:
        df = pd.read_excel(files["t1_summary"], sheet_name=sheet, dtype=str)
        df.columns = df.columns.str.strip()
        df = _normalise_surcharge_columns(df, f"T1 {sheet}")
        result = _unpivot_surcharge_sheet(df, tier="T1", year=year)
        logger.info(f"T1 surcharge [{sheet}]: {len(result)} rows")
        frames.append(result)
 
    # T2 Summary
    for sheet, year in [("2024", 2024), ("2025", 2025)]:
        df = pd.read_excel(files["t2_summary"], sheet_name=sheet, dtype=str)
        df.columns = df.columns.str.strip()
        df = _normalise_surcharge_columns(df, f"T2 {sheet}")
        result = _unpivot_surcharge_sheet(df, tier="T2", year=year)
        logger.info(f"T2 surcharge [{sheet}]: {len(result)} rows")
        frames.append(result)
 
    combined = pd.concat(frames, ignore_index=True)
    #T1 uses "SUPPLIER-X INC", T2 uses "SUPPLIER-X" - same data different name
    combined["supplier"] = combined["supplier"].str.strip().replace(r'\s+INC$', '', regex=True)

    # Same part+supplier+year+month exists in both T1 and T2 — keep T1 (loaded first)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["part_number", "supplier", "year", "month"], keep="first")
    logger.info(f"Dedup: {before} -> {len(combined)} rows (dropped {before - len(combined)} duplicates)")

 
    # Enforce final column order matching surcharge_monthly schema
    final_cols = [
        "part_number", "invoiced_part_number", "supplier", "destination_plant",
        "year", "month", "material", "raw_material_index", "scrap_index",
        "surcharge_weight_lbs", "raw_material_base_cost", "index_cost",
        "part_surcharge", "scrap_weight_lbs", "scrap_index_cost", "scrap_surcharge",
        "total_surcharge", "quantity", "total_surcharge_cost", "base_material", "source_tier",
    ]
    combined = combined[[c for c in final_cols if c in combined.columns]]
 
    logger.info(f"surcharge_monthly combined: {len(combined)} rows")
    return combined
 
 
# 
# MAIN
# 
def main():
    logger.info("=== Converter starting ===")
 
    parts_master = read_parts_master()
    parts_master.to_excel(output_dir / "parts_master.xlsx", index=False)
    logger.info(f"Saved: parts_master.xlsx — {len(parts_master)} rows")
 
    part_weights = read_part_weights()
    part_weights.to_excel(output_dir / "part_weights.xlsx", index=False)
    logger.info(f"Saved: part_weights.xlsx — {len(part_weights)} rows")
 
    supplier_parts, monthly_costs = read_all_supplier_parts()
    supplier_parts.to_excel(output_dir / "supplier_parts.xlsx", index=False)
    monthly_costs.to_excel(output_dir / "monthly_costs.xlsx", index=False)
    logger.info(f"Saved: supplier_parts.xlsx — {len(supplier_parts)} rows")
    logger.info(f"Saved: monthly_costs.xlsx  — {len(monthly_costs)} rows")
 
    surcharge_monthly = read_surcharge_monthly()
    surcharge_monthly.to_excel(output_dir / "surcharge_monthly.xlsx", index=False)
    logger.info(f"Saved: surcharge_monthly.xlsx — {len(surcharge_monthly)} rows")
 
    logger.info("=== Converter complete — check data/processed/ ===")
 
 
if __name__ == "__main__":
    main() 