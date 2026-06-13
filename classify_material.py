
import json
import re
import sys
import pandas as pd
import ollama
from sqlalchemy import text
from pathlib import Path
from loguru import logger
from utils.db import get_engine

output_dir = Path("data/processed")
output_file = output_dir/"material_classification.xlsx"

#valid_commodities = [
    
#"LME_COPPER", "LME_ALUMINIUM", "LME_ZINC", "LME_NICKEL",
#    "MWUS_HR_COIL", "IRON_ORE", "SCRAP_STEEL", "NATURAL_GAS", "NONE"
#]

#Exact-match classification rules
#(base_material_name, variant_attribute, variant_value, primary, secondary, confidence)
rules = {
    #steels
    
    "LC STEEL": (
        "Low Carbon Steel", "carbon_content", "low",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.95),
    "MC STEEL": (
        "Medium Carbon Steel", "carbon_content", "medium",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.95),
    "HC STEEL": (
        "High Carbon Steel", "carbon_content", "high",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.95),
    "MICRO ALLOY STEEL": (
        "Micro Alloy Steel", "alloy_type", "micro_alloy",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.95),
    "SPRING STEEL": (
        "Spring Steel", "alloy_type", "spring",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.95),
    "STRUCTURAL STEEL": (
        "Structural Steel", "alloy_type", "structural",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.90),
    "FORGED STEEL": (
        "Forged Steel", "process", "forged",
        "MWUS_HR_COIL", "SCRAP_STEEL", 0.90),
    #Alloy steels (chromium/manganese-based)
    
    "CHROMIUM-MOLYBDENUM ALLOY": (
        "Chromium-Molybdenum Alloy Steel", "alloy_type", "chromium-molybdenum",
        "MWUS_HR_COIL", "NONE", 0.90),
    "CHROMIUM-SILICON ALLOY": (
        "Chromium-Silicon Alloy Steel", "alloy_type", "chromium-silicon",
        "MWUS_HR_COIL", "NONE", 0.90),
    "CHROMIUM-VANADIUM ALLOY": (
        "Chromium-Vanadium Alloy Steel", "alloy_type", "chromium-vanadium",
        "MWUS_HR_COIL", "NONE", 0.90),
    "CHROMIUM-MANGANESE ALLOY": (
        "Chromium-Manganese Alloy Steel", "alloy_type", "chromium-manganese",
        "MWUS_HR_COIL", "NONE", 0.90),
    "FeCrAl ALLOY": (
        "Iron-Chromium-Aluminum Alloy", "alloy_type", "FeCrAl",
        "MWUS_HR_COIL", "NONE", 0.85),
    #Nickel alloys
    
    "NICKEL-MOLYBDENUM ALLOY": (
        "Nickel-Molybdenum Alloy", "alloy_type", "nickel-molybdenum",
        "LME_NICKEL", "MWUS_HR_COIL", 0.90),
    "NICKEL-CHROMIUM-MOLYBDENUM ALLOY": (
        "Nickel-Chromium-Molybdenum Alloy", "alloy_type", "nickel-chromium-molybdenum",
        "LME_NICKEL", "MWUS_HR_COIL", 0.90),
    "NiAl ALLOY": (
        "Nickel-Aluminum Alloy", "alloy_type", "nickel-aluminum",
        "LME_NICKEL", "LME_ALUMINIUM", 0.85),
    #Stainless steel
    
    "AUSTENITIC SS": (
        "Austenitic Stainless Steel", "grade", "austenitic",
        "LME_NICKEL", "MWUS_HR_COIL", 0.95),
    "MARTENSITIC SS": (
        "Martensitic Stainless Steel", "grade", "martensitic",
        "LME_NICKEL", "MWUS_HR_COIL", 0.90),
    #Aluminum (CAST/WROUGHT  Non-Ferrous)
    
    "CAST": (
        "Cast Aluminum", "process", "cast",
        "LME_ALUMINIUM", "NONE", 0.95),
    "WROUGHT": (
        "Wrought Aluminum", "process", "wrought",
        "LME_ALUMINIUM", "NONE", 0.95),
    #Iron
    
    "CAST IRON": (
        "Cast Iron", "process", "cast",
        "IRON_ORE", "SCRAP_STEEL", 0.95),
    "FERRITE": (
        "Ferrite", "none", "none",
        "IRON_ORE", "NONE", 0.80),

     "GRAY CAST IRON": (
        "Gray Cast Iron", "grade", "gray",
        "IRON_ORE", "SCRAP_STEEL", 0.95),

    #Copper
    
    "COPPER ALLOY": (
        "Copper Alloy", "none", "none",
        "LME_COPPER", "NONE", 0.95),
    #Plastics
    
    "PA66": (
        "Nylon 66", "polymer_grade", "66",
        "NATURAL_GAS", "NONE", 0.90),
    "PA6": (
        "Nylon 6", "polymer_grade", "6",
        "NATURAL_GAS", "NONE", 0.90),
    "PA6T/XT": (
        "Nylon 6T/XT", "polymer_grade", "6T/XT",
        "NATURAL_GAS", "NONE", 0.85),
    "PA9T": (
        "Nylon 9T", "polymer_grade", "9T",
        "NATURAL_GAS", "NONE", 0.85),
    "PP": (
        "Polypropylene", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "ABS": (
        "ABS", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PC": (
        "Polycarbonate", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "POM": (
        "Polyoxymethylene (Acetal)", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PVC": (
        "Polyvinyl Chloride", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PBT": (
        "Polybutylene Terephthalate", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PET": (
        "Polyethylene Terephthalate", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PPS": (
        "Polyphenylene Sulfide", "none", "none",
        "NATURAL_GAS", "NONE", 0.85),
    "PTFE": (
        "Polytetrafluoroethylene (Teflon)", "none", "none",
        "NATURAL_GAS", "NONE", 0.85),
    "PMMA": (
        "Polymethyl Methacrylate (Acrylic)", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "PPE-PA": (
        "PPE-PA Blend", "blend", "PPE-PA",
        "NATURAL_GAS", "NONE", 0.85),    
    "PC-ABS": (
        "PC-ABS Blend", "blend", "PC-ABS",
        "NATURAL_GAS", "NONE", 0.85),

    "PC-ASA": (
        "PC-ASA Blend", "blend", "PC-ASA",
        "NATURAL_GAS", "NONE", 0.85),
    "ASA": (
        "Acrylonitrile Styrene Acrylate", "none", "none",
        "NATURAL_GAS", "NONE", 0.90),
    "TPV": (
        "Thermoplastic Vulcanizate", "none", "none",
        "NATURAL_GAS", "NONE", 0.85),
    "TPE": (
        "Thermoplastic Elastomer", "none", "none",
        "NATURAL_GAS", "NONE", 0.85),
    "TPES": (
        "Thermoplastic Elastomer", "none", "none",
        "NATURAL_GAS", "NONE", 0.85),
    "GRP": (
        "Glass Reinforced Plastic", "none", "none",
        "NATURAL_GAS", "NONE", 0.80),
    #Non-commodity
    
    "FOAM TAPE": (
        "Foam Tape", "none", "none",
        "NONE", "NONE", 1.00),
    "TRANSFER TAPE": (
        "Transfer Tape", "none", "none",
        "NONE", "NONE", 1.00),
    "LIQUID": (
        "Liquid (Unspecified)", "none", "none",
        "NONE", "NONE", 1.00),
    "NEODYMIUM": (
        "Neodymium (Rare Earth)", "none", "none",
        "NONE", "NONE", 0.90),
    "NEODUMIUM": (
        "Neodymium (Rare Earth)", "none", "none",
        "NONE", "NONE", 0.90),
    "NOT AVAILABLE": (
        "Not Available", "none", "none",
        "NONE", "NONE", 1.00),
}

#Fetch materials from DB
'''
def fetch_distinct_materials(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT material_from_drawing, classification
            FROM parts_master
            WHERE material_from_drawing IS NOT NULL
              AND TRIM(material_from_drawing) != ''
              AND material_from_drawing != 'NOT AVAILABLE'
            ORDER BY material_from_drawing
        """)).fetchall()

    materials = [{"material": r[0].strip(), "classification": r[1] or ""} for r in rows]
    logger.info(f"Fetched {len(materials)} distinct materials (excl. NOT AVAILABLE)")
    return materials

#LLM Prompt

def build_prompt(materials: list[dict]) -> str:
    material_list = json.dumps(materials, indent=2)

    return f"""You are a procurement materials engineer classifying raw materials for commodity price tracking.

TASK: For each material below, return:
1. base_material_name — standardized common name (e.g. "Low Carbon Steel", "Cast Aluminum", "Nylon 66")
2. variant_attribute — what differentiates variants with the same base (e.g. "carbon_content", "alloy_type", "polymer_grade"). Use "none" if not applicable.
3. variant_value — the specific variant (e.g. "low", "chromium-molybdenum", "66"). Use "none" if not applicable.
4. primary_commodity — main commodity benchmark from the list below
5. secondary_commodity — secondary price driver from the list below, or "NONE"
6. confidence — 0.0 to 1.0

AVAILABLE COMMODITIES (use ONLY these exact codes):
- LME_COPPER — copper and copper alloys
- LME_ALUMINIUM — aluminum and aluminum alloys, cast aluminum, wrought aluminum
- LME_ZINC — zinc and zinc alloys
- LME_NICKEL — nickel alloys, stainless steel (nickel is the SS price driver)
- MWUS_HR_COIL — carbon steel, alloy steel, spring steel, structural steel
- IRON_ORE — iron ore, cast iron
- SCRAP_STEEL — steel scrap
- NATURAL_GAS — all plastics and polymers (feedstock proxy)
- NONE — no matching commodity (adhesives, tapes, foams, rare materials)

RULES:
- "CAST" with classification "NON-FERROUS" = Cast Aluminum → LME_ALUMINIUM
- "WROUGHT" with classification "NON-FERROUS" = Wrought Aluminum → LME_ALUMINIUM
- LC STEEL / MC STEEL / HC STEEL = Low / Medium / High Carbon Steel → MWUS_HR_COIL
- Any "SS" or "Stainless" → primary: LME_NICKEL
- Alloy steels (CHROMIUM-X, NICKEL-X alloys) → primary: MWUS_HR_COIL
- All plastics (PA66, PA6, PP, ABS, POM, PVC, TPV, TPE, etc.) → NATURAL_GAS
- FOAM TAPE, TRANSFER TAPE, LIQUID → NONE

MATERIALS TO CLASSIFY:
{material_list}

Return ONLY a valid JSON array. No explanation, no markdown code fences, no text before or after.
[
  {{"material": "...", "base_material_name": "...", "variant_attribute": "...", "variant_value": "...", "primary_commodity": "...", "secondary_commodity": "...", "confidence": 0.95}},
  ...
]"""
'''
#Fetch distict  materials from DB
def fetch_distinct_materials(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT material_from_drawing
            FROM parts_master
            WHERE material_from_drawing IS NOT NULL
              AND TRIM(material_from_drawing) != ''
            ORDER BY material_from_drawing
        """)).fetchall()

    materials = [r[0].strip() for r in rows]
    logger.info(f"Fetched {len(materials)} distinct materials")
    return materials
'''
#Calling Qwen3 via ollama

def call_qwen3(prompt: str) -> str:
    logger.info("Calling qwen3:latest via Ollama — expect 1-2 minutes...")
    response = ollama.chat(
        model="qwen2.5:3b",
        messages=[{"role": "user", "content": prompt}]
    )
    return response["message"]["content"]

#Parse response


def parse_response(raw: str) -> list[dict]:
    # Strip <think>...</think> if present
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Strip markdown code fences if present
    cleaned = re.sub(r"```json\\s*", "", cleaned)
    cleaned = re.sub(r"```\\s*", "", cleaned)
    cleaned = cleaned.strip()

    # Find the first JSON array using raw_decode
    start = cleaned.find("[")
    if start == -1:
        logger.error(f"No JSON array found. Raw response:\n{raw[:1000]}")
        raise ValueError("qwen3 did not return a valid JSON array")

    decoder = json.JSONDecoder()
    try:
        result, end_idx = decoder.raw_decode(cleaned, start)
        if isinstance(result, list):
            return result
        raise ValueError("Parsed JSON is not a list")
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed at position {e.pos}. Snippet around error:\n{cleaned[max(0,e.pos-200):e.pos+200]}")
        raise


#Validate

def validate_results(results: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(results)

    required = ["material", "base_material_name", "primary_commodity",
                 "secondary_commodity", "confidence"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in qwen3 response: {missing}")

    # Flag invalid commodity codes
    for col in ["primary_commodity", "secondary_commodity"]:
        invalid = df[~df[col].isin(valid_commodities)][col].unique()
        if len(invalid) > 0:
            logger.warning(f"⚠ Invalid {col} values: {list(invalid)}")

    # Add NOT AVAILABLE (hardcoded, not sent to qwen3)
    not_avail = {
        "material": "NOT AVAILABLE",
        "base_material_name": "Not Available",
        "variant_attribute": "none",
        "variant_value": "none",
        "primary_commodity": "NONE",
        "secondary_commodity": "NONE",
        "confidence": 1.0
    }
    df = pd.concat([df, pd.DataFrame([not_avail])], ignore_index=True)

    # Fill missing optional columns
    for col in ["variant_attribute", "variant_value"]:
        if col not in df.columns:
            df[col] = "none"

    return df


def classify():
    logger.info("=== Phase E: Material Classification ===")

    engine = get_engine()
    materials = fetch_distinct_materials(engine)

    prompt = build_prompt(materials)
    raw_response = call_qwen3(prompt)

    results = parse_response(raw_response)
    logger.info(f"Parsed {len(results)} classifications from qwen3")

    df = validate_results(results)
    df.to_excel(output_file, index=False)
    logger.info(f"Saved to {output_file}")

    # Print all results for visual review
    print("\n" + "=" * 80)
    print(" CLASSIFICATION RESULTS — REVIEW ALL 52 BEFORE LOADING")
    print("=" * 80)

    for _, row in df.iterrows():
        conf = row['confidence']
        flag = " ⚠ LOW" if conf < 0.8 else ""
        print(f"\n  {row['material']}")
        print(f"    base:      {row['base_material_name']}")
        print(f"    commodity: {row['primary_commodity']} / {row['secondary_commodity']}")
        print(f"    variant:   {row.get('variant_attribute', 'none')} = {row.get('variant_value', 'none')}")
        print(f"    confidence:{conf}{flag}")

    print(f"\n{'=' * 80}")
    print(f"Total: {len(df)} materials classified")
    print(f"Saved: {output_file}")
    print(f"\n→ Review the output above.")
    print(f"→ If correct, run:  python classify_materials.py --load")
    print(f"→ If wrong, fix the Excel file manually, then run --load")

#Push only validate results to DB

def load_to_db():
    logger.info("=== Loading material_classification_ai to DB ===")

    if not output_file.exists():
        logger.error(f"{output_file} not found — run classify first (without --load)")
        return

    engine = get_engine()
    df = pd.read_excel(output_file)
    logger.info(f"Read {len(df)} rows from {output_file}")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO material_classification_ai
            (material_from_drawing, base_material_name, variant_attribute,
             variant_value, primary_commodity, secondary_commodity, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            base_material_name  = VALUES(base_material_name),
            variant_attribute   = VALUES(variant_attribute),
            variant_value       = VALUES(variant_value),
            primary_commodity   = VALUES(primary_commodity),
            secondary_commodity = VALUES(secondary_commodity),
            confidence          = VALUES(confidence),
            classified_at       = CURRENT_TIMESTAMP
    """

    cols = ["material", "base_material_name", "variant_attribute",
            "variant_value", "primary_commodity", "secondary_commodity", "confidence"]
    data = []
    for _, row in df[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Loaded {len(data)} rows to material_classification_ai")

if __name__ == "__main__":
    if "--load" in sys.argv:
        load_to_db()
    else:
        classify()
'''
#Classify uing rules
def classify_materials(materials: list[str]) -> pd.DataFrame:
    results = []
    unmatched = []

    for mat in materials:
        lookup = mat.strip()

        if lookup in rules:
            base, var_attr, var_val, prim, sec, conf = rules[lookup]
        else:
            # No match — flag for review
            base = f"UNCLASSIFIED: {lookup}"
            var_attr = "none"
            var_val = "none"
            prim = "NONE"
            sec = "NONE"
            conf = 0.0
            unmatched.append(lookup)

        results.append({
            "material": lookup,
            "base_material_name": base,
            "variant_attribute": var_attr,
            "variant_value": var_val,
            "primary_commodity": prim,
            "secondary_commodity": sec,
            "confidence": conf,
        })

    if unmatched:
        logger.warning(f"{len(unmatched)} materials NOT matched: {unmatched}")
    else:
        logger.info("All materials matched rules — zero unclassified")

    return pd.DataFrame(results)


def classify():
    logger.info("=== Phase E: Material Classification (Rule-Based) ===")

    engine = get_engine()
    materials = fetch_distinct_materials(engine)

    df = classify_materials(materials)
    df.to_excel(output_file, index=False)
    logger.info(f"Saved to {output_file}")

    # Print all results for visual review
    print("\n" + "=" * 80)
    print(" CLASSIFICATION RESULTS — REVIEW ALL BEFORE LOADING")
    print("=" * 80)

    for _, row in df.iterrows():
        conf = row["confidence"]
        flag = "LOW" if conf < 0.8 else ""
        flag = "UNCLASSIFIED" if conf == 0.0 else flag
        print(f"\n  {row['material']}")
        print(f"    base:      {row['base_material_name']}")
        print(f"    commodity: {row['primary_commodity']} / {row['secondary_commodity']}")
        print(f"    variant:   {row['variant_attribute']} = {row['variant_value']}")
        print(f"    confidence:{conf}{flag}")

    # Summary
    matched = len(df[df["confidence"] > 0])
    unmatched = len(df[df["confidence"] == 0])
    print(f"\n{'=' * 80}")
    print(f"Total: {len(df)} materials | Matched: {matched} | Unclassified: {unmatched}")
    print(f"Saved: {output_file}")
    print(f"\n→ Review the output above.")
    print(f"→ If correct, run:  python classify_material.py --load")
    print(f"→ If wrong, edit {output_file} manually, then run --load")


#  LOAD: Push validated results to DB 
def load_to_db():
    logger.info("=== Loading material_classification_ai to DB ===")

    if not output_file.exists():
        logger.error(f"{output_file} not found — run classify first (without --load)")
        return

    engine = get_engine()
    df = pd.read_excel(output_file)
    logger.info(f"Read {len(df)} rows from {output_file}")

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO material_classification_ai
            (material_from_drawing, base_material_name, variant_attribute,
             variant_value, primary_commodity, secondary_commodity, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            base_material_name  = VALUES(base_material_name),
            variant_attribute   = VALUES(variant_attribute),
            variant_value       = VALUES(variant_value),
            primary_commodity   = VALUES(primary_commodity),
            secondary_commodity = VALUES(secondary_commodity),
            confidence          = VALUES(confidence),
            classified_at       = CURRENT_TIMESTAMP
    """

    cols = ["material", "base_material_name", "variant_attribute",
            "variant_value", "primary_commodity", "secondary_commodity", "confidence"]
    data = []
    for _, row in df[cols].iterrows():
        data.append(tuple(None if pd.isna(v) else v for v in row))

    cursor.executemany(sql, data)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Loaded {len(data)} rows to material_classification_ai")


if __name__ == "__main__":
    if "--load" in sys.argv:
        load_to_db()
    else:
        classify()
