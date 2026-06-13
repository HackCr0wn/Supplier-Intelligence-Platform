import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import warnings
from sqlalchemy import text
from utils.db import get_engine

warnings.filterwarnings("ignore")
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

engine = get_engine()


#Data loader
@st.cache_data(ttl=300)
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


#Model 1: ETS
def forecast_ets(series, months):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    try:
        model = ExponentialSmoothing(
            series, trend="add", seasonal="add",
            seasonal_periods=12, damped_trend=True
        ).fit(optimized=True)
    except Exception:
        try:
            model = ExponentialSmoothing(
                series, trend="add", damped_trend=True
            ).fit(optimized=True)
        except Exception:
            return None

    forecast = model.forecast(months)
    residuals = model.resid.dropna()
    std_r = residuals.std()
    steps = np.arange(1, months + 1)
    ci = 1.96 * std_r * np.sqrt(steps)

    return {
        "name": "ETS (Holt-Winters)",
        "forecast": forecast,
        "lower": forecast - ci,
        "upper": forecast + ci,
    }


#Model 2 & 3: LSTM / BiLSTM 
def create_sequences(data, look_back=12):
    X, y = [], []
    for i in range(look_back, len(data)):
        X.append(data[i - look_back:i, 0])
        y.append(data[i, 0])
    return np.array(X), np.array(y)


