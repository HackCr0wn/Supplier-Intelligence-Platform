import sys
import os
import json
import re
import io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd     
import numpy as np
from sqlalchemy import text
from dotenv import load_dotenv
from utils.db import get_engine
from difflib import SequenceMatcher, get_close_matches

load_dotenv()
engine = get_engine()

def log_upload(filename: str, sheet: str, table: str, rows: int, mapping: dict, metadata: dict, anomalies: list = None) -> int:
    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    mapped_count = len([m for m in mapping.values() if m.get("target_column")])
    unmapped_count = len(metadata.get("unmapped_columns", []))
    anomaly_count = len(anomalies) if anomalies else 0

    cursor.execute("""
        INSERT INTO upload_history
            (filename, sheet_name, target_table, rows_loaded,
             columns_mapped, columns_unmapped, column_mapping,
             file_metadata, anomalies_found, anomaly_details, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        filename, sheet, table, rows,
        mapped_count, unmapped_count,
        json.dumps(mapping, default=str),
        json.dumps(metadata, default=str),
        anomaly_count,
        json.dumps(anomalies, default=str) if anomalies else None,
        "SUCCESS"
    ))

    upload_id = cursor.lastrowid
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return upload_id


def get_upload_history() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT id, filename, sheet_name, target_table, rows_loaded,
               columns_mapped, columns_unmapped, anomalies_found,
               status, created_at
        FROM upload_history
        ORDER BY created_at DESC
        LIMIT 50
    """), engine)

##Database schema for LLM context
SCHEMA_CONTEXT = """
Database tables and their columns:

1. parts_master (material specifications per part)
   - part_number VARCHAR(100) PK
   - material_from_drawing VARCHAR(500)
   - alternate_material VARCHAR(500)
   - heat_treatment VARCHAR(200)
   - coating VARCHAR(200)
   - classification VARCHAR(100)
   - sub_class, family, form, grade VARCHAR
   - cad_weight_lbs DECIMAL(10,4)

2. supplier_parts (supplier-part relationships with cost)
   - part_number VARCHAR(100)
   - supplier_id VARCHAR(100)
   - supplier_name VARCHAR(200)
   - plant VARCHAR(50)
   - commodity VARCHAR(200)
   - coo_code VARCHAR(10)
   - coo_name VARCHAR(200)
   - fx_type VARCHAR(10)
   - unit_cost DECIMAL(15,4)
   - snapshot_date DATE
   - source_file VARCHAR(200)
   - UNIQUE KEY (part_number, supplier_id, snapshot_date)

3. part_weights (invoice weights)
   - part_number VARCHAR(100)
   - supplier VARCHAR(200)
   - weight_from_invoice DECIMAL(10,4)
   - material_from_drawing VARCHAR(500)
   - class_pbi VARCHAR(100)

4. monthly_costs (monthly cost/receipt/MRP data)
   - part_number VARCHAR(100)
   - supplier_id VARCHAR(100)
   - year INT
   - month INT
   - month_cost DECIMAL(15,4)
   - month_receipts DECIMAL(15,4)
   - month_recd_spend DECIMAL(15,4)
   - mrp_qty DECIMAL(15,4)
   - mrp_spend DECIMAL(15,4)
   - UNIQUE KEY (part_number, supplier_id, year, month)

5. surcharge_monthly (monthly surcharge breakdown)
   - part_number VARCHAR(100)
   - invoiced_part_number VARCHAR(100)
   - supplier VARCHAR(200)
   - destination_plant VARCHAR(100)
   - year INT, month INT
   - material VARCHAR(200)  
   - raw_material_index VARCHAR(500)
   - scrap_index VARCHAR(500)
   - surcharge_weight_lbs DECIMAL(10,4)
   - raw_material_base_cost DECIMAL(15,6)
   - index_cost DECIMAL(15,6)
   - part_surcharge DECIMAL(15,4)
   - scrap_weight_lbs DECIMAL(10,4)
   - scrap_index_cost DECIMAL(15,6)
   - scrap_surcharge DECIMAL(15,4)
   - total_surcharge DECIMAL(15,4)
   - quantity INT
   - total_surcharge_cost DECIMAL(15,4)
   - base_material VARCHAR(200)
   - source_tier VARCHAR(5)
   - UNIQUE KEY (part_number, supplier, year, month, source_tier)
"""

analysis_prompt = """You are a procurement data analyst. Analyze this uploaded Excel file and determine:

1. Which database table(s) this data should go into
2. How each column in the file maps to database columns
3. Any data transformations needed (e.g., monthly columns need unpivoting, date parsing, name normalization)
4. What year/snapshot_date to assign
5. Any data quality concerns

DATABASE SCHEMA:
{schema}

{rag_context}

FILE INFO:
- Filename: {filename}
- Sheet: {sheet_name}
- Total rows: {total_rows}
- Columns: {columns}

SAMPLE DATA (first 5 rows):
{sample_data}

Return ONLY valid JSON:
{{
    "target_table": "table_name",
    "confidence": 0.95,
    "file_description": "Brief description of what this file contains",
    "column_mapping": {{
        "source_column_name": {{
            "target_column": "db_column_name",
            "transform": "none|strip|numeric|date|uppercase|lowercase|strip_inc",
            "notes": "any notes"
        }}
    }},
    "unmapped_columns": ["columns that don't map to any DB field"],
    "monthly_unpivot": {{
        "needed": true/false,
        "pattern": "description of monthly column pattern if applicable",
        "year": 2025,
        "metric_columns": {{
            "cost_prefix": "JanCost, FebCost...",
            "receipts_prefix": "JanRcpts, FebRcpts..."
        }}
    }},
    "metadata": {{
        "snapshot_date": "2025-07-01 or null",
        "source_tier": "T1 or T2 or null",
        "data_year": 2025
    }},
    "warnings": ["list of data quality concerns"],
    "row_estimate": "approximate rows after transformation"
}}

Be specific about column mappings. If a column doesn't match any DB field, put it in unmapped_columns.
If monthly columns (Jan-Dec) exist, set monthly_unpivot.needed = true and describe the pattern."""



