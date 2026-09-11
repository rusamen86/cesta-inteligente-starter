from __future__ import annotations

import os
from html import escape
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from cesta_inteligente.queries import (
    comparable_price_comparisons,
    product_history,
    receipt_history,
)


DB_PATH = Path(os.environ.get("CESTA_DB_PATH", "data/cesta.db"))
HOUSEHOLD_LABEL = os.environ.get("CESTA_HOUSEHOLD_LABEL", "Mi hogar")

TODAY = pd.Timestamp(date.today())
ACCENT = "#0f7b62"
WARM = "#e1843b"


def euro(value: float | int) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def period_start(period: str) -> pd.Timestamp | None:
    if period == "Este mes":
        return TODAY.replace(day=1)
    if period == "Últimos 3 meses":
        return (TODAY - pd.DateOffset(months=2)).replace(day=1)
    if period == "Este año":
        return TODAY.replace(month=1, day=1)
    return None


def filter_dates(frame: pd.DataFrame, start: pd.Timestamp | None) -> pd.DataFrame:
    if frame.empty or "purchase_date" not in frame:
        return frame.copy()
    result = frame.copy()
    result["purchase_date"] = pd.to_datetime(result["purchase_date"], errors="coerce")
    if start is not None:
        result = result[result["purchase_date"] >= start]
    return result


def empty_panel(*, has_other_periods: bool) -> None:
    with st.container(border=True):
        if has_other_periods:
            st.subheader("Sin compras en este periodo")
            st.write("Prueba otro periodo para consultar el histórico ya registrado.")
        else:
            st.subheader("Todo listo para el primer ticket")
            st.write(
                "Aún no hay compras válidas que mostrar. Envía una foto del ticket al grupo Cesta y "
                "el panel se actualizará automáticamente cuando quede registrado."
            )
            st.caption("Los tickets pendientes de revisión aparecerán en Calidad antes de entrar en los cálculos.")


