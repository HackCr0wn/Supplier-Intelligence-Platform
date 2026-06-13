import pandas as pd
from sqlalchemy import text
from utils.db import get_engine
from utils.logger import logger
from datetime import datetime

#FETCH OPEN ALERTS FROM DB
def fetch_open_alerts(engine) -> pd.DataFrame:
    query = text("""
        SELECT
            mat_id,
            supplier_id,
            alert_type,
            alert_month,
            previous_value,
            current_value,
            change_pct,
            message,
            status,
            created_at
        FROM price_alerts
        WHERE status = 'OPEN'
        ORDER BY alert_month DESC, change_pct DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    logger.info(f"feteched {len(df)} open alerts")
    return df

#GENERATE EXCEL ALERTS REPORT
def generate_alert_report(df: pd.DataFrame) -> str:
    output_path = "data/processed/price_alerts_report.xlsx"

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        #all alert - sheet 1
        df.to_excel(writer, sheet_name="All Alerts", index=False)

        #summary by alerts - sheet 2
        summary = df.groupby("alert_type").agg(
            total_alerts = ("alert_type", "count"),
            avg_change_pct = ("change_pct", "mean"),
            max_change_pct = ("change_pct", "max")
        ).reset_index()
        summary.to_excel(writer, sheet_name="Summary", index=False)

        #Top 10 biggest changes
        top10 = df.groupby(10, "change_pct")
        top10.to_excel(writer, sheet_name="Top 10 Changes", index=False)

    logger.info(f"Alerts report saved to {output_path}")
    return output_path

#MARKI ALERTS AS RESOLVED
def mark_alerts_resolved(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE price_alerts
            SET status = 'RESOLVED'
            WHERE status = 'OPEN'
    """))
    logger.info("All open alerts marked as resolved")

def run_alerts():
    engine = get_engine()
    logger.info("="*50)
    logger.info("Alert System Started")
    logger.info("="*50)

    alerts_df = fetch_open_alerts(engine)

    if alerts_df.empty:
        logger.info("No open alerts - skipping report generation")
        return
    
    generate_alert_report(alerts_df)

    mark_alerts_resolved(engine)

    logger.info("Alert System Completed")
    logger.info(f"Total alerts processed: {len(alerts_df)}")

if __name__ == "__main__":
    run_alerts()