def get_similar_mappings(filename: str, columns: list, limit: int = 3) -> list:
    #Retrieve past successful mappings for similar files (RAG context)
    try:
        history = pd.read_sql(text("""
            SELECT filename, sheet_name, target_table, rows_loaded,
                   column_mapping, file_metadata
            FROM upload_history
            WHERE status = 'SUCCESS' AND column_mapping IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 20
        """), engine)

        if history.empty:
            return []

        # Score similarity based on column name overlap
        scored = []
        upload_cols = set(c.lower().strip() for c in columns)

        for _, row in history.iterrows():
            try:
                past_mapping = json.loads(row["column_mapping"]) if isinstance(row["column_mapping"], str) else row["column_mapping"]
                past_cols = set(k.lower().strip() for k in past_mapping.keys())

                # Jaccard similarity
                intersection = len(upload_cols & past_cols)
                union = len(upload_cols | past_cols)
                col_similarity = intersection / union if union > 0 else 0

                # Filename similarity bonus
                name_bonus = 0.2 if any(
                    word in filename.lower()
                    for word in row["filename"].lower().split("_")
                    if len(word) > 3
                ) else 0

                score = col_similarity + name_bonus

                if score > 0.1:
                    scored.append({
                        "filename": row["filename"],
                        "sheet": row["sheet_name"],
                        "target_table": row["target_table"],
                        "rows": row["rows_loaded"],
                        "mapping": past_mapping,
                        "metadata": json.loads(row["file_metadata"]) if isinstance(row["file_metadata"], str) else row["file_metadata"],
                        "similarity": round(score, 2),
                    })
            except Exception:
                continue

        # Return top matches
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]

    except Exception:
        return []


def detect_anomalies(df: pd.DataFrame, target_table: str, mapping: dict) -> list:
    #Scan uploaded data for anomalies before loading
    anomalies = []

    #1. Missing critical fields
    part_col = None
    for src, config in mapping.items():
        if config.get("target_column") == "part_number" and src in df.columns:
            part_col = src
            break

    if part_col:
        null_parts = df[part_col].isna().sum()
        empty_parts = (df[part_col].astype(str).str.strip() == "").sum()
        total_missing = null_parts + empty_parts
        if total_missing > 0:
            anomalies.append({
                "type": "MISSING_DATA",
                "severity": "HIGH",
                "message": f"{total_missing} rows have missing part_number ({total_missing/len(df)*100:.1f}% of data)",
                "affected_rows": int(total_missing),
            })

    #2 Duplicate detection
    key_cols = []
    for src, config in mapping.items():
        if config.get("target_column") in ("part_number", "supplier_id", "supplier") and src in df.columns:
            key_cols.append(src)

    if len(key_cols) >= 2:
        dupes = df.duplicated(subset=key_cols, keep=False).sum()
        if dupes > 0:
            anomalies.append({
                "type": "DUPLICATES",
                "severity": "MEDIUM",
                "message": f"{dupes} duplicate rows found on ({', '.join(key_cols)}). Upsert will keep latest values.",
                "affected_rows": int(dupes),
            })

    #3 Numeric outliers (3std rule)
    numeric_targets = ["unit_cost", "index_cost", "part_surcharge", "total_surcharge",
                       "total_surcharge_cost", "month_cost", "weight_from_invoice",
                       "cad_weight_lbs", "surcharge_weight_lbs", "raw_material_base_cost"]

    for src, config in mapping.items():
        target = config.get("target_column", "")
        if target not in numeric_targets or src not in df.columns:
            continue

        values = pd.to_numeric(df[src], errors="coerce").dropna()
        if len(values) < 10:
            continue

        mean = values.mean()
        std = values.std()
        if std == 0:
            continue

        outliers = values[abs(values - mean) > 3 * std]
        if len(outliers) > 0:
            anomalies.append({
                "type": "OUTLIER",
                "severity": "MEDIUM",
                "message": f"{len(outliers)} outliers in '{src}' → `{target}` (beyond 3std). "
                           f"Range: ${values.min():,.2f} to ${values.max():,.2f}, "
                           f"Mean: ${mean:,.2f}, Outlier values: {outliers.head(3).tolist()}",
                "affected_rows": int(len(outliers)),
            })

    #4 Negative values where positives expected
    positive_cols = ["unit_cost", "quantity", "weight_from_invoice",
                     "cad_weight_lbs", "surcharge_weight_lbs"]

    for src, config in mapping.items():
        target = config.get("target_column", "")
        if target not in positive_cols or src not in df.columns:
            continue

        values = pd.to_numeric(df[src], errors="coerce").dropna()
        negatives = (values < 0).sum()
        if negatives > 0:
            anomalies.append({
                "type": "NEGATIVE_VALUE",
                "severity": "MEDIUM",
                "message": f"{negatives} negative values in '{src}' → `{target}` (expected positive)",
                "affected_rows": int(negatives),
            })

    #5 New suppliers/materials not in system
    supplier_col = None
    for src, config in mapping.items():
        if config.get("target_column") in ("supplier", "supplier_name") and src in df.columns:
            supplier_col = src
            break

    if supplier_col:
        uploaded_suppliers = set(df[supplier_col].dropna().astype(str).str.strip().str.replace(
            r'\s+INC$', '', regex=True).unique())

        with engine.connect() as conn:
            existing = conn.execute(text(
                "SELECT DISTINCT supplier FROM surcharge_monthly"
            )).fetchall()
            existing_suppliers = set(r[0] for r in existing if r[0])

        new_suppliers = uploaded_suppliers - existing_suppliers
        if new_suppliers and len(new_suppliers) < 50:
            anomalies.append({
                "type": "NEW_SUPPLIER",
                "severity": "LOW",
                "message": f"{len(new_suppliers)} new supplier(s) not in system: {', '.join(list(new_suppliers)[:5])}{'...' if len(new_suppliers) > 5 else ''}",
                "affected_rows": int(len(new_suppliers)),
            })

    material_col = None
    for src, config in mapping.items():
        if config.get("target_column") in ("material_from_drawing", "material") and src in df.columns:
            material_col = src
            break

    if material_col:
        uploaded_materials = set(df[material_col].dropna().astype(str).str.strip().unique())

        with engine.connect() as conn:
            existing = conn.execute(text(
                "SELECT DISTINCT material_from_drawing FROM parts_master WHERE material_from_drawing IS NOT NULL"
            )).fetchall()
            existing_materials = set(r[0] for r in existing if r[0])

        new_materials = uploaded_materials - existing_materials
        if new_materials and len(new_materials) < 50:
            anomalies.append({
                "type": "NEW_MATERIAL",
                "severity": "LOW",
                "message": f"{len(new_materials)} new material(s) not classified: {', '.join(list(new_materials)[:5])}{'...' if len(new_materials) > 5 else ''}. "
                           f"Run classify_material.py after upload to classify them.",
                "affected_rows": int(len(new_materials)),
            })

    #6 Compare with existing data (price deviation)
    cost_col = None
    for src, config in mapping.items():
        if config.get("target_column") == "unit_cost" and src in df.columns:
            cost_col = src
            break

    if cost_col and part_col:
        uploaded_costs = df[[part_col, cost_col]].copy()
        uploaded_costs[cost_col] = pd.to_numeric(uploaded_costs[cost_col], errors="coerce")
        uploaded_costs = uploaded_costs.dropna()

        if len(uploaded_costs) > 0:
            sample_parts = uploaded_costs[part_col].head(100).tolist()
            parts_str = "', '".join([str(p).strip() for p in sample_parts])

            try:
                existing_costs = pd.read_sql(text(f"""
                    SELECT part_number, AVG(unit_cost) AS avg_cost
                    FROM supplier_parts
                    WHERE part_number IN ('{parts_str}')
                      AND unit_cost IS NOT NULL AND unit_cost > 0
                    GROUP BY part_number
                """), engine)

                if not existing_costs.empty:
                    merged = uploaded_costs.merge(
                        existing_costs,
                        left_on=part_col,
                        right_on="part_number",
                        how="inner"
                    )

                    if len(merged) > 0:
                        merged["pct_change"] = abs(
                            (pd.to_numeric(merged[cost_col]) - merged["avg_cost"]) / merged["avg_cost"]
                        ) * 100

                        big_changes = merged[merged["pct_change"] > 50]
                        if len(big_changes) > 0:
                            worst = big_changes.nlargest(3, "pct_change")
                            details = "; ".join([
                                f"Part {r[part_col]}: ${float(r[cost_col]):,.2f} vs existing ${r['avg_cost']:,.2f} ({r['pct_change']:.0f}%)"
                                for _, r in worst.iterrows()
                            ])
                            anomalies.append({
                                "type": "PRICE_DEVIATION",
                                "severity": "HIGH",
                                "message": f"{len(big_changes)} parts have >50% price change vs existing data. "
                                           f"Top deviations: {details}",
                                "affected_rows": int(len(big_changes)),
                            })
            except Exception:
                pass  # Non-critical — skip if comparison fails

    #7 Empty columns
    for src, config in mapping.items():
        if src not in df.columns:
            continue
        null_pct = (df[src].isna().sum() + (df[src].astype(str).str.strip() == "").sum()) / len(df) * 100
        if null_pct > 90:
            anomalies.append({
                "type": "EMPTY_COLUMN",
                "severity": "LOW",
                "message": f"Column '{src}' → `{config.get('target_column')}` is {null_pct:.0f}% empty",
                "affected_rows": 0,
            })

    return anomalies