def forecast_lstm(series, months, bidirectional=False):
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Bidirectional, Dense, Dropout
    from tensorflow.keras.callbacks import EarlyStopping
    from sklearn.preprocessing import MinMaxScaler

    tf.random.set_seed(42)
    name = "BiLSTM" if bidirectional else "LSTM"

    if len(series) < 36:
        return None

    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled = scaler.fit_transform(series.values.reshape(-1, 1))

    look_back = min(24, len(scaled) // 3)
    if look_back < 3:
        return None

    X, y = create_sequences(scaled, look_back)
    if len(X) < 10:
        return None

    X = X.reshape(X.shape[0], X.shape[1], 1)

    model = Sequential()
    if bidirectional:
        model.add(Bidirectional(LSTM(64, return_sequences=True), input_shape=(look_back, 1)))
        model.add(Dropout(0.2))
        model.add(Bidirectional(LSTM(32)))
    else:
        model.add(LSTM(64, return_sequences=True, input_shape=(look_back, 1)))
        model.add(Dropout(0.2))
        model.add(LSTM(32))

    model.add(Dropout(0.2))
    model.add(Dense(16, activation='relu'))
    model.add(Dense(1))

    model.compile(optimizer='adam', loss='mse')

    early_stop = EarlyStopping(monitor='loss', patience=15,
                                restore_best_weights=True, min_delta=1e-6)

    model.fit(X, y, epochs=200, batch_size=min(32, len(X) // 2),
              callbacks=[early_stop], verbose=0, shuffle=False)

    # Forecast
    seed = scaled[-look_back:].flatten().tolist()
    predictions = []
    current = list(seed)

    for _ in range(months):
        x_input = np.array(current[-look_back:]).reshape(1, look_back, 1)
        pred = model.predict(x_input, verbose=0)[0, 0]
        predictions.append(pred)
        current.append(pred)

    predictions_inv = scaler.inverse_transform(
        np.array(predictions).reshape(-1, 1)
    ).flatten()

    forecast_idx = pd.date_range(
        start=series.index[-1] + pd.DateOffset(months=1),
        periods=months, freq='MS'
    )
    forecast = pd.Series(predictions_inv, index=forecast_idx)

    # MC Dropout CI (20 runs for speed in dashboard)
    all_preds = []
    for _ in range(20):
        preds_run = []
        curr = list(seed)
        for _ in range(months):
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

    return {
        "name": name,
        "forecast": forecast,
        "lower": lower,
        "upper": upper,
    }


#Backtest helper
def backtest_model(series, holdback, model_fn, **kwargs):
    train = series[:-holdback]
    test = series[-holdback:]

    result = model_fn(train, holdback, **kwargs)
    if not result:
        return None

    common = test.index.intersection(result["forecast"].index)
    if len(common) == 0:
        return None

    actual = test.loc[common].values
    predicted = result["forecast"].loc[common].values
    mape = float((np.abs((actual - predicted) / actual)).mean() * 100)
    return mape


def render():
    st.markdown('<p class="main-header">Commodity Forecast</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">ETS vs LSTM vs BiLSTM — with confidence intervals</p>',
                unsafe_allow_html=True)

    #Tabs
    tab1, tab2 = st.tabs(["Forecast", "Backtest Comparison"])

    # TAB 1: FORECAST
    with tab1:
        commodities = pd.read_sql(text(
            "SELECT commodity_code, commodity_name FROM commodity_master ORDER BY commodity_name"
        ), engine)

        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            selected = st.selectbox("Select Commodity",
                commodities["commodity_code"].tolist(),
                format_func=lambda x: commodities[
                    commodities["commodity_code"] == x]["commodity_name"].values[0],
                key="fc_commodity")
        with col2:
            months = st.slider("Months Ahead", 3, 24, 12, key="fc_months")
        with col3:
            models_to_run = st.multiselect("Models",
                ["ETS", "LSTM", "BiLSTM"],
                default=["ETS"],
                key="fc_models")

        comm_name = commodities[
            commodities["commodity_code"] == selected]["commodity_name"].values[0]

        series = get_commodity_series(selected)
        if len(series) < 36:
            st.warning(f"Not enough data for {comm_name} ({len(series)} months, need 36+)")
            return

        #KPI cards
        c1, c2, c3 = st.columns(3)
        c1.metric("Current Price", f"${series.iloc[-1]:,.2f}")
        c2.metric("Data Points", f"{len(series)} months")
        c3.metric("Range", f"{series.index[0].strftime('%b %Y')} — {series.index[-1].strftime('%b %Y')}")

        st.markdown("---")

        if not models_to_run:
            st.info("Select at least one model from the dropdown above.")
            return

        #Run selected models
        results = {}
        progress = st.progress(0, text="Training models...")

        total_steps = len(models_to_run)
        for i, model_name in enumerate(models_to_run):
            progress.progress((i) / total_steps, text=f"Training {model_name}...")

            if model_name == "ETS":
                results["ETS"] = forecast_ets(series, months)
            elif model_name == "LSTM":
                results["LSTM"] = forecast_lstm(series, months, bidirectional=False)
            elif model_name == "BiLSTM":
                results["BiLSTM"] = forecast_lstm(series, months, bidirectional=True)

        progress.progress(1.0, text="All models trained!")

        #Chart
        fig = go.Figure()

        # Historical prices
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines",
            name="Historical", line=dict(color="#3B82F6", width=2),
            hovertemplate="%{x|%b %Y}: $%{y:,.2f}<extra></extra>"
        ))

        # Model colors
        model_colors = {
            "ETS": {"line": "#EF4444", "fill": "rgba(239,68,68,0.12)"},
            "LSTM": {"line": "#10B981", "fill": "rgba(16,185,129,0.12)"},
            "BiLSTM": {"line": "#8B5CF6", "fill": "rgba(139,92,246,0.12)"},
        }

        for model_name, r in results.items():
            if not r:
                continue

            colors = model_colors[model_name]

            # Forecast line
            fig.add_trace(go.Scatter(
                x=r["forecast"].index, y=r["forecast"].values,
                mode="lines+markers",
                name=f"{model_name} Forecast",
                line=dict(color=colors["line"], width=2, dash="dash"),
                marker=dict(size=5),
                hovertemplate=f"<b>{model_name}</b><br>" +
                    "%{x|%b %Y}: $%{y:,.2f}<extra></extra>"
            ))

            # Confidence interval
            fig.add_trace(go.Scatter(
                x=list(r["forecast"].index) + list(r["forecast"].index[::-1]),
                y=list(r["upper"].values) + list(r["lower"].values[::-1]),
                fill="toself", fillcolor=colors["fill"],
                line=dict(color="rgba(0,0,0,0)"),
                name=f"{model_name} 95% CI",
                hoverinfo="skip"
            ))

        fig.update_layout(
            title=dict(text=f"{comm_name} — {months}-Month Forecast",
                       font=dict(size=22, color="black", family="Arial")),
            xaxis=dict(
                title=dict(text="Date", font=dict(size=18, color="black", family="Arial Black")),
                tickfont=dict(size=14, color="black")),
            yaxis=dict(
                title=dict(text="Price (USD)", font=dict(size=18, color="black", family="Arial Black")),
                tickfont=dict(size=14, color="black")),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
            height=600, template="plotly_white",
        )

        st.plotly_chart(fig, use_container_width=True)

        #Direction summary
        st.subheader("Direction Summary")
        dir_cols = st.columns(len(results))

        for i, (model_name, r) in enumerate(results.items()):
            if not r:
                continue
            last = series.iloc[-1]
            end = r["forecast"].iloc[-1]
            pct = ((end - last) / last) * 100
            direction = "UP" if pct > 0 else "DOWN"

            with dir_cols[i]:
                st.metric(
                    model_name,
                    f"${end:,.2f}",
                    f"{pct:+.1f}% ({direction})"
                )

        #Forecast data table
        with st.expander("Forecast Data"):
            table_data = {}

            for model_name, r in results.items():
                if not r:
                    continue
                table_data[f"{model_name} Forecast"] = r["forecast"].values
                table_data[f"{model_name} Lower"] = r["lower"].values
                table_data[f"{model_name} Upper"] = r["upper"].values

            if table_data:
                # Use first model's index for dates
                first_result = next(r for r in results.values() if r is not None)
                fdf = pd.DataFrame(table_data, index=first_result["forecast"].index)
                fdf.index = fdf.index.strftime("%b %Y")
                fdf.index.name = "Month"

                st.dataframe(fdf.style.format("${:,.2f}"),
                            use_container_width=True)

        with st.expander("Export"):
            if table_data:
                csv = fdf.to_csv()
                st.download_button("Download CSV", csv,
                                  f"forecast_{selected}.csv", "text/csv")

        st.caption("Confidence intervals widen over time — use with caution beyond 6 months. "
                   "LSTM/BiLSTM CI uses MC Dropout approximation (20 runs).")

