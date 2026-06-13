import pandas as pd
from sqlalchemy import text
from utils.db import get_engine
from utils.logger import logger
from datetime import datetime

#ALERT THRESHOLD
pricr_spike_threshold = 5.0 # changes in invoice price
invoice_spike_threshold = 5.0 # changes in invoice total
surcharge_change_threshold = 5.0 #changes in surcharge

#MOTHLY SNAPSHOTS
def build_price_snapshots(engine):
    logger.info("Building monthly price snapshots...")


    #invoice data: avg price and total per material per supplier per month
    invoice_query = text("""
        SELECT
            `Mat. ID` AS mat_id,
            `Supp. ID` AS supplier_id,
            DATE_FORMAT(`Invoice Dt.`, '%Y-%m-01') AS snapshot_month,
            AVG(`Unit Price $`) AS avg_invoice_price,
            SUM(`Total Amt`) AS total_invoice_value,
            `currency`
        FROM invoice_data
        GROUP BY mat_id, supplier_id, snapshot_month, currency           
    """)

    #cost data: base cost, surchagr, variance per material per supplier per month
    cost_query = text("""
        SELECT
            `Mat. ID` AS mat_id,
            `Supp. ID` AS supplier_id,
            DATE_FORMAT('Year', '%Y-%m-01') AS snapshot_month,
            AVG(`Base Cost`) AS base_cost,
            AVG(`Surcharge Cost`) AS surcharge,
            AVG(`Variance`) AS variance,
            `currency`
        FROM cost_data
        GROUP BY mat_id, supplier_id, snapshot_month, currency
    """)

    with engine.connect() as conn:
        invoice_df = pd.read_sql(invoice_query, conn)
        cost_df = pd.read_sql(cost_query, conn)

    #Merging invoice and cost on mat_id + supplier_id + snapshot_month
    merged = pd.merge(
        invoice_df, cost_df,
        on=["mat_id", "supplier_id", "snapshot_month"],
        how="outer",
        suffixes=("_inv", "_cost")
    )

    #use invoice currency, fall back to cost currency
    merged["currency"] = merged["currency_inv"].fillna(merged["currency_cost"])
    merged = merged.drop(columns=["currency_inv", "currency_cost"])

    #write snapshots to MySQL
    merged["created_at"] = datetime.now()
    merged.to_sql(
        name="price_snapshots",
        con=engine,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=500
    )

    logger.info(f"Price snapshots built: {len(merged)} records")
    return merged