def fuzzy_match_and_fix(df: pd.DataFrame, mapping: dict) -> tuple:
    
 

    fixes = []
    fixed_df = df.copy()

    #1. Fix supplier names 
    supplier_col = None
    for src, config in mapping.items():
        if config.get("target_column") in ("supplier", "supplier_name") and src in df.columns:
            supplier_col = src
            break

    if supplier_col:
        # Get existing suppliers from DB
        with engine.connect() as conn:
            existing = conn.execute(text(
                "SELECT DISTINCT supplier FROM surcharge_monthly "
                "UNION "
                "SELECT DISTINCT supplier_name FROM supplier_parts"
            )).fetchall()
        existing_suppliers = [r[0] for r in existing if r[0] and r[0].strip()]

        if existing_suppliers:
            uploaded_suppliers = fixed_df[supplier_col].dropna().unique()

            for uploaded_name in uploaded_suppliers:
                clean_name = str(uploaded_name).strip()
                if not clean_name or clean_name.upper() in ("NAN", "NONE"):
                    continue

                # Strip INC for comparison
                compare_name = clean_name.replace(" INC", "").strip()

                # Check exact match first
                exact_match = False
                for existing in existing_suppliers:
                    existing_clean = existing.replace(" INC", "").strip()
                    if compare_name.upper() == existing_clean.upper():
                        exact_match = True
                        if compare_name != existing_clean:
                            # Case difference — fix silently
                            fixed_df[supplier_col] = fixed_df[supplier_col].replace(
                                uploaded_name, existing_clean)
                        break

                if exact_match:
                    continue

                # Fuzzy match
                existing_clean_list = [s.replace(" INC", "").strip() for s in existing_suppliers]
                matches = get_close_matches(compare_name.upper(),
                                           [s.upper() for s in existing_clean_list],
                                           n=1, cutoff=0.75)

                if matches:
                    # Find the original-case version
                    match_idx = [s.upper() for s in existing_clean_list].index(matches[0])
                    correct_name = existing_clean_list[match_idx]
                    similarity = SequenceMatcher(
                        None, compare_name.upper(), matches[0]
                    ).ratio()

                    fixes.append({
                        "type": "SUPPLIER_TYPO",
                        "original": clean_name,
                        "corrected": correct_name,
                        "similarity": round(similarity * 100, 1),
                        "column": supplier_col,
                    })

                    # Apply fix
                    fixed_df[supplier_col] = fixed_df[supplier_col].replace(
                        uploaded_name, correct_name)

    #2 Fix material names
    material_col = None
    for src, config in mapping.items():
        if config.get("target_column") in ("material_from_drawing", "material") and src in df.columns:
            material_col = src
            break

    if material_col:
        with engine.connect() as conn:
            existing = conn.execute(text(
                "SELECT DISTINCT material_from_drawing FROM parts_master "
                "WHERE material_from_drawing IS NOT NULL "
                "UNION "
                "SELECT DISTINCT material FROM surcharge_monthly "
                "WHERE material IS NOT NULL"
            )).fetchall()
        existing_materials = [r[0] for r in existing if r[0] and r[0].strip()]

        if existing_materials:
            uploaded_materials = fixed_df[material_col].dropna().unique()

            for uploaded_name in uploaded_materials:
                clean_name = str(uploaded_name).strip()
                if not clean_name or clean_name.upper() in ("NAN", "NONE", "NULL", "N/A", "NOT AVAILABLE"):
                    continue

                # Check exact match
                if clean_name.upper() in [m.upper() for m in existing_materials]:
                    continue

                # Fuzzy match
                matches = get_close_matches(clean_name.upper(),
                                           [m.upper() for m in existing_materials],
                                           n=1, cutoff=0.70)

                if matches:
                    match_idx = [m.upper() for m in existing_materials].index(matches[0])
                    correct_name = existing_materials[match_idx]
                    similarity = SequenceMatcher(
                        None, clean_name.upper(), matches[0]
                    ).ratio()

                    fixes.append({
                        "type": "MATERIAL_TYPO",
                        "original": clean_name,
                        "corrected": correct_name,
                        "similarity": round(similarity * 100, 1),
                        "column": material_col,
                    })

                    # Apply fix
                    fixed_df[material_col] = fixed_df[material_col].replace(
                        uploaded_name, correct_name)

    #3 Fix part numbers (leading/trailing spaces, dashes)
    part_col = None
    for src, config in mapping.items():
        if config.get("target_column") == "part_number" and src in df.columns:
            part_col = src
            break

    if part_col:
        # Strip whitespace and normalize
        original_parts = fixed_df[part_col].copy()
        fixed_df[part_col] = fixed_df[part_col].astype(str).str.strip()

        changed = (original_parts != fixed_df[part_col]).sum()
        if changed > 0:
            fixes.append({
                "type": "PART_NUMBER_CLEANUP",
                "original": f"{changed} part numbers had whitespace",
                "corrected": "Stripped leading/trailing spaces",
                "similarity": 100.0,
                "column": part_col,
            })

    return fixed_df, fixes


