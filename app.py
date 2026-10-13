
import streamlit as st
import pandas as pd

from engine import run_backtest


st.set_page_config(
    page_title="ORBIT Scalper Futures",
    page_icon="🌌",
    layout="wide",
)

st.title("🌌 ORBIT Scalper Futures V1")
st.caption("موتور بک‌تست محلی برای فیوچرز ارز دیجیتال")

st.warning(
    "این نسخه فقط شبیه‌سازی تاریخی انجام می‌دهد. "
    "به صرافی متصل نمی‌شود و هیچ سفارش واقعی ارسال نمی‌کند."
)

with st.sidebar:
    st.header("تنظیمات ریسک")

    initial_balance = st.number_input(
        "سرمایه اولیه (USDT)",
        min_value=100.0,
        value=1000.0,
        step=100.0,
    )

    risk_pct = st.slider(
        "ریسک هر معامله (%)",
        min_value=0.1,
        max_value=1.0,
        value=1.0,
        step=0.1,
    )

    risk_cap = st.slider(
        "سقف ریسک مجموع موقعیت‌ها (%)",
        min_value=1.0,
        max_value=10.0,
        value=10.0,
        step=0.5,
    )

    leverage = st.selectbox(
        "لوریج شبیه‌سازی",
        options=[1.0, 2.0, 3.0],
        index=2,
    )

    reward_risk = st.selectbox(
        "نسبت سود به ریسک",
        options=[1.0, 1.5, 2.0, 2.5, 3.0],
        index=2,
    )

    atr_mult = st.slider(
        "ضریب ATR برای حد ضرر",
        min_value=0.5,
        max_value=3.0,
        value=1.5,
        step=0.1,
    )

    fee_pct = st.number_input(
        "کارمزد هر سمت (%)",
        min_value=0.0,
        max_value=0.3,
        value=0.05,
        step=0.01,
    )

    slippage_pct = st.number_input(
        "لغزش فرضی هر سمت (%)",
        min_value=0.0,
        max_value=0.5,
        value=0.02,
        step=0.01,
    )

st.subheader("۱. بارگذاری داده‌های تاریخی")

st.write(
    "فایل CSV باید ستون‌های timestamp, open, high, low, close "
    "داشته باشد. ستون volume اختیاری است. "
    "داده‌ها باید کندل‌های ۵ دقیقه‌ای باشند."
)

uploads = st.file_uploader(
    "فایل‌های CSV نمادها را انتخاب کن",
    type=["csv"],
    accept_multiple_files=True,
)

data_by_symbol = {}

if uploads:
    for file in uploads:
        symbol = file.name.rsplit(".", 1)[0].upper()

        try:
            frame = pd.read_csv(file)
            data_by_symbol[symbol] = frame

            st.success(
                f"{symbol}: {len(frame)} ردیف بارگذاری شد."
            )

        except Exception as exc:
            st.error(f"خطا در فایل {file.name}: {exc}")

st.subheader("۲. اجرای بک‌تست")

if st.button(
    "اجرای شبیه‌سازی",
    type="primary",
    disabled=not bool(data_by_symbol),
):
    try:
        summary, trades, equity = run_backtest(
            data_by_symbol=data_by_symbol,
            initial_balance=initial_balance,
            risk_pct=risk_pct,
            max_open_risk_pct=risk_cap,
            leverage=leverage,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            reward_risk=reward_risk,
            atr_mult=atr_mult,
        )

        st.subheader("گزارش عملکرد")

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "بازده خالص",
            f"{summary['net_return_pct']:.2f}%",
        )

        c2.metric(
            "معاملات بسته‌شده",
            summary["closed_trades"],
        )

        c3.metric(
            "نرخ برد",
            f"{summary['win_rate_pct']:.1f}%",
        )

        c4.metric(
            "افت سرمایه بیشینه",
            f"{summary['max_drawdown_pct']:.2f}%",
        )

        c5, c6, c7 = st.columns(3)

        c5.metric(
            "ارزش نهایی تقریبی",
            f"${summary['final_equity']:.2f}",
        )

        pf = summary["profit_factor"]

        c6.metric(
            "Profit Factor",
            "بی‌نهایت" if pf == float("inf") else f"{pf:.2f}",
        )

        c7.metric(
            "موقعیت‌های باز در پایان",
            summary["open_positions_at_end"],
        )

        st.info(summary["note"])

        if not equity.empty:
            st.subheader("منحنی ارزش حساب")

            st.line_chart(
                equity.set_index("timestamp")[["equity"]]
            )

        if not trades.empty:
            st.subheader("تاریخچه معاملات")

            st.dataframe(
                trades,
                use_container_width=True,
            )

            st.download_button(
                "دانلود گزارش معاملات CSV",
                data=trades.to_csv(index=False).encode("utf-8"),
                file_name="orbit_trades.csv",
                mime="text/csv",
            )
        else:
            st.info(
                "در این داده‌ها معامله بسته‌شده‌ای ثبت نشد. "
                "بازه داده و شرایط سیگنال را بررسی کن."
            )

    except Exception as exc:
        st.error(f"اجرای بک‌تست ناموفق بود: {exc}")

st.subheader("۳. قالب داده")

sample = pd.DataFrame(
    {
        "timestamp": [
            "2026-01-01 00:00:00+00:00",
            "2026-01-01 00:05:00+00:00",
        ],
        "open": [90000, 90020],
        "high": [90100, 90080],
        "low": [89900, 89950],
        "close": [90020, 90000],
        "volume": [10, 12],
    }
)

st.dataframe(sample, use_container_width=True)

st.caption(
    "داده نمونه بالا فقط برای نمایش قالب است، نه داده واقعی بازار. "
    "نتایج بک‌تست تضمین‌کننده سود آینده نیستند."
)
