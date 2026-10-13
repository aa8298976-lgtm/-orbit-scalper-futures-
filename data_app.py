
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="ORBIT Data Loader",
    page_icon="📊",
    layout="wide",
)

API_URL = "https://api.bybit.com/v5/market/kline"
INTERVAL_MS = 5 * 60 * 1000

st.title("ORBIT Scalper Futures")
st.subheader("دریافت داده واقعی BTCUSDT Futures")
st.info(
    "منبع داده: Bybit Linear Perpetual. "
    "این برنامه فقط داده عمومی دریافت می‌کند و سفارش ثبت نمی‌کند."
)


def fetch_klines(days_back):
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start_ms = int(
        (now - timedelta(days=days_back)).timestamp() * 1000
    )

    all_rows = []
    cursor_end = now_ms
    session = requests.Session()
    progress = st.progress(0)
    status = st.empty()

    try:
        while cursor_end > start_ms:
            params = {
                "category": "linear",
                "symbol": "BTCUSDT",
                "interval": "5",
                "start": start_ms,
                "end": cursor_end,
                "limit": 1000,
            }

            response = session.get(
                API_URL,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()

            if payload.get("retCode") != 0:
                raise RuntimeError(
                    payload.get("retMsg", "خطای Bybit API")
                )

            rows = payload.get("result", {}).get("list", [])

            if not rows:
                break

            # Bybit داده‌ها را از جدید به قدیم برمی‌گرداند.
            all_rows.extend(rows)
            times = [int(row[0]) for row in rows]
            oldest_ms = min(times)

            status.write(
                f"کندل‌های دریافت‌شده: {len(all_rows):,}"
            )
            progress.progress(
                min(
                    max((now_ms - oldest_ms) /
                        (now_ms - start_ms), 0),
                    1,
                )
            )

            if oldest_ms <= start_ms:
                break

            next_end = oldest_ms - 1
            if next_end >= cursor_end:
                break

            cursor_end = next_end
            time.sleep(0.12)

    finally:
        session.close()

    if not all_rows:
        raise RuntimeError(
            "هیچ داده‌ای دریافت نشد. دسترسی به API را بررسی کن."
        )

    columns = [
        "timestamp", "open", "high", "low", "close",
        "volume", "turnover",
    ]
    raw = pd.DataFrame(all_rows, columns=columns)
    raw["timestamp_ms"] = pd.to_numeric(
        raw["timestamp"], errors="coerce"
    )
    raw = raw.dropna(subset=["timestamp_ms"])
    raw["timestamp_ms"] = raw["timestamp_ms"].astype("int64")
    raw = raw.drop_duplicates(subset=["timestamp_ms"])

    # فقط کندل‌هایی که کاملاً بسته شده‌اند.
    raw = raw[
        (raw["timestamp_ms"] >= start_ms)
        & (raw["timestamp_ms"] + INTERVAL_MS <= now_ms)
    ].copy()

    raw["timestamp"] = pd.to_datetime(
        raw["timestamp_ms"], unit="ms", utc=True
    )

    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw = raw.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )
    raw = raw.sort_values("timestamp").reset_index(drop=True)

    output = raw[
        ["timestamp", "open", "high", "low", "close", "volume"]
    ].copy()

    output["timestamp"] = output["timestamp"].dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return output


days = st.selectbox(
    "بازه تاریخی",
    [7, 30, 60, 90],
    index=3,
    format_func=lambda value: f"{value} روز اخیر",
)

st.caption("نماد: BTCUSDT | بازار: قرارداد خطی دائمی | تایم‌فریم: ۵ دقیقه")

if st.button("دریافت داده و ساخت CSV", type="primary"):
    try:
        with st.spinner("در حال دریافت داده‌های تاریخی..."):
            data = fetch_klines(days)

        csv_data = data.to_csv(index=False).encode("utf-8")

        st.success("دریافت داده‌ها با موفقیت انجام شد.")
        st.metric("تعداد کندل‌های بسته‌شده", f"{len(data):,}")

        if len(data) < days * 24 * 12 * 0.95:
            st.warning(
                "تعداد کندل‌ها کمتر از مقدار مورد انتظار است. "
                "قبل از بک‌تست، بازه زمانی داده‌ها را بررسی کن."
            )

        st.write("آخرین کندل‌ها:")
        st.dataframe(data.tail(15), use_container_width=True)

        st.download_button(
            "دانلود CSV برای بک‌تست",
            data=csv_data,
            file_name=f"BTCUSDT_BYBIT_5m_{days}days.csv",
            mime="text/csv",
            type="primary",
        )

        st.caption(
            "ستون‌ها با ورودی موتور بک‌تست ORBIT سازگارند. "
            "این داده‌ها تضمین‌کننده سودآوری نیستند."
        )

    except Exception as exc:
        st.error(f"دریافت داده ناموفق بود: {exc}")
        st.info(
            "اگر خطای اتصال یا دسترسی دریافت کردی، "
            "ممکن است API از محیط میزبانی قابل دسترسی نباشد."
        )