def validate_with_llm(df: pd.DataFrame, anomalies: list, target_table: str, mapping: dict) -> str:
    #Second LLM call — reviews anomalies and transformed data for deeper issues

    if not os.getenv("GROQ_API_KEY"):
        return None

    anomaly_summary = "None detected" if not anomalies else "\n".join(
        [f"- [{a['severity']}] {a['type']}: {a['message']}" for a in anomalies]
    )

    # Get sample of transformed data
    try:
        preview = apply_transforms(df.head(10), mapping)
        preview_str = preview.to_string(index=False)
    except Exception:
        preview_str = "Could not generate preview"

    prompt = f"""You are a procurement data quality expert. Review this upload before it goes into the database.

TARGET TABLE: {target_table}
TOTAL ROWS: {len(df)}

ANOMALIES ALREADY DETECTED:
{anomaly_summary}

TRANSFORMED DATA SAMPLE (first 10 rows):
{preview_str}

Review and provide:
1. Are the anomalies serious enough to block the upload?
2. Any additional issues you spot in the data?
3. Specific rows or values that look suspicious?
4. Overall recommendation: PROCEED, PROCEED_WITH_CAUTION, or BLOCK

Return ONLY valid JSON:
{{
    "recommendation": "PROCEED|PROCEED_WITH_CAUTION|BLOCK",
    "confidence": 0.9,
    "summary": "One sentence summary",
    "additional_issues": ["list of any new issues found"],
    "suspicious_values": ["specific values that look wrong"],
    "advice": "What the user should do"
}}"""

    try:
        raw = call_llm(prompt)
        result = parse_json_response(raw)
        return result
    except Exception:
        return None

def run_auto_pipeline(target_table: str, df: pd.DataFrame, mapping: dict):
    """
    After successful upload, automatically trigger downstream updates:
    1. Detect new materials → classify
    2. Update commodity map
    3. Recalculate affected data
    4. Generate alerts if thresholds breached
    """
    pipeline_log = []

    #1 Detect and classify new materials
    material_col = None
    for src, config in mapping.items():
        if config.get("target_column") in ("material_from_drawing", "material"):
            material_col = src
            break

    if material_col and material_col in df.columns:
        uploaded_materials = set(df[material_col].dropna().astype(str).str.strip().unique())

        with engine.connect() as conn:
            classified = conn.execute(text(
                "SELECT material_from_drawing FROM material_classification_ai"
            )).fetchall()
            classified_set = set(r[0] for r in classified if r[0])

        new_materials = uploaded_materials - classified_set
        # Remove empty strings and common non-materials
        new_materials = {m for m in new_materials if m and m.strip() and
                        m.upper() not in ("NAN", "NONE", "NULL", "N/A", "")}

        if new_materials:
            pipeline_log.append(f"{len(new_materials)} new material(s) detected: {', '.join(list(new_materials)[:5])}")

            # Auto-classify using Groq
            try:
                classify_prompt = f"""Classify these procurement materials for commodity mapping.

Materials: {json.dumps(list(new_materials))}

For each material, return:
- base_material_name: standardized name
- primary_commodity: one of [LME_COPPER, LME_ALUMINIUM, LME_ZINC, LME_NICKEL, MWUS_HR_COIL, IRON_ORE, SCRAP_STEEL, NATURAL_GAS, NONE]
- secondary_commodity: same list or NONE
- confidence: 0.0 to 1.0

Return ONLY valid JSON array:
[{{"material": "...", "base_material_name": "...", "primary_commodity": "...", "secondary_commodity": "...", "confidence": 0.9}}]"""

                raw = call_llm(classify_prompt)
                classifications = parse_json_response(raw)

                if isinstance(classifications, list):
                    conn_raw = engine.raw_connection()
                    cursor = conn_raw.cursor()

                    for c in classifications:
                        cursor.execute("""
                            INSERT INTO material_classification_ai
                                (material_from_drawing, base_material_name,
                                 primary_commodity, secondary_commodity, confidence)
                            VALUES (%s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                base_material_name = VALUES(base_material_name),
                                primary_commodity = VALUES(primary_commodity),
                                secondary_commodity = VALUES(secondary_commodity),
                                confidence = VALUES(confidence)
                        """, (
                            c.get("material"),
                            c.get("base_material_name"),
                            c.get("primary_commodity", "NONE"),
                            c.get("secondary_commodity", "NONE"),
                            c.get("confidence", 0.5),
                        ))

                    conn_raw.commit()
                    cursor.close()
                    conn_raw.close()

                    pipeline_log.append(f"Classified {len(classifications)} new material(s) via AI")

                    #2 Update commodity map for new materials
                    conn_raw = engine.raw_connection()
                    cursor = conn_raw.cursor()
                    map_count = 0

                    for c in classifications:
                        prim = c.get("primary_commodity", "NONE")
                        sec = c.get("secondary_commodity", "NONE")
                        mat = c.get("material")

                        if prim == "NONE" or not mat:
                            continue

                        if sec == "NONE" or sec is None:
                            cursor.execute("""
                                INSERT INTO material_commodity_map
                                    (material_from_drawing, commodity_code, weight, effective_from)
                                VALUES (%s, %s, 1.0000, '2024-01-01')
                                ON DUPLICATE KEY UPDATE weight = VALUES(weight)
                            """, (mat, prim))
                            map_count += 1
                        else:
                            cursor.execute("""
                                INSERT INTO material_commodity_map
                                    (material_from_drawing, commodity_code, weight, effective_from)
                                VALUES (%s, %s, 0.7000, '2024-01-01')
                                ON DUPLICATE KEY UPDATE weight = VALUES(weight)
                            """, (mat, prim))
                            cursor.execute("""
                                INSERT INTO material_commodity_map
                                    (material_from_drawing, commodity_code, weight, effective_from)
                                VALUES (%s, %s, 0.3000, '2024-01-01')
                                ON DUPLICATE KEY UPDATE weight = VALUES(weight)
                            """, (mat, sec))
                            map_count += 2

                    conn_raw.commit()
                    cursor.close()
                    conn_raw.close()

                    if map_count > 0:
                        pipeline_log.append(f"Added {map_count} commodity mapping(s)")

            except Exception as e:
                pipeline_log.append(f"Auto-classification failed (Groq unavailable): {str(e)[:100]}")
                pipeline_log.append(f"Run `python classify_material.py` manually to classify new materials")
        else:
            pipeline_log.append("No new materials — all already classified")
    else:
        pipeline_log.append("No material column in upload — skipping classification")

    #3 Check for price alerts
    if target_table in ("surcharge_monthly", "supplier_parts", "monthly_costs"):
        try:
            cost_col = None
            for src, config in mapping.items():
                if config.get("target_column") in ("unit_cost", "index_cost", "total_surcharge_cost"):
                    cost_col = src
                    break

            if cost_col and cost_col in df.columns:
                values = pd.to_numeric(df[cost_col], errors="coerce").dropna()
                if len(values) > 0:
                    mean_val = values.mean()
                    max_val = values.max()
                    min_val = values.min()

                    if max_val > mean_val * 3:
                        pipeline_log.append(
                            f"Price alert: max value ${max_val:,.2f} is >3x average ${mean_val:,.2f}"
                        )

                    if min_val < 0 and target_table != "surcharge_monthly":
                        pipeline_log.append(
                            f"Price alert: negative values found (min: ${min_val:,.2f})"
                        )
        except Exception:
            pass

    #4 Summary
    if not pipeline_log:
        pipeline_log.append("No downstream updates needed")

    return pipeline_log

