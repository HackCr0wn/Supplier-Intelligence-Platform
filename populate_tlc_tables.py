
import os
import json
import re
import time
import pandas as pd
from sqlalchemy import text
from loguru import logger
from dotenv import load_dotenv
from utils.db import get_engine

load_dotenv()
engine = get_engine()


##Groq Client
def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
    
    from utils.llm import call_llm
    return call_llm(system_prompt, user_prompt, temperature=temperature)

    # from groq import Groq
    # api_key = os.getenv("GROQ_API_KEY")
    # if not api_key:
    #     raise ValueError("GROQ_API_KEY not found in .env")

    # client = Groq(api_key=api_key)
    # response = client.chat.completions.create(
    #     model="qwen/qwen3-32b",
    #     messages=[
    #         {"role": "system", "content": system_prompt},
    #         {"role": "user", "content": user_prompt}
    #     ],
    #     temperature=temperature,
    #     max_tokens=4096,
    # )
    # return response.choices[0].message.content


def parse_json(raw: str) -> list:
    """Parse JSON from LLM response — handles thinking tags, code fences"""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"```json\s*", "", cleaned)
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()

    start = cleaned.find("[")
    if start == -1:
        # Try single object
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            return [json.loads(cleaned[start:end])]
        raise ValueError(f"No JSON found in response: {cleaned[:300]}")

    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(cleaned, start)
    return result if isinstance(result, list) else [result]

# AGENT 1: TARIFF RATES
TARIFF_SYSTEM = """You are a trade compliance expert specializing in US import tariffs.
You have deep knowledge of:
- US Harmonized Tariff Schedule (HTS)
- MFN (Most Favored Nation) applied rates
- Section 301 tariffs on China
- Free trade agreements: USMCA (US-Mexico-Canada), KORUS (US-Korea), US-Australia FTA, US-Israel FTA
- EU/UK general tariff rates for manufactured goods
- Anti-dumping and countervailing duties on steel/aluminum

For each material + origin country pair, provide:
1. The most likely HS chapter (2-digit) and heading (4-digit) for that material
2. The US import tariff rate (MFN applied + any special tariffs like Section 301)
3. Whether an FTA applies

Return ONLY valid JSON array. No explanation."""

def get_tariff_data() -> pd.DataFrame:
    """Get unique material-country combinations that need tariff rates"""
    df = pd.read_sql(text("""
        SELECT DISTINCT
            sp.coo_code,
            sp.coo_name,
            pm.material_from_drawing,
            mca.base_material_name,
            mca.primary_commodity
        FROM supplier_parts sp
        JOIN parts_master pm ON sp.part_number = pm.part_number
        LEFT JOIN material_classification_ai mca ON pm.material_from_drawing = mca.material_from_drawing
        WHERE sp.coo_code IS NOT NULL AND TRIM(sp.coo_code) != ''
          AND pm.material_from_drawing IS NOT NULL
        ORDER BY sp.coo_code, pm.material_from_drawing
    """), engine)
    return df