st.set_page_config(page_title="Cesta Inteligente", page_icon="🧺", layout="wide")
st.markdown(
    """
    <style>
    :root { --ink:#182019; --muted:#687168; --paper:#fffdf8; --line:#dddcd3; --accent:#0f7b62; }
    .stApp { background: #f6f7f2; color: var(--ink); }
    header[data-testid="stHeader"] { height: 0; min-height: 0; }
    .block-container { max-width: 1180px; padding-top: 2.5rem; padding-bottom: 4rem; }
    [data-testid="stAppDeployButton"], [data-testid="stMainMenu"] { display: none; }
    h1, h2, h3 { color: var(--ink); letter-spacing: -0.025em; }
    h1 { font-size: clamp(2rem, 5vw, 3.4rem) !important; margin-bottom: .15rem !important; }
    [data-testid="stMetric"], [data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--paper); border: 1px solid var(--line); border-radius: 14px;
        box-shadow: 0 8px 24px rgba(24,32,25,.035);
    }
    [data-testid="stMetric"] { padding: 1rem 1.1rem; min-height: 112px; }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stMetricValue"] { color: var(--ink); font-weight: 720; }
    div[data-baseweb="select"] > div { background: var(--paper); border-color: var(--line); }
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    .dashboard-kicker { color: var(--accent); font-size: .78rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
    .dashboard-subtitle { color: var(--muted); margin: -.35rem 0 1.2rem; }
    @media (max-width: 640px) {
        .block-container { padding: 2rem .85rem 2.5rem; }
        [data-testid="stMetric"] { min-height: 96px; padding: .8rem .9rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(f'<div class="dashboard-kicker">{escape(HOUSEHOLD_LABEL)}</div>', unsafe_allow_html=True)
st.title("Cesta Inteligente")
st.markdown(
    '<div class="dashboard-subtitle">Qué gastamos, dónde compramos y cómo evoluciona nuestra cesta.</div>',
    unsafe_allow_html=True,
)

period = st.selectbox("Periodo", ["Este mes", "Últimos 3 meses", "Este año", "Todo"], index=2)
start = period_start(period)

if DB_PATH.exists():
    history = pd.DataFrame(receipt_history(DB_PATH))
    products = pd.DataFrame(product_history(DB_PATH))
    comparisons = pd.DataFrame(comparable_price_comparisons(DB_PATH))
else:
    history = pd.DataFrame()
    products = pd.DataFrame()
    comparisons = pd.DataFrame()

valid = history[history["validation_status"] == "valid"].copy() if not history.empty else history.copy()
valid = filter_dates(valid, start)
pending = history[history["validation_status"] == "needs_review"] if not history.empty else history.copy()
products = filter_dates(products, start)
comparisons = filter_dates(comparisons, start)

total_cents = int(valid["amount_paid_cents"].sum()) if not valid.empty else 0
ticket_count = len(valid)
article_count = int(valid["article_count"].fillna(0).sum()) if not valid.empty else 0
savings_cents = int(valid["savings_cents"].fillna(0).sum()) if not valid.empty else 0
average_cents = total_cents / ticket_count if ticket_count else 0

first_row = st.columns(3)
first_row[0].metric("Gasto", euro(total_cents / 100))
first_row[1].metric("Tickets", f"{ticket_count}")
first_row[2].metric("Cesta media", euro(average_cents / 100))
second_row = st.columns(3)
second_row[0].metric("Artículos", f"{article_count}")
second_row[1].metric("Ahorro aplicado", euro(savings_cents / 100))
second_row[2].metric("Pendientes", f"{len(pending)}", help="Tickets que aún necesitan una revisión")

if valid.empty:
    has_other_periods = not history.empty and (history["validation_status"] == "valid").any()
    empty_panel(has_other_periods=has_other_periods)
else:
    valid["month"] = valid["purchase_date"].dt.to_period("M").dt.to_timestamp()
    monthly = valid.groupby("month", as_index=False).agg(gasto_cents=("amount_paid_cents", "sum"), tickets=("id", "count"))
    monthly["gasto_eur"] = monthly["gasto_cents"] / 100

    stores = valid.groupby("supermarket", as_index=False).agg(
        gasto_cents=("amount_paid_cents", "sum"), tickets=("id", "count")
    )
    stores["gasto_eur"] = stores["gasto_cents"] / 100

    chart_left, chart_right = st.columns([1.35, 1])
    with chart_left.container(border=True):
        st.subheader("Evolución mensual")
        evolution_base = alt.Chart(monthly).encode(
            x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0)),
            y=alt.Y("gasto_eur:Q", title="Gasto (€)", axis=alt.Axis(gridColor="#e7e7df")),
            tooltip=[
                alt.Tooltip("month:T", title="Mes", format="%B %Y"),
                alt.Tooltip("gasto_eur:Q", title="Gasto", format=".2f"),
            ],
        )
        evolution = (
            evolution_base.mark_area(color=ACCENT, opacity=0.1)
            + evolution_base.mark_line(color=ACCENT, strokeWidth=3)
            + evolution_base.mark_point(color=ACCENT, filled=True, size=80)
        ).properties(height=260)
        st.altair_chart(evolution, width="stretch")

    with chart_right.container(border=True):
        st.subheader("Supermercados")
        store_chart = (
            alt.Chart(stores.sort_values("gasto_eur"))
            .mark_bar(color=WARM, cornerRadiusEnd=5)
            .encode(
                x=alt.X("gasto_eur:Q", title="Gasto (€)", axis=alt.Axis(gridColor="#e7e7df")),
                y=alt.Y("supermarket:N", title=None, sort="-x"),
                tooltip=["supermarket:N", alt.Tooltip("gasto_eur:Q", title="Gasto", format=".2f"), "tickets:Q"],
            )
            .properties(height=260)
        )
        st.altair_chart(store_chart, width="stretch")

    detail_left, detail_right = st.columns(2)
    with detail_left.container(border=True):
        st.subheader("Categorías")
        if products.empty:
            st.caption("Aún no hay artículos normalizados para este periodo.")
        else:
            categories = products.groupby("category", as_index=False).agg(gasto_cents=("line_final_cents", "sum"))
            categories["gasto_eur"] = categories["gasto_cents"] / 100
            category_chart = (
                alt.Chart(categories.nlargest(8, "gasto_eur").sort_values("gasto_eur"))
                .mark_bar(color=ACCENT, cornerRadiusEnd=5)
                .encode(
                    x=alt.X("gasto_eur:Q", title="Gasto (€)", axis=alt.Axis(gridColor="#e7e7df")),
                    y=alt.Y("category:N", title=None, sort="-x"),
                    tooltip=["category:N", alt.Tooltip("gasto_eur:Q", title="Gasto", format=".2f")],
                )
                .properties(height=290)
            )
            st.altair_chart(category_chart, width="stretch")

    with detail_right.container(border=True):
        st.subheader("Productos frecuentes")
        if products.empty:
            st.caption("Aún no hay artículos normalizados para este periodo.")
        else:
            frequent = (
                products.groupby(["comparable_product", "category"], as_index=False)
                .agg(compras=("product_stable_id", "count"), gasto_cents=("line_final_cents", "sum"))
                .sort_values(["compras", "gasto_cents"], ascending=False)
                .head(8)
            )
            frequent["gasto_eur"] = frequent["gasto_cents"] / 100
            frequent = frequent.rename(columns={"comparable_product": "Producto", "category": "Categoría", "compras": "Compras", "gasto_eur": "Gasto"})
            st.dataframe(
                frequent[["Producto", "Categoría", "Compras", "Gasto"]],
                hide_index=True,
                width="stretch",
                column_config={"Gasto": st.column_config.NumberColumn(format="%.2f €")},
            )

    st.subheader("Comparador entre cadenas")
    if comparisons.empty:
        st.caption("Necesita el mismo producto comparable, en la misma unidad, comprado en dos supermercados distintos.")
    else:
        comparison_table = comparisons.sort_values("purchase_date", ascending=False).head(16).copy()
        comparison_table["Precio normalizado"] = comparison_table["normalized_price_cents"] / 100
        comparison_table = comparison_table.rename(
            columns={"purchase_date": "Fecha", "supermarket": "Supermercado", "comparable_product": "Producto", "normalized_unit": "Unidad"}
        )
        st.dataframe(
            comparison_table[["Fecha", "Supermercado", "Producto", "Unidad", "Precio normalizado"]],
            hide_index=True,
            width="stretch",
            column_config={
                "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Precio normalizado": st.column_config.NumberColumn(format="%.2f €"),
            },
        )

    st.subheader("Últimos tickets")
    recent = valid.sort_values("purchase_date", ascending=False).head(10).copy()
    recent["Total"] = recent["amount_paid_cents"] / 100
    recent = recent.rename(
        columns={"purchase_date": "Fecha", "supermarket": "Supermercado", "store_name": "Tienda", "article_count": "Artículos"}
    )
    st.dataframe(
        recent[["Fecha", "Supermercado", "Tienda", "Artículos", "Total"]],
        hide_index=True,
        width="stretch",
        column_config={"Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"), "Total": st.column_config.NumberColumn(format="%.2f €")},
    )

st.caption("Solo se incluyen tickets válidos en los cálculos · Dashboard privado en este Mac")
