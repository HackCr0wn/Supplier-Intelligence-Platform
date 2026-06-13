
import sys
import os
import argparse
import warnings
import numpy as np
import pandas as pd
from sqlalchemy import text
from loguru import logger
from utils.db import get_engine
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Bidirectional, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping
from sklearn.preprocessing import MinMaxScaler


warnings.filterwarnings("ignore")
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

engine = get_engine()

##DATA LOADING
def get_commodity_series(commodity_code: str) -> pd.Series:
    df = pd.read_sql(text("""
        SELECT cp.price_date, cp.price
        FROM commodity_prices cp
        JOIN commodity_master cm ON cp.commodity_id = cm.commodity_id
        WHERE cm.commodity_code = :code
        ORDER BY cp.price_date
    """), engine, params={"code": commodity_code})

    if df.empty:
        return pd.Series(dtype=float)

    df["price_date"] = pd.to_datetime(df["price_date"])
    df = df.set_index("price_date").resample("MS").mean().dropna()
    return df["price"]


##MODEL 1: ETS (Holt-Winters)
def forecast_ets(series: pd.Series, forecast_months: int = 12,
                 holdback: int = 0) -> dict:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    if len(series) < 24:
        return None

    if holdback > 0:
        train = series[:-holdback]
        test = series[-holdback:]
    else:
        train = series
        test = None

    try:
        model = ExponentialSmoothing(
            train, trend="add", seasonal="add",
            seasonal_periods=12, damped_trend=True
        ).fit(optimized=True)
    except Exception:
        try:
            model = ExponentialSmoothing(
                train, trend="add", damped_trend=True
            ).fit(optimized=True)
        except Exception:
            return None

    n = holdback if holdback > 0 else forecast_months
    forecast = model.forecast(n)

    residuals = model.resid.dropna()
    std_r = residuals.std()
    steps = np.arange(1, n + 1)
    ci = 1.96 * std_r * np.sqrt(steps)

    mape = None
    if test is not None and len(test) > 0:
        pred = model.forecast(holdback)
        common = test.index.intersection(pred.index)
        if len(common) > 0:
            actual = test.loc[common]
            predicted = pred.loc[common]
            mape = float((np.abs((actual - predicted) / actual)).mean() * 100)

    return {
        "model": "ETS (Holt-Winters)",
        "forecast": forecast,
        "lower_ci": forecast - ci,
        "upper_ci": forecast + ci,
        "mape": mape,
        "train_size": len(train),
        "history": train,
        "test": test,
    }

##MODEL 2 & 3: LSTM / BiLSTM
def create_sequences(data: np.ndarray, look_back: int = 12):
    X, y = [], []
    for i in range(look_back, len(data)):
        X.append(data[i - look_back:i, 0])
        y.append(data[i, 0])
    return np.array(X), np.array(y)


