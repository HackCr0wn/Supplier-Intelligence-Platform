from sqlalchemy import text
from utils.db import get_engine

engine = get_engine()

def create_commodity_master():
    """Creates commodity_master table and populates with v1 commodities"""

    create_table_sql = """
    CREATE TABLE IF NOT EXISTS commodity_master (
        commodity_id INT AUTO_INCREMENT PRIMARY KEY,
        commodity_code VARCHAR(50) UNIQUE NOT NULL,
        commodity_name VARCHAR(200) NOT NULL,
        source VARCHAR(100) NOT NULL,
        ticker_symbol VARCHAR(50),
        unit VARCHAR(50),
        category VARCHAR(100),
        created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_code (commodity_code),
        INDEX idx_category (category)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;   
    """

    #v1 commodities - 8 metals
    commodities = [
        ('LME_COPPER', 'LME Copper', 'yfinance', 'HG=F', 'USD/lb', 'Base Metal'),
        ('LME_ALUMINIUM', 'LME Aluminium', 'yfinance', 'ALI=F', 'USD/ton', 'Base Metal'),
        ('LME_ZINC', 'LME Zinc', 'yfinance', 'ZS=F', 'USD/ton', 'Base Metal'),
        ('LME_NICKEL', 'LME Nickel', 'yfinance', 'NI=F', 'USD/ton', 'Base Metal'),
        ('MWUS_HR_COIL', 'Midwest US HR Coil', 'FRED', 'PCU33122033122021', 'Index', 'Steel'),
        ('IRON_ORE', 'Iron Ore CFR China', 'FRED', 'PIORECRUSDM', 'USD/ton', 'Steel Raw Material'),
        ('SCRAP_STEEL', 'Steel Scrap HMS 1', 'FRED', 'WPU10170301', 'Index', 'Steel Raw Material'),
        ('NATURAL_GAS', 'Natural Gas Henry Hub', 'yfinance', 'NG=F', 'USD/MMBtu', 'Energy')
    ]

    insert_sql = """
    INSERT INTO commodity_master
        (commodity_code, commodity_name, source, ticker_symbol, unit, category)
    VALUES
        (:code, :name, :source, :ticker, :unit, :category)
    ON DUPLICATE KEY UPDATE
        commodity_name = VALUES(commodity_name),
        source = VALUES(source),
        ticker_symbol = VALUES(ticker_symbol),
        unit = VALUES(unit),
        category = VALUES(category);
    """

    with engine.begin() as conn:
        #Create Table
        conn.execute(text(create_table_sql))
        print("commodity_master table created")

        #Insert Commodities
        for comm in commodities:
            conn.execute(text(insert_sql), {
                'code': comm[0],
                'name': comm[1],
                'source': comm[2],
                'ticker': comm[3],
                'unit': comm[4],
                'category': comm[5]
            })
        print(f"Inserted {len(commodities)} commodities")

if __name__ == "__main__":
    create_commodity_master()
    print("\n Commoditiy master table complete")