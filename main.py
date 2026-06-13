from ingestion.loaders.load_to_db import run_ingestion
from monitoring.price_tracker import run_price_monitoring
from monitoring.alerts import run_alerts
from utils.logger import logger

def main():
    logger.info("=" * 50)
    logger.info("Procurement Pipeline Started")
    logger.info("=" * 50)

    logger.info("Running Ingestion Pipeline...")
    run_ingestion()

    logger.info("Running Price Monitoring...")
    run_price_monitoring()

    logger.info("Running Alert System...")
    run_alerts()

    logger.info("=" * 50)
    logger.info("Pipeline Completed")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()