def call_llm(prompt: str) -> str:
    
    
    from utils.llm import call_llm
    return call_llm(
        "You are a procurement data mapping expert. Return ONLY valid JSON.",
        prompt
    )

    

    # from groq import Groq
    # api_key = os.getenv("GROQ_API_KEY")
    # if not api_key:
    #     raise ValueError("GROQ_API_KEY not found in .env")

    # client = Groq(api_key=api_key)
    # response = client.chat.completions.create(
    #     model="qwen/qwen3-32b",
    #     messages=[
    #         {"role": "system", "content": "You are a procurement data mapping expert. Return ONLY valid JSON."},
    #         {"role": "user", "content": prompt}
    #     ],
    #     temperature=0.1,
    #     max_tokens=4096,
    # )
    # return response.choices[0].message.content


def parse_json_response(raw: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"```json\s*", "", cleaned)
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON found in response")

    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(cleaned, start)
    return result


def load_to_table(df: pd.DataFrame, table: str, mapping: dict,  metadata: dict, monthly_config: dict) -> int:
    #Load mapped DataFrame to the target MySQL table

    if table == "surcharge_monthly":
        return load_surcharge(df, mapping, metadata, monthly_config)
    elif table == "supplier_parts":
        return load_supplier_parts(df, mapping, metadata)
    elif table == "parts_master":
        return load_parts_master(df, mapping)
    elif table == "part_weights":
        return load_part_weights(df, mapping)
    elif table == "monthly_costs":
        return load_monthly_costs(df, mapping, metadata, monthly_config)
    else:
        st.error(f"Unknown table: {table}")
        return 0


