
import pandas as pd
import plotly.graph_objects as go

def plot_price_verification(df, stock_code):
    """
    Plot Raw vs Adjusted Price to verify data quality.
    
    Args:
        df: DataFrame with '$close' and '$factor'.
        stock_code: Instrument ID for title.
    """
    if df.empty:
        print("No data to plot.")
        return

    df = df.sort_index()
    latest_factor = df['$factor'].iloc[-1]
    if latest_factor == 0:
        latest_factor = 1.0
    adj_close_forward = df['$close'] * (df['$factor'] / latest_factor)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=adj_close_forward, name='Forward Adj'))
    fig.add_trace(go.Scatter(x=df.index, y=df['$close'], name='Raw Close', line=dict(color='gray')))
    fig.update_layout(title=f"{stock_code} Price Verification", template="plotly_white")
    fig.show()

def plot_forward_adjusted_candlestick(df, stock_code):
    if df.empty:
        print("No data to plot.")
        return
    if isinstance(df.index, pd.MultiIndex):
        if "datetime" in df.index.names:
            df = df.reset_index(level=["datetime"])
            df = df.set_index("datetime")
        else:
            df = df.reset_index(level=[df.index.names[-1]])
            df = df.set_index(df.index.names[-1])
    df = df.sort_index()
    latest_factor = df['$factor'].iloc[-1]
    if latest_factor == 0:
        latest_factor = 1.0
    ratio = df['$factor'] / latest_factor
    plot_df = pd.DataFrame({
        "open": df['$open'] * ratio,
        "high": df['$high'] * ratio,
        "low": df['$low'] * ratio,
        "close": df['$close'] * ratio,
        "raw_close": df['$close']
    }, index=pd.to_datetime(df.index))
    fig = go.Figure(data=[go.Candlestick(
        x=plot_df.index,
        open=plot_df["open"],
        high=plot_df["high"],
        low=plot_df["low"],
        close=plot_df["close"],
        name="Forward Adj"
    )])
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df["raw_close"], name="Raw Close", line=dict(color="gray", width=1)))
    fig.update_layout(title=f"{stock_code} Forward Adjusted Candlestick", template="plotly_white", xaxis_rangeslider_visible=False)
    fig.show()

def plot_equity_curve(history_df, benchmark_df=None):
    """
    Plot strategy equity curve.
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=history_df.index, y=history_df['total_assets'], name='Strategy'))
    if benchmark_df is not None:
        start_equity = history_df['total_assets'].iloc[0]
        bench_ret = benchmark_df['close'] / benchmark_df['close'].iloc[0]
        fig.add_trace(go.Scatter(x=benchmark_df.index, y=bench_ret * start_equity, name='Benchmark'))
    fig.update_layout(title="Strategy Equity Curve", template="plotly_white")
    fig.show()

def plot_ic_series(ic_series, title="IC Series"):
    """
    Plot Information Coefficient series.
    """
    fig = go.Figure()
    fig.add_trace(go.Bar(x=ic_series.index, y=ic_series.values, name='IC'))
    mean_ic = ic_series.mean()
    fig.add_trace(go.Scatter(x=ic_series.index, y=[mean_ic] * len(ic_series), name=f"Mean: {mean_ic:.4f}", line=dict(color='red', dash='dash')))
    fig.update_layout(title=title, template="plotly_white")
    fig.show()