def populate_tariff_rates():
    """Use Groq agent to get HS-code specific tariff rates"""
    logger.info("=== Agent 1: Tariff Rates (Groq) ===")

    tariff_data = get_tariff_data()
    if tariff_data.empty:
        logger.warning("No material-country combinations found")
        return 0

    # Group by country to batch requests
    countries = tariff_data.groupby(["coo_code", "coo_name"]).agg(
        materials=("material_from_drawing", lambda x: list(x.unique())),
        base_materials=("base_material_name", lambda x: list(x.dropna().unique())),
        commodities=("primary_commodity", lambda x: list(x.dropna().unique()))
    ).reset_index()

    logger.info(f"Processing {len(countries)} countries with {len(tariff_data)} material-country pairs")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO tariff_rates
            (hs_code, origin_country, destination_country, rate_pct, effective_date, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rate_pct = VALUES(rate_pct),
            notes = VALUES(notes)
    """

    total_loaded = 0

    for _, row in countries.iterrows():
        coo_code = row["coo_code"].strip().upper()
        coo_name = row["coo_name"] or coo_code
        materials = row["materials"][:15]  # Limit per request
        base_materials = row["base_materials"][:15]

        prompt = f"""For importing these materials from {coo_name} ({coo_code}) into the United States:

Materials: {json.dumps(materials)}
Base material types: {json.dumps(base_materials)}

For EACH material, provide the US import tariff rate.

Return JSON array:
[
  {{
    "material": "LC STEEL",
    "hs_chapter": "72",
    "hs_heading": "7208",
    "hs_description": "Hot-rolled flat products of iron/steel",
    "mfn_rate_pct": 0.0,
    "section_301_pct": 0.0,
    "anti_dumping_pct": 0.0,
    "total_rate_pct": 0.0,
    "fta_applies": false,
    "fta_name": "",
    "notes": "USMCA zero rate"
  }}
]

Include Section 301 tariffs for China (25% on steel, 25% on aluminum, etc.).
Include anti-dumping duties where commonly applied (Chinese steel, etc.).
If an FTA applies (USMCA, KORUS, etc.), set total_rate to the preferential rate.
Be specific — different materials get different HS codes and rates."""

        try:
            logger.info(f"  Querying tariffs for {coo_code} ({coo_name}) — {len(materials)} materials...")
            raw = call_llm(TARIFF_SYSTEM, prompt)
            results = parse_json(raw)

            for r in results:
                material = r.get("material", "ALL")
                hs = r.get("hs_heading", r.get("hs_chapter", "GENERAL"))
                total_rate = float(r.get("total_rate_pct", 0))
                fta = r.get("fta_name", "")
                notes_parts = []
                if r.get("hs_description"):
                    notes_parts.append(f"HS: {r['hs_description']}")
                if r.get("mfn_rate_pct"):
                    notes_parts.append(f"MFN: {r['mfn_rate_pct']}%")
                if r.get("section_301_pct") and float(r["section_301_pct"]) > 0:
                    notes_parts.append(f"Section 301: {r['section_301_pct']}%")
                if r.get("anti_dumping_pct") and float(r["anti_dumping_pct"]) > 0:
                    notes_parts.append(f"AD: {r['anti_dumping_pct']}%")
                if fta:
                    notes_parts.append(f"FTA: {fta}")
                notes_parts.append("Source: Groq Agent (qwen3-32b)")

                cursor.execute(sql, (
                    str(hs), coo_code, "US", total_rate, "2024-01-01",
                    f"{material} — " + " | ".join(notes_parts)
                ))
                total_loaded += 1

            logger.info(f"    → Loaded {len(results)} tariff rates for {coo_code}")

        except Exception as e:
            logger.error(f"    → Failed for {coo_code}: {e}")

        # Rate limiting — Groq free tier: 60 RPM
        time.sleep(1.5)

    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Tariff Agent complete: {total_loaded} rates loaded")
    return total_loaded


##AGENT 2: LOGISTICS RATES
LOGISTICS_SYSTEM = """You are a logistics and freight cost expert specializing in:
- Ocean container shipping (FCL rates on major trade lanes)
- Air freight for automotive/industrial parts
- Trucking rates (US domestic, cross-border USMCA)
- Inland haulage and port handling
- Current market rates (2024-2025 levels)

You know Freightos Baltic Index (FBX) rates, Drewry Container Index rates,
and general market pricing for major shipping routes.

For each origin → US route, provide estimated per-part shipping cost.
Assume automotive/industrial parts averaging 2-10 lbs per part, ~3,000-5,000 parts per 40ft container.

Return ONLY valid JSON array. No explanation."""


def get_logistics_data() -> pd.DataFrame:
    """Get unique supplier-origin combinations"""
    df = pd.read_sql(text("""
        SELECT DISTINCT
            sp.supplier_name,
            sp.coo_code,
            sp.coo_name,
            AVG(pw.weight_from_invoice) AS avg_weight
        FROM supplier_parts sp
        LEFT JOIN part_weights pw ON sp.part_number = pw.part_number
        WHERE sp.coo_code IS NOT NULL AND TRIM(sp.coo_code) != ''
          AND sp.supplier_name IS NOT NULL
        GROUP BY sp.supplier_name, sp.coo_code, sp.coo_name
    """), engine)
    return df


def populate_logistics_rates():
    """Use Groq agent to get route-specific logistics rates"""
    logger.info("=== Agent 2: Logistics Rates (Groq) ===")

    logistics_data = get_logistics_data()
    if logistics_data.empty:
        logger.warning("No supplier-origin combinations found")
        return 0

    # Group by origin country to batch
    origins = logistics_data.groupby(["coo_code", "coo_name"]).agg(
        supplier_count=("supplier_name", "nunique"),
        avg_weight=("avg_weight", "mean")
    ).reset_index()

    logger.info(f"Processing {len(origins)} origin countries")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO logistics_rates
            (supplier, origin, destination, mode, rate_per_unit, currency, effective_date, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rate_per_unit = VALUES(rate_per_unit),
            notes = VALUES(notes)
    """

    total_loaded = 0

    for _, row in origins.iterrows():
        coo_code = row["coo_code"].strip().upper()
        coo_name = row["coo_name"] or coo_code
        avg_wt = row["avg_weight"] if pd.notna(row["avg_weight"]) else 3.0

        prompt = f"""Estimate the per-part logistics cost for shipping automotive/industrial parts
from {coo_name} ({coo_code}) to the United States (Midwest, e.g. Detroit area).

Assumptions:
- Average part weight: {avg_wt:.1f} lbs
- Parts per 40ft container: estimate based on part size/weight
- Include: ocean/air freight + port handling + customs clearance + inland trucking to Midwest

Provide costs for the most likely shipping mode.

Return JSON array:
[
  {{
    "origin_country": "{coo_code}",
    "origin_name": "{coo_name}",
    "destination": "US Midwest",
    "primary_mode": "ocean+truck",
    "container_rate_usd": 3500,
    "parts_per_container": 4000,
    "port_handling_per_part": 0.15,
    "customs_clearance_per_part": 0.10,
    "inland_trucking_per_part": 0.20,
    "total_per_part": 1.33,
    "rate_basis": "FBX China-US West Coast Q1 2025 + inland estimate",
    "notes": "Via LA/LB port, truck to Detroit"
  }}
]

Be specific about the routing (which ports, which mode).
For domestic US, use trucking only.
For USMCA (Canada/Mexico), use cross-border truck."""

        try:
            logger.info(f"  Querying logistics for {coo_code} ({coo_name})...")
            raw = call_llm(LOGISTICS_SYSTEM, prompt)
            results = parse_json(raw)

            # Get suppliers for this origin
            sups = logistics_data[logistics_data["coo_code"].str.strip().str.upper() == coo_code]

            for r in results:
                total_per_part = float(r.get("total_per_part", 0))
                mode = r.get("primary_mode", "ocean+truck")
                rate_basis = r.get("rate_basis", "")
                notes = r.get("notes", "")
                full_notes = f"{rate_basis} | {notes} | Source: Groq Agent (qwen3-32b)"

                # Apply to each supplier from this origin
                for _, sup_row in sups.iterrows():
                    supplier_clean = sup_row["supplier_name"].strip()
                    if supplier_clean.endswith(" INC"):
                        supplier_clean = supplier_clean[:-4].strip()

                    cursor.execute(sql, (
                        supplier_clean,
                        coo_name or coo_code,
                        "US",
                        mode,
                        total_per_part,
                        "USD",
                        "2024-01-01",
                        full_notes
                    ))
                    total_loaded += 1

            logger.info(f"    → Loaded rates for {len(sups)} suppliers from {coo_code}")

        except Exception as e:
            logger.error(f"    → Failed for {coo_code}: {e}")

        time.sleep(1.5)

    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Logistics Agent complete: {total_loaded} rates loaded")
    return total_loaded



##AGENT 3: SCRAP RECOVERY (unchanged — real data, no LLM needed)
def populate_scrap_recovery():
    """Derive per-material scrap recovery from surcharge_monthly (real data)"""
    logger.info("=== Scrap Recovery (from surcharge data — no agent needed) ===")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                pm.material_from_drawing,
                ROUND(AVG(
                    CASE WHEN sm.surcharge_weight_lbs > 0 AND sm.scrap_weight_lbs > 0
                         THEN (sm.scrap_weight_lbs / sm.surcharge_weight_lbs) * 100
                         ELSE NULL END
                ), 2) AS recovery_pct,
                ROUND(AVG(
                    CASE WHEN sm.scrap_index_cost != 0
                         THEN ABS(sm.scrap_index_cost)
                         ELSE NULL END
                ), 4) AS scrap_value_per_lb
            FROM surcharge_monthly sm
            JOIN parts_master pm ON sm.part_number = pm.part_number
            WHERE pm.material_from_drawing IS NOT NULL
              AND sm.surcharge_weight_lbs > 0
            GROUP BY pm.material_from_drawing
            HAVING recovery_pct IS NOT NULL AND recovery_pct > 0
        """)).fetchall()

    if not rows:
        logger.warning("No scrap data found")
        return 0

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO scrap_recovery_rates
            (material_from_drawing, recovery_pct, scrap_value_per_lb, effective_date, notes)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            recovery_pct = VALUES(recovery_pct),
            scrap_value_per_lb = VALUES(scrap_value_per_lb)
    """

    data = []
    for r in rows:
        data.append((
            r[0], float(r[1]), float(r[2]), "2024-01-01",
            "Derived from surcharge_monthly: avg(scrap_weight/surcharge_weight)"
        ))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Loaded {len(data)} scrap recovery rates")
    return len(data)


##MAIN
if __name__ == "__main__":
    logger.info("===LLM-Powered TLC Agents")

    # Truncate old data first
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE tariff_rates"))
        conn.execute(text("TRUNCATE TABLE logistics_rates"))
        conn.execute(text("TRUNCATE TABLE scrap_recovery_rates"))
    logger.info("Truncated old TLC tables")

    tariff_count = populate_tariff_rates()
    logistics_count = populate_logistics_rates()
    scrap_count = populate_scrap_recovery()

    print(f"\n{'=' * 70}")
    print(f" TLC AGENT POPULATION COMPLETE")
    print(f"{'=' * 70}")
    print(f"Tariff rates:          {tariff_count} (Groq Agent — HS-code specific)")
    print(f"Logistics rates:        {logistics_count} (Groq Agent — route-specific)")
    print(f"Scrap recovery rates:   {scrap_count} (derived from surcharge data)")
    print(f"{'=' * 70}")
    print(f"\n  Tariff: HS-code level rates with Section 301, AD duties, FTA adjustments")
    print(f"  Logistics: Per-part cost including ocean, port, customs, inland trucking")
    print(f"  Scrap: Real data from your surcharge files")
    print(f"{'=' * 70}\n")