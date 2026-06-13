from datetime import datetime, timedelta
from ingestion.fx_ingestion import fetch_fx_rates, load_fx_rates
from ingestion.commodity_ingestion import ingest_all_commodities
from loguru import logger
import time

def backfill_fx_rates(years=10):
    """Backfill FX rates for specified years"""

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=years * 365)

    logger.info(f"Backfilling FX rates from {start_date} to {end_date}")

    current_date = start_date
    total_loaded = 0
    days_processed = 0

    while current_date <= end_date:
        fx_data = fetch_fx_rates(current_date)
        if fx_data:
            loaded = load_fx_rates(fx_data)
            total_loaded += loaded
        
        days_processed += 1
        current_date += timedelta(days=1)

        #Progress update every 100 days
        if days_processed % 100 == 0:
            logger.info(f"FX Progress: {current_date}, loaded {total_loaded} rates")

        #Rate limiting - one request per second
        time.sleep(1)

    logger.info(f"FX Backfill complete. Total Rates: {total_loaded}")
    return total_loaded

def backfill_commodities(years=10):
    """Backfill commodity prices for specifies years"""

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=years * 365)

    logger.info(f"Backfilling commodity prices from {start_date} to {end_date}")

    #FRED API can handle large data ranges in one call
    #so we just call once per commoditiy
    total_loaded = ingest_all_commodities(start_date, end_date)

    logger.info(f"Commodity backfill complete. Total prices: {total_loaded}")
    return total_loaded

if __name__ == "__main__":
    print("="*60)
    print("BACKFILL HISTRORICAL DATA - 10 YEARS")
    print("="*60)
    print('\nThis will take approximately 60-90 minutes')
    print("FX: ~3650 days x 4 currencies = ~14,600 rates")
    print("Commodites: ~3,650 days x 8 commodities = ~29,200 prices")
    print("\nPress Ctrl+C to cancel, or wait 5 seconds to continue ")

    time.sleep(5)

    #Backfill commodities first
    print("\n[1/2] Starting commodity backfill...")
    commodity_count = backfill_commodities(years=10)

    print("\n[2/2] Starting FX backfill...")
    fx_count = backfill_fx_rates(years=10)

    print("\n" + "="*60)
    print("BACKFILL COMPLETE")
    print("="*60)
    print(f"Commodity prices loaded: {commodity_count:,}")
    print(f"FX rates loaded: {fx_count:,}")
    print(f"Total data points: {commodity_count + fx_count:,}")