from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from fx_ingestion import fetch_fx_rates, load_fx_rates
from commodity_ingestion import ingest_all_commodities
from loguru import logger

scheduler = BlockingScheduler()

def refresh_fx():
    """Daily FX rate refresh"""

    logger.info("Starting daily FX refresh...")
    fx_data = fetch_fx_rates()
    if fx_data:
        loaded = load_fx_rates(fx_data)
        logger.info(f"FX refresh complete. {loaded} rates updated.")
    else:
        logger.error("FX refresh failed- API returned no data.")

def refresh_commodities():
    """Daily commodity prices refresh."""
    logger.info("starting daily commodities refresh...")
    today = datetime.now().date()
    loaded = ingest_all_commodities(start_date=today, end_date=today)
    logger.info(f"Commodity refresh complete. {loaded} prices updated.")

def run_daily_refresh():
    """Run both refreshes together"""
    logger.info("="*50)
    logger.info(f"DAILY REFRESH STARTED: {datetime.now()}")
    refresh_fx()
    refresh_commodities()
    logger.info(f"DAILY REFRESH COMPLETE: {datetime.now()}")
    logger.info("="*50)

#schedule daily at 7AM
scheduler.add_job(
    run_daily_refresh,
    trigger=CronTrigger(hour=7, minute=0),
    id='daily refresh',
    name='Daily FX + Commodity Refresh',
    misfire_grace_time=3600 #allow 1hr late if machine is down
)

if __name__ == "__main__":
    logger.info("Scheduler Started. Daily refresh runs at 7:00AM")
    logger.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")