# TAB 2: BACKTEST COMPARISON
    with tab2:
        st.subheader("Model Comparison — 6-Month Holdback Backtest")
        st.caption("Tests each model by hiding the last 6 months, predicting them, "
                   "and measuring error (MAPE)")

        # Check if backtest already cached
        if "backtest_results" not in st.session_state:
            if st.button("Run Backtest (takes 8-12 minutes)", type="primary"):
                commodities_list = pd.read_sql(text(
                    "SELECT commodity_code, commodity_name FROM commodity_master"
                ), engine)

                all_results = []
                progress = st.progress(0, text="Starting backtest...")
                total = len(commodities_list)

                for idx, (_, row) in enumerate(commodities_list.iterrows()):
                    code = row["commodity_code"]
                    name = row["commodity_name"]
                    progress.progress(idx / total, text=f"Backtesting {name}...")

                    s = get_commodity_series(code)
                    if len(s) < 36:
                        all_results.append({
                            "Commodity": name, "Data": len(s),
                            "ETS MAPE": None, "LSTM MAPE": None,
                            "BiLSTM MAPE": None, "Winner": "—"
                        })
                        continue

                    ets_mape = backtest_model(s, 6, forecast_ets)
                    lstm_mape = backtest_model(s, 6, forecast_lstm, bidirectional=False)
                    bilstm_mape = backtest_model(s, 6, forecast_lstm, bidirectional=True)

                    mapes = {}
                    if ets_mape is not None: mapes["ETS"] = ets_mape
                    if lstm_mape is not None: mapes["LSTM"] = lstm_mape
                    if bilstm_mape is not None: mapes["BiLSTM"] = bilstm_mape

                    winner = min(mapes, key=mapes.get) if mapes else "—"

                    all_results.append({
                        "Commodity": name,
                        "Data": len(s),
                        "ETS MAPE": ets_mape,
                        "LSTM MAPE": lstm_mape,
                        "BiLSTM MAPE": bilstm_mape,
                        "Winner": winner,
                    })

                progress.progress(1.0, text="Backtest complete!")
                st.session_state["backtest_results"] = all_results
            else:
                st.info("Click the button above to run the full backtest. "
                        "This trains 3 models on each of 8 commodities (24 total).")
                return

        # Display results
        if "backtest_results" in st.session_state:
            results_df = pd.DataFrame(st.session_state["backtest_results"])

            # Summary cards
            ets_vals = results_df["ETS MAPE"].dropna()
            lstm_vals = results_df["LSTM MAPE"].dropna()
            bilstm_vals = results_df["BiLSTM MAPE"].dropna()

            wins = results_df["Winner"].value_counts()

            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("ETS Avg MAPE",
                       f"{ets_vals.mean():.1f}%" if len(ets_vals) > 0 else "N/A",
                       f"Wins: {wins.get('ETS', 0)}")
            sc2.metric("LSTM Avg MAPE",
                       f"{lstm_vals.mean():.1f}%" if len(lstm_vals) > 0 else "N/A",
                       f"Wins: {wins.get('LSTM', 0)}")
            sc3.metric("BiLSTM Avg MAPE",
                       f"{bilstm_vals.mean():.1f}%" if len(bilstm_vals) > 0 else "N/A",
                       f"Wins: {wins.get('BiLSTM', 0)}")

            st.markdown("---")

            # Results table with color coding
            def color_mape(val):
                if pd.isna(val):
                    return ""
                if val < 5:
                    return "background-color: #D1FAE5"  # green
                elif val < 10:
                    return "background-color: #FEF3C7"  # yellow
                elif val < 15:
                    return "background-color: #FED7AA"  # orange
                else:
                    return "background-color: #FECACA"  # red

            def bold_winner(row):
                styles = [""] * len(row)
                mapes = {}
                for col in ["ETS MAPE", "LSTM MAPE", "BiLSTM MAPE"]:
                    if pd.notna(row[col]):
                        mapes[col] = row[col]
                if mapes:
                    best_col = min(mapes, key=mapes.get)
                    idx = row.index.get_loc(best_col)
                    styles[idx] = "font-weight: bold"
                return styles

            styled = results_df.style.format({
                "ETS MAPE": lambda x: f"{x:.1f}%" if pd.notna(x) else "—",
                "LSTM MAPE": lambda x: f"{x:.1f}%" if pd.notna(x) else "—",
                "BiLSTM MAPE": lambda x: f"{x:.1f}%" if pd.notna(x) else "—",
            }).map(color_mape, subset=["ETS MAPE", "LSTM MAPE", "BiLSTM MAPE"]
            ).apply(bold_winner, axis=1)

            st.dataframe(styled, use_container_width=True, hide_index=True)

            # Bar chart comparison
            st.subheader("MAPE Comparison Chart")

            chart_df = results_df[results_df["ETS MAPE"].notna()].copy()

            fig = go.Figure()

            fig.add_trace(go.Bar(
                name="ETS", x=chart_df["Commodity"], y=chart_df["ETS MAPE"],
                marker_color="#EF4444"
            ))
            fig.add_trace(go.Bar(
                name="LSTM", x=chart_df["Commodity"], y=chart_df["LSTM MAPE"],
                marker_color="#10B981"
            ))
            fig.add_trace(go.Bar(
                name="BiLSTM", x=chart_df["Commodity"], y=chart_df["BiLSTM MAPE"],
                marker_color="#8B5CF6"
            ))

            fig.add_hline(y=10, line_dash="dot", line_color="gray",
                         annotation_text="10% threshold", annotation_position="top right")

            fig.update_layout(
                barmode="group",
                title=dict(text="MAPE by Commodity — Lower is Better",
                           font=dict(size=20, color="black")),
                xaxis=dict(tickfont=dict(size=12, color="black")),
                yaxis=dict(title=dict(text="MAPE %",
                           font=dict(size=16, color="black", family="Arial Black")),
                           tickfont=dict(size=14, color="black")),
                height=450, template="plotly_white",
                legend=dict(orientation="h", yanchor="bottom", y=-0.3,
                           xanchor="center", x=0.5),
            )

            st.plotly_chart(fig, use_container_width=True)

            # Interpretation
            st.markdown("---")
            st.subheader("Interpretation Guide")

            ic1, ic2 = st.columns(2)
            with ic1:
                st.markdown("""
                **MAPE Thresholds:**
                - Below 5% — Excellent (reliable for budgeting)
                - 5-10% — Good (usable for planning)
                - 10-15% — Fair (directional guidance only)
                - Above 15% — Poor (trend awareness only)
                """)
            with ic2:
                st.markdown("""
                **Why ETS Often Wins:**
                - 400 monthly data points favors simpler models
                - LSTM needs 1,000+ points to outperform
                - ETS has 3 parameters vs LSTM's 15,000+
                - Consistent with M4 Competition results
                """)

            # Clear button
            if st.button("Clear Backtest Results"):
                del st.session_state["backtest_results"]
                st.rerun()