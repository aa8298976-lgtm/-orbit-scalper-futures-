
import numpy as np
import pandas as pd


def prepare_data(source):
    """آماده‌سازی کندل‌های M5 و محاسبه اندیکاتورها."""
    df = source.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"ستون‌های ناقص: {', '.join(sorted(missing))}")

    df["timestamp"] = pd.to_datetime(
        df["timestamp"], utc=True, errors="coerce"
    )

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )

    if len(df) < 250:
        raise ValueError("حداقل ۲۵۰ کندل معتبر لازم است.")

    close = df["close"]

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(
        alpha=1 / 14, adjust=False
    ).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # ساخت کندل‌های M15 از داده M5
    indexed = df.set_index("timestamp")
    m15 = indexed.resample(
        "15min", label="right", closed="right"
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).dropna()

    m15["ema50"] = m15["close"].ewm(
        span=50, adjust=False
    ).mean()
    m15["ema200"] = m15["close"].ewm(
        span=200, adjust=False
    ).mean()

    # روند فقط از کندل M15 تکمیل‌شده قبلی
    m15["trend_up"] = m15["ema50"].shift(1) > m15["ema200"].shift(1)
    m15["trend_down"] = m15["ema50"].shift(1) < m15["ema200"].shift(1)

    trend = m15[["trend_up", "trend_down"]].reset_index()
    trend = trend.rename(columns={"timestamp": "trend_time"})

    df = pd.merge_asof(
        df.sort_values("timestamp"),
        trend.sort_values("trend_time"),
        left_on="timestamp",
        right_on="trend_time",
        direction="backward",
        allow_exact_matches=True,
    )

    df["trend_up"] = df["trend_up"].fillna(False).astype(bool)
    df["trend_down"] = df["trend_down"].fillna(False).astype(bool)

    df["bullish"] = df["close"] > df["open"]
    df["bearish"] = df["close"] < df["open"]

    df["near_pullback"] = (
        (df["close"] - df["ema20"]).abs() <= 0.5 * df["atr"]
    )

    df["long_signal"] = (
        df["trend_up"]
        & df["bullish"]
        & df["near_pullback"]
        & (df["rsi"].shift(1) <= 50)
        & (df["rsi"] > 50)
    )

    df["short_signal"] = (
        df["trend_down"]
        & df["bearish"]
        & df["near_pullback"]
        & (df["rsi"].shift(1) >= 50)
        & (df["rsi"] < 50)
    )

    return df.reset_index(drop=True)


