import requests
from datetime import datetime, timedelta
from sqlalchemy import text
from utils.db import get_engine
from loguru import logger
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

engine = get_engine()

def create_fx_tables():
    """Creates fx_rates table"""

    create_sql = """
    CREATE TABLE IF NOT EXISTS fx_rates (
        rate_id INT AUTO_INCREMENT PRIMARY KEY,
        rate_date DATE NOT NULL,
        base_currency VARCHAR(3) NOT NULL DEFAULT 'USD',
        target_currency VARCHAR(3) NOT NULL,
        rate DECIMAL(12, 6) NOT NULL,
        source VARCHAR(50) DEFAULT 'Frankfurter',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_rate (rate_date, base_currency, target_currency),
        INDEX idx_date (rate_date),
        INDEX idx_currency (target_currency)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with engine.begin() as conn:
        conn.execute(text(create_sql))
        logger.info("fx_rates table created")

def fetch_fx_rates(date=None):
    """
    Fetch FX rates from Franfurt API

    Args: datetime.date object or None for latest
    Returns: dict with rates or None on failure 
    """

    if date is None:
        url = "https://api.frankfurter.app/latest"
    else:
        if isinstance(date, str):
            date_str = date
        else:
            date_str = date.strftime('%Y-%m-%d')
        url = f"https://api.frankfurter.app/{date_str}"

    #we need USD as base so fetching GBP, EUR, INR, CNY
    params = {
        'from': 'USD',
        'to': 'GBP,EUR,INR,CNY'
    }

    try: 
        response = requests.get(url, params=params, timeout=10, verify=False)
        response.raise_for_status()
        date = response.json()

        return {
            'date': datetime.strptime(date['date'], '%Y-%m-%d').date(),
            'rates': date['rates']
        }
    
    except requests.exceptions.RequestException as e:
        logger.info(f"FX API error: {e}")
        return None
    
def load_fx_rates(fx_data):
    """Load FX rates into database"""

    if fx_data is None:
        logger.warning("No FX data to load")
        return 0
    
    insert_sql = """
    INSERT INTO fx_rates (rate_date, base_currency, target_currency, rate, source)
    VALUES (:date, :base, :target, :rate, :source)
    ON DUPLICATE KEY UPDATE
        rate = VALUES(rate),
        created_at = CURRENT_TIMESTAMP;
    """

    loaded = 0
    with engine.begin() as conn:
        for currency, rate in fx_data['rates'].items():
            conn.execute(text(insert_sql), {
                'date': fx_data['date'],
                'base': 'USD',
                'target': currency,
                'rate': rate,
                'source': 'Frankfurter'
            })
            loaded += 1
        
    logger.info(f"Loaded {loaded} FX rates for {fx_data['date']}")
    return loaded

def backfill_fx_rates(start_date, end_date=None):
    """
    Backfill historical FX rates.

    Args: 
    start_date: datetime.date to start from
    end_date: datetime.date to end at(None = today)
    """

    if end_date is None:
        end_date = datetime.now().date()

    logger.info(f"Backfilling FX rates from {start_date} to {end_date}")

    current_date = start_date
    total_loaded = 0

    while current_date <= end_date:
        fx_data = fetch_fx_rates(current_date)
        if fx_data:
            loaded = load_fx_rates(fx_data)
            total_loaded += loaded

        current_date += timedelta(days=1)

        #rate limiting
        if (current_date.day%10 == 0):
            logger.info(f"Progress: {current_date}, total loaded: {total_loaded}")

    logger.info(f"Backfill complete. Total rates loaded: {total_loaded}")

if __name__ == "__main__":
    #setup
    create_fx_tables()

    #test with today's rates
    print("Fetch today's rates...")
    fx_data = fetch_fx_rates()
    if fx_data:
        print(f"Date: {fx_data['date']}")
        print(f"Rates: {fx_data['rates']}")
        load_fx_rates(fx_data)
        print("FX ingestion complete")