def forecast_lstm(series: pd.Series, forecast_months: int = 12,  holdback: int = 0, bidirectional: bool = False) -> dict:

    tf.random.set_seed(42)
    model_name = "BiLSTM" if bidirectional else "LSTM"

    if len(series) < 36:
        return None

    if holdback > 0:
        train_series = series[:-holdback]
        test_series = series[-holdback:]
    else:
        train_series = series
        test_series = None

    # Normalize
    scaler = MinMaxScaler(feature_range=(0, 1))
    train_scaled = scaler.fit_transform(train_series.values.reshape(-1, 1))

    # Sequences
    look_back = min(24, len(train_scaled) // 3)
    if look_back < 3:
        return None

    X_train, y_train = create_sequences(train_scaled, look_back)
    if len(X_train) < 10:
        return None

    X_train = X_train.reshape(X_train.shape[0], X_train.shape[1], 1)

    # Build model
    model = Sequential()
    if bidirectional:
        model.add(Bidirectional(LSTM(64, return_sequences=True),
                                input_shape=(look_back, 1)))
        model.add(Dropout(0.2))
        model.add(Bidirectional(LSTM(32)))
    else:
        model.add(LSTM(64, return_sequences=True, input_shape=(look_back, 1)))
        model.add(Dropout(0.2))
        model.add(LSTM(32))

    model.add(Dropout(0.2))
    model.add(Dense(16, activation='relu'))
    model.add(Dense(1))

    model.compile(optimizer='adam', loss='mse', metrics=['mae'])

    early_stop = EarlyStopping(
        monitor='loss', patience=15, restore_best_weights=True, min_delta=1e-6
    )

    model.fit(
        X_train, y_train,
        epochs=200,
        batch_size=min(32, len(X_train) // 2),
        callbacks=[early_stop],
        verbose=0,
        shuffle=False
    )

    # Forecast
    n = holdback if holdback > 0 else forecast_months

    if holdback > 0:
        full_scaled = scaler.transform(series.values.reshape(-1, 1))
        seed = full_scaled[-(holdback + look_back):-holdback].flatten().tolist()
    else:
        seed = train_scaled[-look_back:].flatten().tolist()

    predictions = []
    current_seq = list(seed)

    for _ in range(n):
        x_input = np.array(current_seq[-look_back:]).reshape(1, look_back, 1)
        pred = model.predict(x_input, verbose=0)[0, 0]
        predictions.append(pred)
        current_seq.append(pred)

    predictions_inv = scaler.inverse_transform(
        np.array(predictions).reshape(-1, 1)
    ).flatten()

    # Build forecast index
    if holdback > 0:
        forecast_idx = test_series.index
    else:
        last_date = train_series.index[-1]
        forecast_idx = pd.date_range(
            start=last_date + pd.DateOffset(months=1),
            periods=n, freq='MS'
        )

    forecast = pd.Series(predictions_inv, index=forecast_idx)

    # MC Dropout confidence intervals (30 runs)
    all_preds = []
    for _ in range(30):
        preds_run = []
        curr = list(seed)
        for _ in range(n):
            x_in = np.array(curr[-look_back:]).reshape(1, look_back, 1)
            p = model(x_in, training=True).numpy()[0, 0]
            preds_run.append(p)
            curr.append(p)
        preds_inv = scaler.inverse_transform(
            np.array(preds_run).reshape(-1, 1)
        ).flatten()
        all_preds.append(preds_inv)

    all_preds = np.array(all_preds)
    lower = pd.Series(np.percentile(all_preds, 2.5, axis=0), index=forecast_idx)
    upper = pd.Series(np.percentile(all_preds, 97.5, axis=0), index=forecast_idx)

    # MAPE
    mape = None
    if test_series is not None and len(test_series) > 0:
        common = test_series.index.intersection(forecast.index)
        if len(common) > 0:
            actual = test_series.loc[common].values
            predicted = forecast.loc[common].values
            mape = float((np.abs((actual - predicted) / actual)).mean() * 100)

    return {
        "model": model_name,
        "forecast": forecast,
        "lower_ci": lower,
        "upper_ci": upper,
        "mape": mape,
        "train_size": len(train_series),
        "history": train_series,
        "test": test_series,
        "look_back": look_back,
    }

##BACKTEST — All models, all commodities
def run_backtest(holdback_months: int = 6):
    with engine.connect() as conn:
        commodities = conn.execute(text(
            "SELECT commodity_code, commodity_name FROM commodity_master"
        )).fetchall()

    print(f"\n{'=' * 105}")
    print(f" BACKTEST REPORT — {holdback_months}-Month Holdback | ETS vs LSTM vs BiLSTM")
    print(f"{'=' * 105}")
    print(f"\n  {'Commodity':<25} {'Data':>6} {'ETS MAPE':>10} {'LSTM MAPE':>11} {'BiLSTM MAPE':>13} {'Winner':>12}")
    print(f"{'─' * 105}")

    all_results = []

    for code, name in commodities:
        series = get_commodity_series(code)
        if len(series) < 36:
            print(f"  {name:<25} {len(series):>6} {'N/A':>10} {'N/A':>11} {'N/A':>13} {'—':>12}")
            continue

        print(f"  {name:<25} {len(series):>6}", end="  ", flush=True)

        # ETS
        print("ETS..", end="", flush=True)
        ets = forecast_ets(series, holdback=holdback_months)
        ets_mape = ets["mape"] if ets and ets["mape"] is not None else None

        # LSTM
        print("LSTM..", end="", flush=True)
        lstm = forecast_lstm(series, holdback=holdback_months, bidirectional=False)
        lstm_mape = lstm["mape"] if lstm and lstm["mape"] is not None else None

        # BiLSTM
        print("BiLSTM..", end="", flush=True)
        bilstm = forecast_lstm(series, holdback=holdback_months, bidirectional=True)
        bilstm_mape = bilstm["mape"] if bilstm and bilstm["mape"] is not None else None

        # Format
        ets_str = f"{ets_mape:.1f}%" if ets_mape is not None else "N/A"
        lstm_str = f"{lstm_mape:.1f}%" if lstm_mape is not None else "N/A"
        bilstm_str = f"{bilstm_mape:.1f}%" if bilstm_mape is not None else "N/A"

        # Winner
        mapes = {}
        if ets_mape is not None: mapes["ETS"] = ets_mape
        if lstm_mape is not None: mapes["LSTM"] = lstm_mape
        if bilstm_mape is not None: mapes["BiLSTM"] = bilstm_mape

        winner = min(mapes, key=mapes.get) if mapes else "—"

        print(f"\r  {name:<25} {len(series):>6} {ets_str:>10} {lstm_str:>11} {bilstm_str:>13} {winner:>12}")

        all_results.append({
            "commodity": name, "data_pts": len(series),
            "ets": ets_mape, "lstm": lstm_mape, "bilstm": bilstm_mape,
            "winner": winner
        })

    print(f"{'─' * 105}")

    # Averages
    ets_vals = [r["ets"] for r in all_results if r["ets"] is not None]
    lstm_vals = [r["lstm"] for r in all_results if r["lstm"] is not None]
    bilstm_vals = [r["bilstm"] for r in all_results if r["bilstm"] is not None]

    ets_avg = np.mean(ets_vals) if ets_vals else 0
    lstm_avg = np.mean(lstm_vals) if lstm_vals else 0
    bilstm_avg = np.mean(bilstm_vals) if bilstm_vals else 0

    print(f"\n  {'AVERAGE':<25} {'':>6} {ets_avg:>9.1f}% {lstm_avg:>10.1f}% {bilstm_avg:>12.1f}%")

    # Winner counts
    wins = {"ETS": 0, "LSTM": 0, "BiLSTM": 0}
    for r in all_results:
        if r["winner"] in wins:
            wins[r["winner"]] += 1

    print(f"\n  Wins:  ETS = {wins['ETS']}  |  LSTM = {wins['LSTM']}  |  BiLSTM = {wins['BiLSTM']}")
    print(f"\n   MAPE < 5% = Excellent | 5-10% = Good | 10-15% = Fair | >15% = Directional only")
    print(f"{'=' * 105}\n")

##FORECAST COMPARISON REPORT
def print_forecast_report(commodity_code: str, forecast_months: int = 12):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT commodity_name FROM commodity_master WHERE commodity_code = :code"
        ), {"code": commodity_code}).fetchone()
    name = row[0] if row else commodity_code

    series = get_commodity_series(commodity_code)
    if len(series) < 36:
        print(f"\nNot enough data for {commodity_code}: {len(series)} months")
        return

    print(f"\n{'=' * 95}")
    print(f" COMMODITY FORECAST — {name} ({commodity_code})")
    print(f"{'=' * 95}")
    print(f"  Data points:  {len(series)} months ({series.index[0].strftime('%b %Y')} — {series.index[-1].strftime('%b %Y')})")
    print(f"  Last price:   ${series.iloc[-1]:,.2f}")
    print(f"  Horizon:      {forecast_months} months")

    print(f"\n  Training models...")
    print(f"    ETS...", end="", flush=True)
    ets = forecast_ets(series, forecast_months)
    print(f" done")

    print(f"    LSTM...", end="", flush=True)
    lstm = forecast_lstm(series, forecast_months, bidirectional=False)
    print(f" done")

    print(f"    BiLSTM...", end="", flush=True)
    bilstm = forecast_lstm(series, forecast_months, bidirectional=True)
    print(f" done")

    # Side by side forecast table
    print(f"\n{'─' * 95}")
    print(f"  {'Month':<12} {'ETS':>14} {'LSTM':>14} {'BiLSTM':>14} {'ETS 95% CI':>22}")
    print(f"{'─' * 95}")

    for i in range(forecast_months):
        parts = []

        # Date
        if ets and i < len(ets["forecast"]):
            dt = ets["forecast"].index[i].strftime("%b %Y")
        elif lstm and i < len(lstm["forecast"]):
            dt = lstm["forecast"].index[i].strftime("%b %Y")
        else:
            dt = f"Month {i+1}"
        parts.append(f"  {dt:<12}")

        # ETS
        if ets and i < len(ets["forecast"]):
            parts.append(f"${ets['forecast'].iloc[i]:>12,.2f}")
        else:
            parts.append(f"{'—':>14}")

        # LSTM
        if lstm and i < len(lstm["forecast"]):
            parts.append(f"${lstm['forecast'].iloc[i]:>12,.2f}")
        else:
            parts.append(f"{'—':>14}")

        # BiLSTM
        if bilstm and i < len(bilstm["forecast"]):
            parts.append(f"${bilstm['forecast'].iloc[i]:>12,.2f}")
        else:
            parts.append(f"{'—':>14}")

        # ETS CI
        if ets and i < len(ets["forecast"]):
            lo = ets["lower_ci"].iloc[i]
            hi = ets["upper_ci"].iloc[i]
            parts.append(f"  ${lo:>8,.0f} — ${hi:>8,.0f}")
        else:
            parts.append(f"{'—':>22}")

        print("".join(parts))

    print(f"{'─' * 95}")

    # Direction summary
    print(f"\n  Direction over {forecast_months} months:")
    for label, result in [("ETS", ets), ("LSTM", lstm), ("BiLSTM", bilstm)]:
        if result and len(result["forecast"]) > 0:
            last = series.iloc[-1]
            end = result["forecast"].iloc[-1]
            pct = ((end - last) / last) * 100
            arrow = "📈 UP" if pct > 0 else "📉 DOWN"
            print(f"    {label:<10} {arrow} {abs(pct):.1f}%  →  ${end:,.2f}")

    print(f"\nConfidence intervals widen over time — use with caution beyond 6 months")
    print(f"{'=' * 95}\n")