#COMPARE MONTH_WISE
def calculate_monthly_changes(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Calculating Month Wise Changes...")

    df = df.sort_values(["mat_id", "supplier_id", "snapshot_month"])

    #calculating changes per month per material per supplier
    for col in ["avg_invoice_price", "total_invoice_value", "base_cost", "surcharge", "variance"]:
        if col in df.columns:
            df[f"{col}_prev"] = df.groupby(["mat_id", "supplier_id"])[col].shift(1)
            df[f"{col}_chg_pct"] = (
                (df[col] - df[f"{col}_prev"]) / df[f"{col}_prev"] * 100
            ).round(2)
    return df

#DETECT ANOMALIES & GENERATE ALERTS
def detect_anomalies(df: pd.DataFrame, engine) -> list:
    logger.info("Detecting Anomalies...")
    alerts = []

    for _,row in df.iterrows():
        month = row["snapshot_month"]
        mat_id = row["mat_id"]
        supplier = row["supplier_id"]

        #Alert 1: Invoice price spike
        if pd.notna(row.get("avg_invoice_price_chg_pct")):
            chg = row["avg_invoice_price_chg_pct"]
            if abs(chg) >= pricr_spike_threshold:
                alerts.append({
                    "mat_id": mat_id,
                    "supplier_id": supplier,
                    "alert_type": "PRICE SPIKE",
                    "alert_month": month,
                    "previous_value": row["avg_invoice_price_prev"],
                    "current_value": row["avg_invoice_price"],
                    "change_pct": chg,
                    "message": f"Invoice price changed by {chg}% for {mat_id} from {supplier}",
                    "status": "OPEN"
                })

        #Alert 2: Invoice Total Spike
        if pd.notna(row.get("total_invoice_value_change_pct")):
            chg = row["total_invoice_value_chg_pct"]
            if abs(chg) >= invoice_spike_threshold:
                alerts.append({
                    "mat_id": mat_id,
                    "supplier_id": supplier,
                    "alert_type": "INVOICE SPIKE",
                    "alert_month": month,
                    "previous_value": row["avg_invoice_value_prev"],
                    "current_value": row["avg_invoice_value"],
                    "change_pct": chg,
                    "message": f"Invoice total changed by {chg}% for {mat_id} from {supplier}",
                    "status": "OPEN"
                })

        #Alert 3: Surcharge change
        if pd.notna(row.get("surcharge_chg_pct")):
            chg = row["surcharge_chg_pct"]
            if abs(chg) >= surcharge_change_threshold:
                alerts.append({
                    "mat_id": mat_id,
                    "supplier_id": supplier,
                    "alert_type": "SURCHAGE CHANGE",
                    "alert_month": month,
                    "previous_value": row["surcharge_prev"],
                    "current_value": row["surcharge"],
                    "change_pct": chg,
                    "message": f"Surcharge changed by {chg}% for {mat_id} from {supplier}",
                    "status": "OPEN"
                })

    logger.info(f"{len(alerts)} anomalies detected")

    #save alerts to MySQL
    if alerts:
        alerts_df = pd.DataFrame(alerts)
        alerts_df["created_at"] = datetime.now()
        alerts_df.to_sql(
            name="price_alerts",
            con=engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500
        )
    
    return alerts

#SIMPLE MOVING AVERAGE FORECAST
def forecast_price(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Running price forecasts...")

    df = df.sort_values(["mat_id", "supplier_id", "snapshot_month"])

    #3 month moving average forecast
    df["invoice_price_forecast"] = (
        df.groupby(["mat_id", "supplier_id"])["avg_invoice_price"]
        .transform(lambda x: x.rolling(window=3, min_periods=1).mean())
    )

    #Flag upward trends - current price above 3 months average
    df["forecast_trends"] = df.apply(
        lambda r: "UP" if pd.notna(r["invoice_price_forecast"])
                       and pd.notna(r["avg_invoice_price"])
                       and r["avg_invoice_price"] > r["invoice_price_forecast"]
                    else "STABLE", axis=1     
    )

    return df

#YEARLY PERFORMANCE SUMMARY
def yearly_summary(engine):
    logger.info("Generating yearly performance summary...")

    query = text("""
        SELECT
            mat_id,
            supplier_id,
            YEAR(snapshot_month) AS year,
            AVG(avg_invoice_price) AS avg_invoice_price,
            SUM(total_invoice_value) AS total_invoice_value,
            AVG(base_cost) AS avg_base_cost,
            AVG(surcharge) AS avg_surcharge,
            AVG(variance) AS avg_variance,
            currency
            FROM price_snapshots
            GROUP BY mat_id, supplier_id, year, currency
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query,conn)

    #saves yearly summary to excel
    output_path = "data/processed/yearly_performance.xlsx"
    df.to_excel(output_path, index=False)
    logger.info(f"Yearly summary saved to {output_path}")

    return df

#main
def run_price_monitoring():
    engine = get_engine()
    logger.info("="*50)
    logger.info("Price Monitoring Started")
    logger.info("="*50)

    snapshots = build_price_snapshots(engine)
    snapshots = calculate_monthly_changes(snapshots)
    alerts = detect_anomalies(snapshots, engine)
    snapshots = forecast_price(snapshots)
    yearly_summary(engine)

    logger.info("Price Monitoring Completed")
    logger.info(f"Total alerts generated: {len(alerts)}")

if __name__ == "__main__":
    run_price_monitoring()