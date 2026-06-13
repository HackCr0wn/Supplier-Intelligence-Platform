import pandas as pd
from pathlib import Path
from utils.db import get_engine
from loguru import logger
 
PROCESSED_DIR = Path("data/processed")
engine = get_engine()
 
 
def _bulk_insert(engine, sql: str, values: list, table_name: str, chunksize: int = 500):
    """Core upsert function used by all loaders"""
    if not values:
        logger.warning(f"{table_name}: 0 rows — nothing to insert")
        return
 
    conn = engine.raw_connection()
    try:
        cursor = conn.cursor()
        total = 0
        for i in range(0, len(values), chunksize):
            chunk = values[i:i + chunksize]
            cursor.executemany(sql, chunk)
            total += len(chunk)
        conn.commit()
        logger.info(f"{table_name}: {total} rows upserted")
    except Exception as e:
        conn.rollback()
        logger.error(f"{table_name} failed: {e}")
        raise
    finally:
        conn.close()
 
 
def _to_values(df: pd.DataFrame, cols: list) -> list:
    """Convert DataFrame to list of tuples, NaN → None"""
    df = df[cols].astype(object).where(df[cols].notna(), None)
    return [tuple(row) for row in df.itertuples(index=False, name=None)]
 
 
def _build_sql(table: str, cols: list, update_cols: list, coalesce_cols: list = None) -> str:
    """Build INSERT ... ON DUPLICATE KEY UPDATE SQL"""
    col_list     = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
 
    update_parts = []
    for c in update_cols:
        if coalesce_cols and c in coalesce_cols:
            update_parts.append(f"`{c}` = COALESCE(VALUES(`{c}`), `{c}`)")
        else:
            update_parts.append(f"{c} = VALUES({c})")
 
    update_clause = ", ".join(update_parts)
    return f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"

def load_parts_master():
    df = pd.read_excel(PROCESSED_DIR / "parts_master.xlsx", dtype=str)
    df["cad_weight_lbs"] = pd.to_numeric(df.get("cad_weight_lbs", pd.Series()), errors="coerce")
    df = df.dropna(subset=["part_number"])
    df["part_number"] = df["part_number"].str.strip()
 
    cols = ["part_number", "material_from_drawing", "alternate_material", "heat_treatment",
            "coating", "classification", "sub_class", "family", "form", "grade", "cad_weight_lbs"]
    cols = [c for c in cols if c in df.columns]
    update_cols = [c for c in cols if c != "part_number"]
 
    sql = _build_sql("parts_master", cols, update_cols)
    _bulk_insert(engine, sql, _to_values(df, cols), "parts_master")
 
 
def load_supplier_parts():
    df = pd.read_excel(PROCESSED_DIR / "supplier_parts.xlsx", dtype=str)
    df["unit_cost"] = pd.to_numeric(df.get("unit_cost", pd.Series()), errors="coerce")
    df = df.dropna(subset=["part_number"])
    df["part_number"] = df["part_number"].str.strip()
    df["supplier_id"]  = df["supplier_id"].str.strip()
 
    cols = ["part_number", "supplier_id", "supplier_name", "plant", "commodity",
            "coo_code", "coo_name", "fx_type", "unit_cost", "snapshot_date", "source_file"]
    cols = [c for c in cols if c in df.columns]
 
    unique_key  = {"part_number", "supplier_id", "snapshot_date"}
    update_cols = [c for c in cols if c not in unique_key]
 
    sql = _build_sql("supplier_parts", cols, update_cols)
    _bulk_insert(engine, sql, _to_values(df, cols), "supplier_parts")

def load_part_weights():
    df = pd.read_excel(PROCESSED_DIR / "part_weights.xlsx", dtype=str)
    df["weight_from_invoice"] = pd.to_numeric(df.get("weight_from_invoice", pd.Series()), errors="coerce")
    df = df.dropna(subset=["part_number"])
    df["part_number"] = df["part_number"].str.strip()
 
    cols = ["part_number", "supplier", "weight_from_invoice", "material_from_drawing", "class_pbi"]
    cols = [c for c in cols if c in df.columns]
    col_list     = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    values = _to_values(df, cols)
 
    conn = engine.raw_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE part_weights")
        sql = f"INSERT INTO part_weights ({col_list}) VALUES ({placeholders})"
        for i in range(0, len(values), 500):
            cursor.executemany(sql, values[i:i + 500])
        conn.commit()
        logger.info(f"part_weights: {len(values)} rows loaded (truncate + reload)")
    except Exception as e:
        conn.rollback()
        logger.error(f"part_weights failed: {e}")
        raise
    finally:
        conn.close()
 
 
def load_monthly_costs():
    df = pd.read_excel(PROCESSED_DIR / "monthly_costs.xlsx")
 
    metric_cols = ["month_cost", "month_receipts", "month_recd_spend", "mrp_qty", "mrp_spend"]
    for c in metric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
 
    df["part_number"] = df["part_number"].astype(str).str.strip()
    df["supplier_id"]  = df["supplier_id"].astype(str).str.strip()
    df = df.dropna(subset=["part_number", "supplier_id"])
    df = df[df["part_number"] != "nan"]
 
    cols = ["part_number", "supplier_id", "year", "month",
            "month_cost", "month_receipts", "month_recd_spend", "mrp_qty", "mrp_spend"]
    cols = [c for c in cols if c in df.columns]
 
    unique_key    = {"part_number", "supplier_id", "year", "month"}
    update_cols   = [c for c in cols if c not in unique_key]
 
    sql = _build_sql("monthly_costs", cols, update_cols, coalesce_cols=metric_cols)
    _bulk_insert(engine, sql, _to_values(df, cols), "monthly_costs")
 
 
def load_surcharge_monthly():
    df = pd.read_excel(PROCESSED_DIR / "surcharge_monthly.xlsx")
 
    numeric_cols = ["surcharge_weight_lbs", "raw_material_base_cost", "index_cost",
                    "part_surcharge", "scrap_weight_lbs", "scrap_index_cost",
                    "scrap_surcharge", "total_surcharge", "quantity", "total_surcharge_cost"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
 
    df["part_number"] = df["part_number"].astype(str).str.strip()
    df["supplier"]     = df["supplier"].astype(str).str.strip()
    df = df.dropna(subset=["part_number", "supplier"])
    df = df[df["part_number"] != "nan"]
 
    final_cols = [
        "part_number", "invoiced_part_number", "supplier", "destination_plant",
        "year", "month", "material", "raw_material_index", "scrap_index",
        "surcharge_weight_lbs", "raw_material_base_cost", "index_cost",
        "part_surcharge", "scrap_weight_lbs", "scrap_index_cost", "scrap_surcharge",
        "total_surcharge", "quantity", "total_surcharge_cost", "base_material", "source_tier",
    ]
    cols = [c for c in final_cols if c in df.columns]
 
    unique_key  = {"part_number", "supplier", "year", "month", "source_tier"}
    update_cols = [c for c in cols if c not in unique_key]
 
    sql = _build_sql("surcharge_monthly", cols, update_cols)
    _bulk_insert(engine, sql, _to_values(df, cols), "surcharge_monthly")

def main():
    logger.info("=== load_to_db starting ===")
    load_parts_master()
    load_supplier_parts()
    load_part_weights()
    load_monthly_costs()
    load_surcharge_monthly()
    logger.info("=== load_to_db complete ===")

if __name__ == "__main__":
    main()