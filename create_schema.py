from utils.db import get_engine
from sqlalchemy import text
from loguru import logger

engine = get_engine()

TABLES = {}

#1 parts_master
TABLES["parts_master"] = """
CREATE TABLE IF NOT EXISTS parts_master (
    part_number VARCHAR(100) NOT NULL,
    material_from_drawing VARCHAR(500),
    alternate_material VARCHAR(500),
    heat_tratment VARCHAR(200),
    coating VARCHAR(200),
    classification VARCHAR(100),
    sub_class VARCHAR(100),
    family VARCHAR(100),
    form VARCHAR(100),
    grade VARCHAR(200),
    cad_weight_lbs DECIMAL(10, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (part_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

#2 supplier_parts
TABLES["supplier_parts"] = """
CREATE TABLE IF NOT EXISTS supplier_parts (
    id INT NOT NULL AUTO_INCREMENT,
    part_number VARCHAR(100) NOT NULL,
    supplier_id VARCHAR(100),
    supplier_name VARCHAR(200),
    plant VARCHAR(50),
    commodity VARCHAR(200),
    coo_code VARCHAR(10),
    coo_name VARCHAR(200),
    fx_type VARCHAR(10),
    unit_cost DECIMAL(15, 4),
    snapshot_date DATE,
    source_file VARCHAR(200),
    PRIMARY KEY (id),
    UNIQUE KEY uq_supplier_parts (part_number, supplier_id, snapshot_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

#3 part_weights
TABLES["part_weighs"] = """
CREATE TABLE IF NOT EXISTS part_weights (
    id INT NOT NULL AUTO_INCREMENT,
    part_number VARCHAR(100) NOT NULL,
    supplier VARCHAR(200),
    weight_from_invoice DECIMAL(10,4),
    material_from_drawing VARCHAR(500),
    class_pbi VARCHAR(100),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

#4 monthly_costs
TABLES["monthly_costs"] = """
CREATE TABLE IF NOT EXISTS monthly_costs (
    id INT NOT NULL AUTO_INCREMENT,
    part_number VARCHAR(100) NOT NULL,
    supplier_id VARCHAR(100) NOT NULL,
    year INT NOT NULL,
    month INT NOT NULL,
    month_cost DECIMAL(15, 4),
    month_receipts DECIMAL(15, 4),
    month_recd_spend DECIMAL(15, 4),
    mrp_qty DECIMAL(15, 4),
    mrp_spend DECIMAL(15, 4),
    PRIMARY KEY (id),
    UNIQUE KEY uq_monthly_costs (part_number, supplier_id, year, month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

#5 surcharge_monthly
TABLES["surcharge_monthly"] = """
CREATE TABLE IF NOT EXISTS surcharge_monthly (
    id INT NOT NULL AUTO_INCREMENT,
    part_number VARCHAR(100) NOT NULL,
    invoice_part_number VARCHAR(100),
    supplier VARCHAR(200) NOT NULL,
    destination_plant VARCHAR(100),
    year INT NOT NULL,
    month INT NOT NULL,
    material VARCHAR(200),
    raw_material_index VARCHAR(500),
    scrap_index VARCHAR(500),
    surcharge_weight_lbs DECIMAL(10, 4),
    raw_material_base_cost DECIMAL(15, 6),
    index_cost DECIMAL(15, 6),
    part_surcharge DECIMAL(15, 4),
    scrap_weight_lbs DECIMAL(10, 4),
    scrap_index_cost DECIMAL(15, 6),
    scarp_surcharge DECIMAL(15, 4),
    total_surcharge DECIMAL(15, 4),
    quantity INT,
    total_surcharge_cost DECIMAL(15, 4),
    base_material VARCHAR(200),
    source_tier VARCHAR(5) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_surcharge_monthly (part_number, supplier, year, month, source_tier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

def create_all_tables():
    with engine.begin() as conn:
        for table_name, ddl in TABLES.items():
            conn.execute(text(ddl))
            logger.info(f"Table ready: {table_name}")
        logger.info("Schema creation complete - all 5 tables ready.")

if __name__ == "__main__":
    create_all_tables()