##REGRESSION
def run_regression(material: str):
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import r2_score, mean_absolute_error

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT mcm.commodity_code, cm.commodity_name
            FROM material_commodity_map mcm
            JOIN commodity_master cm ON mcm.commodity_code = cm.commodity_code
            WHERE mcm.material_from_drawing = :mat
            ORDER BY mcm.weight DESC LIMIT 1
        """), {"mat": material}).fetchone()

    if not row:
        print(f"\nNo commodity mapping for {material}")
        return

    commodity_code, commodity_name = row[0], row[1]

    supplier_df = pd.read_sql(text("""
        SELECT sm.year, sm.month, sm.supplier, AVG(sm.index_cost) AS avg_index_cost
        FROM surcharge_monthly sm
        JOIN parts_master pm ON sm.part_number = pm.part_number
        WHERE pm.material_from_drawing = :mat
          AND sm.index_cost IS NOT NULL AND sm.index_cost != 0
        GROUP BY sm.year, sm.month, sm.supplier
    """), engine, params={"mat": material})

    if supplier_df.empty:
        print(f"\nNo supplier data for {material}")
        return

    supplier_df["date"] = pd.to_datetime(
        supplier_df["year"].astype(str) + "-" +
        supplier_df["month"].astype(str).str.zfill(2) + "-01")

    commodity_series = get_commodity_series(commodity_code)
    if commodity_series.empty:
        print(f"\nNo commodity data for {commodity_code}")
        return

    comm_df = commodity_series.reset_index()
    comm_df.columns = ["date", "commodity_price"]

    print(f"\n{'=' * 80}")
    print(f" REGRESSION — {material} vs {commodity_name}")
    print(f"{'=' * 80}")

    suppliers = supplier_df["supplier"].unique()
    print(f"\n  {'Supplier':<22} {'R²':>8} {'MAE':>10} {'Corr':>8} {'N':>6} {'Interpretation':<20}")
    print(f"{'─' * 80}")

    for sup in suppliers:
        sdf = supplier_df[supplier_df["supplier"] == sup]
        merged = sdf.merge(comm_df, on="date", how="inner")
        if len(merged) < 6:
            print(f"  {sup:<22} {'—':>8} {'—':>10} {'—':>8} {len(merged):>6} {'Too few data pts':<20}")
            continue

        X = merged[["commodity_price"]].values
        y = merged["avg_index_cost"].values
        reg = LinearRegression().fit(X, y)
        y_pred = reg.predict(X)
        r2 = r2_score(y, y_pred)
        mae = mean_absolute_error(y, y_pred)
        corr = np.corrcoef(merged["commodity_price"], merged["avg_index_cost"])[0, 1]

        if r2 > 0.7:   interp = "Strong link"
        elif r2 > 0.4: interp = "Moderate link"
        elif r2 > 0.1: interp = "Weak link"
        else:           interp = "No link"

        print(f"  {sup:<22} {r2:>8.3f} ${mae:>9.4f} {corr:>8.3f} {len(merged):>6} {interp:<20}")

    print(f"{'─' * 80}")
    print(f"\n  R² > 0.7 = commodity drives supplier price (hedge)")
    print(f"  R² < 0.3 = supplier price decoupled (negotiate harder)")
    print(f"{'=' * 80}\n")


##MAIN
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forecasting")
    parser.add_argument("--commodity", type=str, help="Commodity code")
    parser.add_argument("--months", type=int, default=12, help="Forecast horizon")
    parser.add_argument("--backtest", action="store_true", help="Backtest all models")
    parser.add_argument("--regression", action="store_true", help="Supplier regression")
    parser.add_argument("--material", type=str, help="Material for regression")

    args = parser.parse_args()

    if args.backtest:
        run_backtest(holdback_months=6)
    elif args.regression and args.material:
        run_regression(args.material)
    elif args.commodity:
        print_forecast_report(args.commodity, args.months)
    else:
        run_backtest(holdback_months=6)
        for code in ["MWUS_HR_COIL", "LME_ALUMINIUM", "LME_NICKEL"]:
            print_forecast_report(code, 12)
        for mat in ["LC STEEL", "CAST", "AUSTENITIC SS"]:
            run_regression(mat)