def run_backtest(
    data_by_symbol,
    initial_balance=1000.0,
    risk_pct=1.0,
    max_open_risk_pct=10.0,
    leverage=3.0,
    fee_pct=0.05,
    slippage_pct=0.02,
    reward_risk=2.0,
    atr_mult=1.5,
):
    """
    شبیه‌سازی محلی OHLCV، بدون اتصال به صرافی.

    ورود در بازشدن کندل بعدی انجام می‌شود.
    اگر حد ضرر و حد سود در یک کندل لمس شوند،
    فرض محافظه‌کارانه این است که حد ضرر زودتر اجرا شده است.
    """
    if not data_by_symbol:
        raise ValueError("حداقل یک فایل داده بارگذاری کن.")

    if initial_balance <= 0 or leverage < 1:
        raise ValueError("سرمایه باید مثبت و لوریج حداقل ۱ باشد.")

    if not 0 < risk_pct <= 1:
        raise ValueError("ریسک هر معامله باید بین صفر و ۱ درصد باشد.")

    if not 0 < max_open_risk_pct <= 10:
        raise ValueError("سقف ریسک مجموع باید بین صفر و ۱۰ درصد باشد.")

    prepared = {
        symbol: prepare_data(frame)
        for symbol, frame in data_by_symbol.items()
    }

    # زمان‌های موجود در همه نمادها
    all_times = sorted(
        set().union(
            *[set(df["timestamp"]) for df in prepared.values()]
        )
    )

    indexed = {
        symbol: df.set_index("timestamp")
        for symbol, df in prepared.items()
    }

    balance = float(initial_balance)
    positions = []
    pending = []
    trades = []
    equity_history = []

    fee_rate = max(0.0, fee_pct) / 100
    slip_rate = max(0.0, slippage_pct) / 100
    risk_rate = risk_pct / 100
    risk_cap = max_open_risk_pct / 100

    peak_equity = balance
    max_drawdown = 0.0

    for ts in all_times:
        # ۱. اجرای سیگنال قبلی در قیمت بازشدن کندل جدید
        due = [p for p in pending if p["entry_time"] == ts]
        pending = [p for p in pending if p["entry_time"] != ts]

        for signal in due:
            symbol = signal["symbol"]
            if ts not in indexed[symbol].index:
                continue

            bar = indexed[symbol].loc[ts]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]

            side = signal["side"]
            raw_entry = float(bar["open"])
            entry = raw_entry * (
                1 + slip_rate if side == "long" else 1 - slip_rate
            )

            stop = float(signal["stop"])
            stop_distance = abs(entry - stop)

            if entry <= 0 or stop_distance <= 0:
                continue

            # محاسبه ارزش حساب با سود و زیان تحقق‌نیافته
            unrealized = 0.0
            for pos in positions:
                if ts in indexed[pos["symbol"]].index:
                    mark = indexed[pos["symbol"]].loc[ts]
                    if isinstance(mark, pd.DataFrame):
                        mark = mark.iloc[-1]
                    price = float(mark["close"])
                    pnl = (
                        (price - pos["entry"]) * pos["qty"]
                        if pos["side"] == "long"
                        else (pos["entry"] - price) * pos["qty"]
                    )
                    unrealized += pnl

            equity = balance + unrealized
            open_risk = sum(p["risk_cash"] for p in positions)
            risk_cash = equity * risk_rate

            # بدون سقف تعداد ثابت؛ فقط محدودیت ریسک و مارجین
            if risk_cash <= 0:
                continue
            if open_risk + risk_cash > equity * risk_cap:
                continue

            qty = risk_cash / stop_distance
            notional = qty * entry
            margin = notional / leverage

            used_margin = sum(
                p["notional"] / leverage for p in positions
            )

            if margin + used_margin > max(0, equity) * 0.90:
                continue

            stop = entry - stop_distance if side == "long" else entry + stop_distance
            target = (
                entry + stop_distance * reward_risk
                if side == "long"
                else entry - stop_distance * reward_risk
            )

            entry_fee = notional * fee_rate
            balance -= entry_fee

            positions.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_time": ts,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "qty": qty,
                    "risk_cash": risk_cash,
                    "notional": notional,
                    "entry_fee": entry_fee,
                }
            )

        # ۲. بررسی حد ضرر و حد سود
        for pos in positions[:]:
            symbol = pos["symbol"]
            if ts not in indexed[symbol].index:
                continue

            bar = indexed[symbol].loc[ts]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]

            high = float(bar["high"])
            low = float(bar["low"])
            exit_price = None
            reason = None

            if pos["side"] == "long":
                if low <= pos["stop"]:
                    exit_price = pos["stop"] * (1 - slip_rate)
                    reason = "SL"
                elif high >= pos["target"]:
                    exit_price = pos["target"] * (1 - slip_rate)
                    reason = "TP"
            else:
                if high >= pos["stop"]:
                    exit_price = pos["stop"] * (1 + slip_rate)
                    reason = "SL"
                elif low <= pos["target"]:
                    exit_price = pos["target"] * (1 + slip_rate)
                    reason = "TP"

            if exit_price is None:
                continue

            gross = (
                (exit_price - pos["entry"]) * pos["qty"]
                if pos["side"] == "long"
                else (pos["entry"] - exit_price) * pos["qty"]
            )

            exit_fee = abs(exit_price * pos["qty"]) * fee_rate
            balance += gross - exit_fee

            trades.append(
                {
                    "symbol": symbol,
                    "side": pos["side"],
                    "entry_time": pos["entry_time"],
                    "exit_time": ts,
                    "entry_price": pos["entry"],
                    "exit_price": exit_price,
                    "quantity": pos["qty"],
                    "gross_pnl": gross,
                    "fees": pos["entry_fee"] + exit_fee,
                    "net_pnl": gross - pos["entry_fee"] - exit_fee,
                    "exit_reason": reason,
                }
            )
            positions.remove(pos)

        # ۳. محاسبه ارزش حساب و افت سرمایه
        unrealized = 0.0
        for pos in positions:
            if ts not in indexed[pos["symbol"]].index:
                continue

            bar = indexed[pos["symbol"]].loc[ts]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]

            price = float(bar["close"])
            unrealized += (
                (price - pos["entry"]) * pos["qty"]
                if pos["side"] == "long"
                else (pos["entry"] - price) * pos["qty"]
            )

        equity = balance + unrealized
        peak_equity = max(peak_equity, equity)
        drawdown = (
            (peak_equity - equity) / peak_equity
            if peak_equity > 0
            else 0
        )
        max_drawdown = max(max_drawdown, drawdown)

        equity_history.append(
            {
                "timestamp": ts,
                "balance": balance,
                "equity": equity,
                "drawdown_pct": drawdown * 100,
            }
        )

        # ۴. تولید سیگنال برای کندل بعدی
        for symbol, df in prepared.items():
            match = df.index[df["timestamp"] == ts]
            if len(match) == 0:
                continue

            i = int(match[0])
            if i + 1 >= len(df):
                continue

            row = df.iloc[i]
            if not np.isfinite(row["atr"]) or row["atr"] <= 0:
                continue

            for side, condition in [
                ("long", bool(row["long_signal"])),
                ("short", bool(row["short_signal"])),
            ]:
                if not condition:
                    continue

                # از بازشدن موقعیت هم‌جهت تکراری جلوگیری می‌شود
                if any(
                    p["symbol"] == symbol and p["side"] == side
                    for p in positions
                ):
                    continue

                if any(
                    p["symbol"] == symbol
                    and p["side"] == side
                    for p in pending
                ):
                    continue

                next_bar = df.iloc[i + 1]
                stop_distance = float(row["atr"]) * atr_mult
                reference = float(row["close"])

                stop = (
                    reference - stop_distance
                    if side == "long"
                    else reference + stop_distance
                )

                pending.append(
                    {
                        "symbol": symbol,
                        "side": side,
                        "entry_time": next_bar["timestamp"],
                        "stop": stop,
                    }
                )

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_history)

    final_equity = (
        float(equity_df.iloc[-1]["equity"])
        if not equity_df.empty
        else initial_balance
    )

    if not trades_df.empty:
        wins = trades_df["net_pnl"] > 0
        gross_profit = trades_df.loc[wins, "net_pnl"].sum()
        gross_loss = abs(trades_df.loc[~wins, "net_pnl"].sum())

        win_rate = float(wins.mean() * 100)
        profit_factor = (
            float(gross_profit / gross_loss)
            if gross_loss > 0
            else float("inf")
        )
    else:
        win_rate = 0.0
        profit_factor = 0.0

    summary = {
        "initial_balance": initial_balance,
        "final_equity": final_equity,
        "net_return_pct": (
            (final_equity / initial_balance - 1) * 100
        ),
        "closed_trades": len(trades_df),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown * 100,
        "open_positions_at_end": len(positions),
        "open_risk_cash": sum(p["risk_cash"] for p in positions),
        "note": (
            "بک‌تست تقریبی OHLCV است؛ فاندینگ، لیکوییدشدن، "
            "مارک‌پرایس و اجرای واقعی صرافی به‌طور کامل شبیه‌سازی نشده‌اند."
        ),
    }

    return summary, trades_df, equity_df
