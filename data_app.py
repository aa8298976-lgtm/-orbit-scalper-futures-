
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="ORBIT Data Loader",
    page_icon="📊",
    layout="wide",
)

API_URL = "https://fapi.binance.com/fapi/v1/klines"
COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]

st.title("📊 ORBIT Scalper Futures")
st.subheader("دریافت داده واقعی بازار Futures")
st.info(
    "این ابزار فقط داده عمومی بازار را دریافت می‌کند. "
    "کلید API و اتصال برای ثبت سفارش لازم نیست."
)

symbol = st.selectbox(
    "جفت‌ارز",
    ["BTCUSDT"],
    index=0,
)

days = st.selectbox(
    "بازه تاریخی",
    [7, 30, 60, 90],
    index=3,
    format_func=lambda x: f"{x} روز اخیر",
)

st.caption(
    "بازه زمانی: کندل ۵ دقیقه‌ای | بازار: Binance USDⓈ-M Futures"
)


def fetch_klines(days_back: int) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    start_ms = int(start.timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)

    all_rows = []
    cursor = start_ms
    progress = st.progress(0)
    status = st.empty()

    # حدود 12 کندل در ساعت، با 1500 کندل در هر درخواست
    expected = days_back * 24 * 12
    request_count = 0

    with requests.Session() as session:
        while cursor < now_ms:
            params = {
                "symbol": symbol,
                "interval": "5m",
                "startTime": cursor,
                "endTime": now_ms,
                "limit": 1500,
            }

            response = session.get(
                API_URL,
                params=params,
                timeout=30,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"Binance HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

            rows = response.json()

            if not rows:
                break

            all_rows.extend(rows)
            request_count += 1

            last_open_ms = int(rows[-1][0])
            next_cursor = last_open_ms + 5 * 60 * 1000

            if next_cursor <= cursor:
                break

            cursor = next_cursor

            progress.progress(
                min(len(all_rows) / expected, 1.0)
            )
            status.write(
                f"دریافت شده: {len(all_rows):,} کندل | "
                f"درخواست‌ها: {request_count}"
            )

            # مکث کوتاه برای کاهش فشار درخواست‌ها
            time.sleep(0.15)

    if not all_rows:
        raise RuntimeError(
            "هیچ داده‌ای دریافت نشد. اتصال یا دسترسی به API را بررسی کن."
        )

    df = pd.DataFrame(all_rows, columns=COLUMNS)

    df = df.drop_duplicates(subset=["timestamp"])
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], unit="ms", utc=True
    )

    # کندل هنوز بسته‌نشده وارد بک‌تست نشود
    df = df[df["close_time"] < int(now_ms)]

    # تبدیل قیمت‌ها و حجم‌ها به عدد
    for col in [
        "open", "high", "low", "close", "volume"
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )
    df = df.sort_values("timestamp").reset_index(drop=True)

    # قالب سازگار با ورودی موتور بک‌تست
    output = df[[
        "timestamp", "open", "high", "low", "close", "volume"
    ]].copy()

    output["timestamp"] = (
        output["timestamp"]
        .dt.strftime("%Y-%m-%d %H:%M:%S")
    )

    return output


if st.button("دریافت داده واقعی و ساخت CSV", type="primary"):
    try:
        with st.spinner("در حال دریافت کندل‌ها از Binance Futures..."):
            data = fetch_klines(days)

        csv_bytes = data.to_csv(index=False).encode("utf-8")

        st.success("دریافت داده‌ها تمام شد.")
        st.metric("تعداد کندل‌ها", f"{len(data):,}")

        st.write("نمونه داده‌های دریافت‌شده:")
        st.dataframe(data.head(20), use_container_width=True)

        st.write("آخرین کندل‌های دریافت‌شده:")
        st.dataframe(data.tail(10), use_container_width=True)

        st.download_button(
            label="⬇️ دانلود فایل CSV برای بک‌تست",
            data=csv_bytes,
            file_name=f"BTCUSDT_Futures_5m_{days}days.csv",
            mime="text/csv",
            type="primary",
        )

        st.caption(
            "فایل شامل timestamp, open, high, low, close, volume است. "
            "داده‌ها واقعی‌اند؛ اما نتیجه بک‌تست سود آینده را تضمین نمی‌کند."
        )

    except Exception as exc:
        st.error(f"دریافت داده ناموفق بود: {exc}")
        st.write(
            "اگر خطا مربوط به اتصال یا دسترسی بود، "
            "ممکن است API از محل اجرای برنامه قابل دسترسی نباشد."
        )
