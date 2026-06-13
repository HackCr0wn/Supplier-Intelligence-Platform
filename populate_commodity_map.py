
from sqlalchemy import text
from loguru import logger
from utils.db import get_engine

primary_weight = 0.7000
secondary_weight = 0.3000


def populate():
    logger.info("=== Phase F: Populating material_commodity_map ===")
    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT material_from_drawing, primary_commodity, secondary_commodity
            FROM material_classification_ai
            WHERE primary_commodity != 'NONE'
        """)).fetchall()

    logger.info(f"Fetched {len(rows)} materials with commodity mappings (excl. NONE)")

    records = []
    single = 0
    dual = 0

    for mat, prim, sec in rows:
        if sec == "NONE" or sec is None:
            records.append((mat, prim, 1.0000, "2024-01-01", "9999-12-31"))
            single += 1
        else:
            records.append((mat, prim, primary_weight, "2024-01-01", "9999-12-31"))
            records.append((mat, sec, secondary_weight, "2024-01-01", "9999-12-31"))
            dual += 1

    conn_raw = engine.raw_connection()
    cursor = conn_raw.cursor()

    sql = """
        INSERT INTO material_commodity_map
            (material_from_drawing, commodity_code, weight, effective_from, effective_to)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            weight = VALUES(weight),
            effective_to = VALUES(effective_to)
    """

    cursor.executemany(sql, records)
    conn_raw.commit()
    cursor.close()
    conn_raw.close()

    logger.info(f"Loaded {len(records)} mappings ({single} single-commodity, {dual} dual-commodity)")


if __name__ == "__main__":
    populate()