def apply_transforms(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    #Apply column mapping and transformations
    result = pd.DataFrame()

    for src_col, config in mapping.items():
        if src_col not in df.columns:
            continue

        target = config["target_column"]
        transform = config.get("transform", "none")

        col_data = df[src_col].copy()

        if transform == "strip":
            col_data = col_data.astype(str).str.strip()
        elif transform == "numeric":
            col_data = pd.to_numeric(col_data, errors="coerce")
        elif transform == "date":
            col_data = pd.to_datetime(col_data, errors="coerce")
        elif transform == "uppercase":
            col_data = col_data.astype(str).str.strip().str.upper()
        elif transform == "lowercase":
            col_data = col_data.astype(str).str.strip().str.lower()
        elif transform == "strip_inc":
            col_data = col_data.astype(str).str.strip().str.replace(r'\s+INC$', '', regex=True)

        result[target] = col_data

    return result


def load_parts_master(df: pd.DataFrame, mapping: dict) -> int:
    mapped = apply_transforms(df, mapping)
    if "part_number" not in mapped.columns:
        st.error("No part_number column mapped")
        return 0

    mapped = mapped.dropna(subset=["part_number"])
    mapped["part_number"] = mapped["part_number"].astype(str).str.strip()

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    cols = [c for c in mapped.columns if c != "created_at"]
    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join([f"{c} = VALUES({c})" for c in cols if c != "part_number"])

    sql = f"""
        INSERT INTO parts_master ({", ".join(cols)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
    """

    data = []
    for _, row in mapped[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return len(data)


def load_supplier_parts(df: pd.DataFrame, mapping: dict, metadata: dict) -> int:
    mapped = apply_transforms(df, mapping)
    if "part_number" not in mapped.columns or "supplier_id" not in mapped.columns:
        st.error("Missing part_number or supplier_id mapping")
        return 0

    mapped = mapped.dropna(subset=["part_number", "supplier_id"])
    mapped["part_number"] = mapped["part_number"].astype(str).str.strip()
    mapped["supplier_id"] = mapped["supplier_id"].astype(str).str.strip()

    if "snapshot_date" not in mapped.columns and metadata.get("snapshot_date"):
        mapped["snapshot_date"] = metadata["snapshot_date"]

    if "source_file" not in mapped.columns:
        mapped["source_file"] = "smart_upload"

    if "unit_cost" in mapped.columns:
        mapped["unit_cost"] = pd.to_numeric(mapped["unit_cost"], errors="coerce")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    cols = [c for c in mapped.columns]
    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join([
        f"{c} = VALUES({c})" for c in cols
        if c not in ("part_number", "supplier_id", "snapshot_date")
    ])

    sql = f"""
        INSERT INTO supplier_parts ({", ".join(cols)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
    """

    data = []
    for _, row in mapped[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return len(data)


def load_part_weights(df: pd.DataFrame, mapping: dict) -> int:
    mapped = apply_transforms(df, mapping)
    if "part_number" not in mapped.columns:
        st.error("No part_number column mapped")
        return 0

    mapped = mapped.dropna(subset=["part_number"])

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    # Truncate + reload (no unique key)
    cursor.execute("TRUNCATE TABLE part_weights")

    cols = list(mapped.columns)
    placeholders = ", ".join(["%s"] * len(cols))

    sql = f"INSERT INTO part_weights ({', '.join(cols)}) VALUES ({placeholders})"

    data = []
    for _, row in mapped[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return len(data)


def load_surcharge(df: pd.DataFrame, mapping: dict, metadata: dict,
                   monthly_config: dict) -> int:
    """Load surcharge data — handles monthly unpivot if needed"""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_num = {m: i + 1 for i, m in enumerate(months)}

    mapped = apply_transforms(df, mapping)

    if monthly_config.get("needed"):
        # Unpivot monthly columns
        year = metadata.get("data_year", 2025)
        tier = metadata.get("source_tier", "T1")

        static_cols = [c for c in mapped.columns]
        all_rows = []

        for m in months:
            mn = month_num[m]
            row_data = mapped[static_cols].copy()
            row_data["year"] = year
            row_data["month"] = mn
            row_data["source_tier"] = tier

            # Map monthly columns
            for col_pattern in ["index_cost", "part_surcharge", "scrap_index_cost",
                                "scrap_surcharge", "total_surcharge", "quantity",
                                "total_surcharge_cost"]:
                # Find matching source column for this month
                for src_col in df.columns:
                    if m.lower() in src_col.lower() and col_pattern.replace("_", " ").lower() in src_col.lower():
                        row_data[col_pattern] = pd.to_numeric(df[src_col], errors="coerce")
                        break

            all_rows.append(row_data)

        if all_rows:
            final = pd.concat(all_rows, ignore_index=True)
        else:
            final = mapped
    else:
        final = mapped
        if "source_tier" not in final.columns and metadata.get("source_tier"):
            final["source_tier"] = metadata["source_tier"]

    final = final.dropna(subset=["part_number", "supplier"])

    # Strip INC from supplier names
    if "supplier" in final.columns:
        final["supplier"] = final["supplier"].astype(str).str.strip().str.replace(
            r'\s+INC$', '', regex=True)

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    cols = [c for c in final.columns if c in [
        "part_number", "invoiced_part_number", "supplier", "destination_plant",
        "year", "month", "material", "raw_material_index", "scrap_index",
        "surcharge_weight_lbs", "raw_material_base_cost", "index_cost",
        "part_surcharge", "scrap_weight_lbs", "scrap_index_cost", "scrap_surcharge",
        "total_surcharge", "quantity", "total_surcharge_cost", "base_material",
        "source_tier"
    ]]

    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join([
        f"{c} = VALUES({c})" for c in cols
        if c not in ("part_number", "supplier", "year", "month", "source_tier")
    ])

    sql = f"""
        INSERT INTO surcharge_monthly ({", ".join(cols)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
    """

    data = []
    for _, row in final[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return len(data)


def load_monthly_costs(df: pd.DataFrame, mapping: dict, metadata: dict,
                       monthly_config: dict) -> int:
    #Load monthly costs — handles unpivot of JanCost-DecCost columns
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_num = {m: i + 1 for i, m in enumerate(months)}

    mapped = apply_transforms(df, mapping)

    if monthly_config.get("needed"):
        cost_year = metadata.get("data_year", 2025)
        all_rows = []

        for m in months:
            mn = month_num[m]
            row_data = pd.DataFrame()
            row_data["part_number"] = mapped.get("part_number", pd.Series())
            row_data["supplier_id"] = mapped.get("supplier_id", pd.Series())
            row_data["year"] = cost_year
            row_data["month"] = mn

            # Find cost column
            cost_col = f"{m}Cost"
            if cost_col in df.columns:
                row_data["month_cost"] = pd.to_numeric(df[cost_col], errors="coerce")

            # Find receipts column
            rcpts_col = f"{m}Rcpts"
            if rcpts_col in df.columns:
                row_data["month_receipts"] = pd.to_numeric(df[rcpts_col], errors="coerce")

            spend_col = f"{m}RecdSpend"
            if spend_col in df.columns:
                row_data["month_recd_spend"] = pd.to_numeric(df[spend_col], errors="coerce")

            all_rows.append(row_data)

        if all_rows:
            final = pd.concat(all_rows, ignore_index=True)
        else:
            final = mapped
    else:
        final = mapped

    final = final.dropna(subset=["part_number", "supplier_id"])

    # Drop rows where all metrics are null
    metric_cols = ["month_cost", "month_receipts", "month_recd_spend", "mrp_qty", "mrp_spend"]
    existing_metrics = [c for c in metric_cols if c in final.columns]
    if existing_metrics:
        final = final.dropna(subset=existing_metrics, how="all")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    cols = [c for c in final.columns if c in [
        "part_number", "supplier_id", "year", "month",
        "month_cost", "month_receipts", "month_recd_spend",
        "mrp_qty", "mrp_spend"
    ]]

    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join([
        f"{c} = COALESCE(VALUES({c}), {c})" for c in cols
        if c not in ("part_number", "supplier_id", "year", "month")
    ])

    sql = f"""
        INSERT INTO monthly_costs ({", ".join(cols)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
    """

    data = []
    for _, row in final[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()
    return len(data)



def render():
    st.markdown('<p class="main-header">Upload</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Upload Files</p>',
                unsafe_allow_html=True)

    # Check API key
    if not os.getenv("GROQ_API_KEY"):
        st.error("GROQ_API_KEY not found in .env. Upload requires Groq API.")
        return

    st.subheader("Step 1: Upload File")
    uploaded_file = st.file_uploader(
        "Drop an Excel file here",
        type=["xlsx", "xls"],
        help="Supports supplier parts lists, surcharge summaries, material master data, weight data"
    )

    if not uploaded_file:
        st.info("Upload a file to get started.")

        st.markdown("---")
        st.markdown("###Supported File Types")
        st.markdown("""
        - **Supplier Parts Lists** → `supplier_parts` + `monthly_costs`
        - **Supplier Summary By Year** → `surcharge_monthly`
        - **Parts Raw Materials Master** → `parts_master`
        - **Gross Weight Data** → `part_weights`
        - **Any procurement Excel** — AI will analyze and map it
        """)
        return

    st.subheader("Step 2: AI Analysis")

    try:
        xls = pd.ExcelFile(uploaded_file)
        sheets = xls.sheet_names

        if len(sheets) > 1:
            selected_sheet = st.selectbox("Select sheet", sheets)
        else:
            selected_sheet = sheets[0]

        df = pd.read_excel(uploaded_file, sheet_name=selected_sheet, dtype=str)
        df.columns = df.columns.str.strip()

    except Exception as e:
        st.error(f"Error reading file: {e}")
        return

    # Show preview
    with st.expander("Raw Data Preview (first 10 rows)", expanded=True):
        st.dataframe(df.head(10), use_container_width=True)

    st.info(f"**{len(df)} rows** x **{len(df.columns)} columns** | Sheet: {selected_sheet}")
    # Show RAG matches if found
    similar_key = f"similar_{uploaded_file.name}_{selected_sheet}"
    if similar_key not in st.session_state:
        st.session_state[similar_key] = get_similar_mappings(uploaded_file.name, df.columns.tolist())

    similar_matches = st.session_state[similar_key]
    if similar_matches:
        with st.expander(f"RAG: Found {len(similar_matches)} similar past upload(s)"):
            for s in similar_matches:
                st.markdown(f"**{s['filename']}** → `{s['target_table']}` | "
                           f"{s['rows']} rows | Similarity: {s['similarity']:.0%}")
    else:
        st.caption("No similar past uploads — analyzing from scratch")

    analysis_key = f"analysis_{uploaded_file.name}_{selected_sheet}"

    if analysis_key not in st.session_state:
        with st.spinner("AI is analyzing your file structure..."):
            try:
                sample = df.head(5).to_string(index=False)
                # RAG: retrieve similar past mappings
                similar = get_similar_mappings(uploaded_file.name, df.columns.tolist())

                rag_context = ""
                if similar:
                    rag_context = "SIMILAR PAST UPLOADS (use these as reference for column mapping):\n"
                    for s in similar:
                        rag_context += f"\n--- Past Upload: {s['filename']} (sheet: {s['sheet']}) ---\n"
                        rag_context += f"Target table: {s['target_table']}\n"
                        rag_context += f"Rows loaded: {s['rows']}\n"
                        rag_context += f"Column mapping: {json.dumps(s['mapping'], indent=2)}\n"
                        if s.get('metadata'):
                            rag_context += f"Metadata: {json.dumps(s['metadata'])}\n"
                    rag_context += "\nUse the above mappings as strong reference. If this file has similar columns, apply the same mapping pattern.\n"
                else:
                    rag_context = "No similar past uploads found. Analyze from scratch using the database schema.\n"

                prompt = analysis_prompt.format(
                    schema=SCHEMA_CONTEXT,
                    rag_context=rag_context,
                    filename=uploaded_file.name,
                    sheet_name=selected_sheet,
                    total_rows=len(df),
                    columns=json.dumps(df.columns.tolist()),
                    sample_data=sample
                )

                raw = call_llm(prompt)
                analysis = parse_json_response(raw)
                st.session_state[analysis_key] = analysis

            except Exception as e:
                st.error(f"AI analysis failed: {e}")
                return

    analysis = st.session_state[analysis_key]


    st.markdown("---")
    st.subheader("Step 3: Review AI Mapping")

    # Summary cards
    c1, c2, c3 = st.columns(3)
    c1.metric("Target Table", analysis.get("target_table", "Unknown"))
    c2.metric("Confidence", f"{analysis.get('confidence', 0) * 100:.0f}%")
    c3.metric("Est. Rows", analysis.get("row_estimate", len(df)))

    st.info(f"**{analysis.get('file_description', 'Procurement data file')}**")

    # Column mapping table
    mapping = analysis.get("column_mapping", {})
    if mapping:
        st.markdown("#### Column Mapping")
        map_data = []
        for src, config in mapping.items():
            map_data.append({
                "Source Column": src,
                "→ DB Column": config.get("target_column", "—"),
                "Transform": config.get("transform", "none"),
                "Notes": config.get("notes", ""),
            })
        map_df = pd.DataFrame(map_data)
        st.dataframe(map_df, use_container_width=True, hide_index=True)

    # Unmapped columns
    unmapped = analysis.get("unmapped_columns", [])
    if unmapped:
        st.warning(f"**Unmapped columns** (will be ignored): {', '.join(unmapped)}")

    # Monthly unpivot
    monthly = analysis.get("monthly_unpivot", {})
    if monthly.get("needed"):
        st.info(f"**Monthly unpivot needed**: {monthly.get('pattern', 'Jan-Dec columns detected')}")

    # Metadata
    meta = analysis.get("metadata", {})
    if meta:
        st.markdown("#### Metadata")
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Snapshot Date", meta.get("snapshot_date", "Auto"))
        mc2.metric("Source Tier", meta.get("source_tier", "Auto"))
        mc3.metric("Data Year", meta.get("data_year", "Auto"))

    # Warnings
    warnings_list = analysis.get("warnings", [])
    if warnings_list:
        st.markdown("#### Data Quality Warnings")
        for w in warnings_list:
            st.warning(w)

    st.markdown("---")
    st.subheader("Step 4: Adjust (Optional)")


    st.markdown("---")
    st.subheader("Anomaly Detection")

    anomaly_key = f"anomalies_{uploaded_file.name}_{selected_sheet}"

    if anomaly_key not in st.session_state:
        with st.spinner("Scanning for anomalies..."):
            detected = detect_anomalies(df, analysis.get("target_table", ""), mapping)
            st.session_state[anomaly_key] = detected

    detected_anomalies = st.session_state[anomaly_key]

    if not detected_anomalies:
        st.success("No anomalies detected — data looks clean!")
    else:
        # Count by severity
        high = len([a for a in detected_anomalies if a["severity"] == "HIGH"])
        medium = len([a for a in detected_anomalies if a["severity"] == "MEDIUM"])
        low = len([a for a in detected_anomalies if a["severity"] == "LOW"])

        ac1, ac2, ac3, ac4 = st.columns(4)
        ac1.metric("Total Anomalies", len(detected_anomalies))
        ac2.metric("High", high)
        ac3.metric("Medium", medium)
        ac4.metric("Low", low)

        # Display each anomaly
        for a in sorted(detected_anomalies, key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["severity"]]):
            if a["severity"] == "HIGH":
                st.error(f"**{a['type']}** — {a['message']}")
            elif a["severity"] == "MEDIUM":
                st.warning(f"**{a['type']}** — {a['message']}")
            else:
                st.info(f"**{a['type']}** — {a['message']}")

        if high > 0:
            st.warning("High-severity anomalies detected. Review before confirming upload.")

    
    fuzzy_key = f"fuzzy_{uploaded_file.name}_{selected_sheet}"

    if fuzzy_key not in st.session_state:
        with st.spinner("Checking for misspellings and typos..."):
            fixed_df, fixes = fuzzy_match_and_fix(df, mapping)
            st.session_state[fuzzy_key] = {"fixed_df": fixed_df, "fixes": fixes}

    fuzzy_result = st.session_state[fuzzy_key]
    fixes = fuzzy_result["fixes"]

    if fixes:
        st.markdown("#### Auto-Corrections (Fuzzy Match)")
        st.caption("These misspellings were detected and will be auto-corrected on upload")

        fix_data = []
        for f in fixes:
            fix_data.append({
                "Type": f["type"],
                "Original": f["original"],
                "Corrected To": f["corrected"],
                "Match %": f"{f['similarity']}%",
            })

        fix_df = pd.DataFrame(fix_data)
        st.dataframe(fix_df, use_container_width=True, hide_index=True)

        # Let user accept or reject
        accept_fixes = st.checkbox("Accept all auto-corrections", value=True,
                                    key="accept_fuzzy")

        if accept_fixes:
            df = fuzzy_result["fixed_df"]
            st.success(f"{len(fixes)} correction(s) will be applied on upload")
        else:
            st.info("Corrections rejected — original values will be used")
    else:
        st.success("No misspellings detected — all names match existing data")


    #AI Validation Agent
    validation_key = f"validation_{uploaded_file.name}_{selected_sheet}"

    if detected_anomalies and len(detected_anomalies) > 0:
        if validation_key not in st.session_state:
            with st.spinner("AI Validation Agent reviewing anomalies..."):
                try:
                    validation = validate_with_llm(
                        df, detected_anomalies, analysis.get("target_table", ""),
                        mapping
                    )
                    st.session_state[validation_key] = validation
                except Exception:
                    st.session_state[validation_key] = None

        validation = st.session_state.get(validation_key)

        if validation:
            st.markdown("####AI Validation Agent")

            rec = validation.get("recommendation", "PROCEED")
            if rec == "BLOCK":
                st.error(f"**BLOCK** — {validation.get('summary', 'Critical issues found')}")
            elif rec == "PROCEED_WITH_CAUTION":
                st.warning(f"**PROCEED WITH CAUTION** — {validation.get('summary', 'Review recommended')}")
            else:
                st.success(f"**PROCEED** — {validation.get('summary', 'Data looks acceptable')}")

            if validation.get("additional_issues"):
                st.markdown("**Additional issues found:**")
                for issue in validation["additional_issues"]:
                    st.markdown(f"- {issue}")

            if validation.get("suspicious_values"):
                st.markdown("**Suspicious values:**")
                for sv in validation["suspicious_values"]:
                    st.markdown(f"- {sv}")

            if validation.get("advice"):
                st.info(f"**Advice:** {validation['advice']}")

    with st.expander("Override Settings"):
        override_table = st.selectbox(
            "Target Table",
            ["parts_master", "supplier_parts", "part_weights",
             "monthly_costs", "surcharge_monthly"],
            index=["parts_master", "supplier_parts", "part_weights",
                    "monthly_costs", "surcharge_monthly"].index(
                analysis.get("target_table", "supplier_parts")
            ) if analysis.get("target_table") in ["parts_master", "supplier_parts",
                "part_weights", "monthly_costs", "surcharge_monthly"] else 1
        )

        override_year = st.number_input("Data Year", value=meta.get("data_year", 2025), min_value=2020, max_value=2030)
        override_tier = st.selectbox("Source Tier", ["T1", "T2", "Auto"],
                                      index=["T1", "T2", "Auto"].index(
                                          meta.get("source_tier", "Auto")
                                      ) if meta.get("source_tier") in ["T1", "T2", "Auto"] else 2)
        override_snapshot = st.text_input("Snapshot Date (YYYY-MM-DD)",
                                           value=meta.get("snapshot_date", ""))

    st.markdown("---")
    st.subheader("Step 5: Confirm & Load")

    target_table = override_table if override_table else analysis.get("target_table")
    final_meta = {
        "data_year": override_year,
        "source_tier": override_tier if override_tier != "Auto" else meta.get("source_tier"),
        "snapshot_date": override_snapshot or meta.get("snapshot_date"),
    }

    # Preview transformed data
    with st.expander("Preview Transformed Data"):
        try:
            preview = apply_transforms(df.head(20), mapping)
            st.dataframe(preview, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"Preview error: {e}")

    # Confirmation
    col_a, col_b = st.columns(2)
    with col_a:
        confirmed = st.button(
            f"Confirm — Load {len(df)} rows into `{target_table}`",
            type="primary",
            use_container_width=True
        )
    with col_b:
        cancelled = st.button("Cancel", use_container_width=True)

    if cancelled:
        st.info("Upload cancelled. No data was loaded.")
        if analysis_key in st.session_state:
            del st.session_state[analysis_key]
        return

    if confirmed:
        with st.spinner(f"Loading data into `{target_table}`..."):
            try:
                loaded = load_to_table(
                    df, target_table, mapping, final_meta,
                    monthly or {}
                )

                
                if loaded > 0:
                    st.success(f"**{loaded} rows loaded** into `{target_table}`")

                    # Log to upload history
                    upload_id = log_upload(
                        filename=uploaded_file.name,
                        sheet=selected_sheet,
                        table=target_table,
                        rows=loaded,
                        mapping=mapping,
                        metadata={
                            **final_meta,
                            "unmapped_columns": unmapped
                        },
                        anomalies=st.session_state.get(anomaly_key, [])
                    )
                    st.caption(f"Upload logged (ID: {upload_id})")

                    # Clear all cached data so dashboards refresh
                    st.cache_data.clear()
                    st.balloons()

                    # Show post-load stats
                    with engine.connect() as conn:
                        count = conn.execute(
                            text(f"SELECT COUNT(*) FROM {target_table}")
                        ).scalar()
                    st.info(f"`{target_table}` now has **{count:,}** total rows")
                    
                    
                    # Run auto-pipeline
                    st.markdown("---")
                    st.subheader("Auto-Pipeline")
                    with st.spinner("Running downstream updates..."):
                        pipeline_results = run_auto_pipeline(target_table, df, mapping)

                    for msg in pipeline_results:
                        if "fail" in msg.lower() or "alert" in msg.lower():
                            st.warning(msg)
                        elif "skip" in msg.lower() or "manual" in msg.lower():
                            st.caption(msg)
                        elif "new" in msg.lower() or "detect" in msg.lower():
                            st.info(msg)
                        else:
                            st.success(msg)

                    



                    # Cleanup
                    if analysis_key in st.session_state:
                        del st.session_state[analysis_key]
                    if anomaly_key in st.session_state:
                        del st.session_state[anomaly_key]
                    
                    if validation_key in st.session_state:
                        del st.session_state[validation_key]
                        similar_key = f"similar_{uploaded_file.name}_{selected_sheet}"
                    if similar_key in st.session_state:
                        del st.session_state[similar_key]
                    
                    if fuzzy_key in st.session_state:
                        del st.session_state[fuzzy_key]

    

                else:
                    st.error("No rows were loaded. Check the column mapping and data.")

            except Exception as e:
                st.error(f"Load failed: {str(e)}")
    

    st.markdown("---")
    st.subheader("Upload History")

    history = get_upload_history()
    if history.empty:
        st.info("No uploads yet. Upload a file above to get started.")
    else:
        # Summary
        hc1, hc2, hc3, hc4 = st.columns(4)
        hc1.metric("Total Uploads", len(history))
        hc2.metric("Total Rows Loaded", f"{history['rows_loaded'].sum():,}")
        hc3.metric("Tables Used", history["target_table"].nunique())
        hc4.metric("Anomalies Found", history["anomalies_found"].sum())

        # Table
        display_hist = history.copy()
        display_hist["created_at"] = pd.to_datetime(display_hist["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(display_hist, use_container_width=True, hide_index=True)

    st.markdown("---")
    with st.expander("How Upload Works"):
        st.markdown("""
        1. **Upload** — You drop an Excel file
        2. **AI Analysis** — LLM reads column names + sample data
        3. **Column Mapping** — AI maps your columns to database fields
        4. **Review** — You see the mapping, warnings, and can override settings
        5. **Confirm** — Data is loaded using upsert (no duplicates)
        6. **Auto-Refresh** — All dashboard caches are cleared, charts update automatically

        **Safety Features:**
        - Upsert pattern (ON DUPLICATE KEY UPDATE) — never creates duplicates
        - Supplier names auto-normalized (trailing "INC" stripped)
        - COALESCE on monthly_costs — NULL values don't overwrite existing data
        - part_weights uses TRUNCATE + reload (static reference data)
        """)