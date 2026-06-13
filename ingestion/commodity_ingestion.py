import yfinance as yf
import requests
from datetime import datetime, timedelta, date
from sqlalchemy import text
from utils.db import get_engine
from loguru import logger
import os
from dotenv import load_dotenv

engine = get_engine()
load_dotenv()

def create_commodity_price_table():
    """Create commodity_price table"""

    create_sql = """
    CREATE TABLE IF NOT EXISTS commodity_prices (
        price_id INT AUTO_INCREMENT PRIMARY KEY,
        commodity_id INT NOT NULL,
        price_date DATE NOT NULL,
        price DECIMAL(15, 4) NOT NULL,
        source VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_price (commodity_id, price_date),
        FOREIGN KEY (commodity_id) REFERENCES commodity_master(commodity_id),
        INDEX idx_date (price_date),
        INDEX idx_commodity (commodity_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with engine.begin() as conn:
        conn.execute(text(create_sql))
        logger.info("commodity_prices table created")

def get_commodity_id(commodity_code):
    """Get commodity_id from code."""
    sql = "SELECT commodity_id FROM commodity_master WHERE commodity_code = :code"
    with engine.connect() as conn:
        result = conn.execute(text(sql), {'code': commodity_code})
        row = result.fetchone()
        return row[0] if row else None
    
#def fetch_yfinance_data(ticker, start_date, end_date=None):
    """Ferch commodity data from yahoo finance 
    args:
        ticker: yahoo finance ticker symbol,
        start_date: datetime.date,
        end_date: datetime.date or None
    Returns:
        list of (date, price) tuples
    """

 #   if end_date is None:
  #      end_date = datetime.now().date()

#    try: 
 #       data = yf.download(
  #          ticker,
   #         start=start_date,
    #        end=end_date,
     #       progress=False
      #  )
#
 #       if data.empty:
  #          logger.warning(f"No data returned for {ticker}")
   #         return []
        
        #Extract close prices        
    #    prices = []
     #   for date, row in data.iterrows():
      #      if 'Close' in data.columns:
       #         price = row['Close']
        #    else:
         #       price = row['Adj Close']

#            prices.append((date.date(), float(price)))
        
 #       return prices
    
  #  except Exception as e:
   #     logger.error(f"yfinance error for {ticker}: {e}")

def fetch_fred_data(series_id, start_date, end_date=None):
    """Fetch data from FRED API
    
    Args:
        series_id : FRED series ID
        start_date: datetime.date
        end_date: datetime.date ot None

    Returns:
        list of (date, price) tuples
    """

    api_key = os.getenv('FRED_API_KEY')
    if not api_key:
        logger.error("FRED_API_KEY not found in .env")
        return []
    
    if end_date is None:
        end_date = datetime.now().date()

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        'series_id': series_id,
        'api_key': api_key,
        'file_type': 'json',
        'observation_start': start_date.strftime('%Y-%m-%d'),
        'observation_end': end_date.strftime('%Y-%m-%d')
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        prices = []
        for obs in data.get('observations', []):
            if obs['value'] != '.': #FRED uses '.' for missing
                date = datetime.strptime(obs['date'], '%Y-%m-%d').date()
                price = float(obs['value'])
                prices.append((date, price))
            
        return prices
    
    except Exception as e:
        logger.error(f"FRED API error {series_id}: {e}")
        return []
    
def load_commodity_prices(commodity_code, prices, source):
    """Load commodity prices into database"""

    commodity_id = get_commodity_id(commodity_code)
    if not commodity_id:
        logger.error(f"Commodity not found: {commodity_code}")
        return 0
    
    insert_sql = """
    INSERT INTO commodity_prices (commodity_id, price_date, price, source)
    VALUES (:commodity_id, :date, :price, :source)
    ON DUPLICATE KEY UPDATE
        price = values(price),
        created_at = CURRENT_TIMESTAMP;
    """

    loaded = 0
    with engine.begin() as conn:
        for date, price in prices:
            conn.execute(text(insert_sql), {
                'commodity_id': commodity_id,
                'date': date,
                'price': price,
                'source': source
            })
            loaded += 1

    logger.info(f"Loaded {loaded} price for {commodity_code}")
    return loaded

def ingest_all_commodities(start_date, end_date=None):
    """Ingest all commodities from their respective sourceses."""

    #yfinance commdities
    yf_commodities = [
        ('LME_COPPER', 'HG=F'),
        ('NATURAL_GAS', 'NG=F')
    ]

    #FRED commodities
    
    fred_commodities = [
        ('LME_COPPER',    'PCOPPUSDM'),
        ('LME_ALUMINIUM', 'PALUMUSDM'),
        ('LME_ZINC',      'PZINCUSDM'),
        ('LME_NICKEL',    'PNICKUSDM'),
        ('MWUS_HR_COIL',  'WPU101704'),
        ('IRON_ORE',      'PIORECRUSDM'),
        ('SCRAP_STEEL',   'WPU10170301'),
        ('NATURAL_GAS',   'PNGASUSUSDM'),
    ]


    total_loaded = 0

    #Fetch yfinance data
    #for code, ticker in yf_commodities:
     #   logger.info(f"Fetching {code} from yfinance...")
      #  prices = fetch_yfinance_data(ticker, start_date, end_date)
       # loaded = load_commodity_prices(code, prices, 'yfinance')
        #total_loaded += loaded

    #Fetch FRED data    
    for code, series_id in fred_commodities:
        logger.info(f"Fetching {code} from FRED...")
        prices = fetch_fred_data(series_id, start_date, end_date)
        loaded = load_commodity_prices(code, prices, 'FRED')
        total_loaded += loaded

    logger.info(f"Total prices loaded: {total_loaded}")
    return total_loaded

if __name__ == "__main__":
    #setup
    create_commodity_price_table()

    #Test with last 30 dats
    print("\nFetching last 365 days of commodity data...")
    end_date = date.today()
    start_date = end_date - timedelta(days=365)
    ingest_all_commodities(start_date, end_date)

    print("Commodity ingested test complete")