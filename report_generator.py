"""
Модуль генерации отчётов о загруженности парковки.

Формирует аналитические отчёты за произвольный период в трёх форматах:
    - PDF   — документ с графиками, сводкой и таблицами (ReportLab);
    - XLSX  — книга Excel из четырёх листов с формулами и диаграммой (openpyxl);
    - CSV   — выгрузка замеров для внешней обработки.

Источник данных — таблицы Detections, SpaceStates и Spaces базы parking.db.
"""
import os
import csv
import io
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image as RLImage)

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter

import database

REPORT_DIR = "reports"
BRAND = colors.HexColor("#1f6f43")
BRAND_LIGHT = colors.HexColor("#e8f3ec")

os.makedirs(REPORT_DIR, exist_ok=True)


def _register_fonts():
    """Подключение шрифта с поддержкой кириллицы (с запасным вариантом)."""
    candidates = [
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("DejaVuSans", "C:/Windows/Fonts/DejaVuSans.ttf",
         "C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
        ("Arial", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ]
    for name, regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(name, regular))
            pdfmetrics.registerFont(TTFont(name + "-Bold", bold))
            return name
    return "Helvetica"


FONT = _register_fonts()
FONT_BOLD = FONT + "-Bold" if FONT != "Helvetica" else "Helvetica-Bold"


def _period_default(date_from=None, date_to=None):
    """Период по умолчанию — последние 7 суток."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.strptime(date_to, "%Y-%m-%d")
                     - timedelta(days=6)).strftime("%Y-%m-%d")
    return date_from, date_to


def _load_level(pct):
    """Текстовая интерпретация уровня загруженности."""
    if pct >= 90:
        return "критическая"
    if pct >= 70:
        return "высокая"
    if pct >= 40:
        return "умеренная"
    return "низкая"


# ---------- ПОСТРОЕНИЕ ГРАФИКОВ ДЛЯ PDF ----------
def _chart_hourly(hourly):
    """График средней загруженности по часам суток."""
    hours = [int(h["hour"]) for h in hourly]
    loads = [h["avg_load"] for h in hourly]
    fig, ax = plt.subplots(figsize=(7.2, 2.9), dpi=150)
    ax.plot(hours, loads, color="#1f6f43", linewidth=2, marker="o", markersize=4)
    ax.fill_between(hours, loads, color="#1f6f43", alpha=0.12)
    if loads:
        peak = max(range(len(loads)), key=lambda i: loads[i])
        ax.annotate(f"пик {loads[peak]}%", (hours[peak], loads[peak]),
                    textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8, color="#b3261e")
    ax.set_xlabel("Час суток", fontsize=9)
    ax.set_ylabel("Средняя загруженность, %", fontsize=9)
    ax.set_ylim(0, 105)
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _chart_daily(daily):
    """Столбчатая диаграмма средней загруженности по дням периода."""
    days = [d["day"][5:] for d in daily]
    loads = [d["avg_load"] for d in daily]
    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=150)
    bars = ax.bar(days, loads, color="#1f6f43", alpha=0.85, width=0.6)
    for b, v in zip(bars, loads):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}",
                ha="center", fontsize=7.5, color="#333333")
    ax.set_xlabel("Дата", fontsize=9)
    ax.set_ylabel("Средняя загруженность, %", fontsize=9)
    ax.set_ylim(0, 105)
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------- ГЕНЕРАЦИЯ PDF-ОТЧЁТА ----------
def generate_pdf(lot_id=1, date_from=None, date_to=None):
    """Аналитический PDF-отчёт о загруженности парковки за период."""
    date_from, date_to = _period_default(date_from, date_to)
    lot = database.get_lot(lot_id) or {"name": "Парковка", "location": "—",
                                       "total_spaces": 0}
    summary = database.get_period_summary(lot_id, date_from, date_to)
    hourly = database.get_hourly_period(lot_id, date_from, date_to)
    daily = database.get_daily_period(lot_id, date_from, date_to)
    usage = database.get_space_usage(lot_id, date_from, date_to)

    file_name = f"report_lot{lot_id}_{date_from}_{date_to}.pdf"
    file_path = os.path.join(REPORT_DIR, file_name)

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Отчёт о загруженности парковки")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontName=FONT_BOLD,
                        fontSize=17, textColor=BRAND, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontName=FONT,
                         fontSize=10, alignment=TA_CENTER,
                         textColor=colors.HexColor("#5f6b64"), spaceAfter=12)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=FONT_BOLD,
                        fontSize=12, textColor=BRAND, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["Normal"], fontName=FONT,
                          fontSize=9.5, leading=14)
    cap = ParagraphStyle("cap", parent=styles["Normal"], fontName=FONT,
                         fontSize=8.5, alignment=TA_CENTER,
                         textColor=colors.HexColor("#5f6b64"), spaceAfter=10)

    def table_style(ncols):
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("FONTNAME", (0, 1), (-1, -1), FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d6cd")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])

    story = []
    story.append(Paragraph("Отчёт о загруженности парковки", h1))
    story.append(Paragraph(
        f"{lot['name']} · {lot['location']} · период {date_from} — {date_to}", sub))

    # --- Сводные показатели ---
    story.append(Paragraph("1. Сводные показатели за период", h2))
    avg_load = summary.get("avg_load") or 0
    kpi = [
        ["Показатель", "Значение"],
        ["Всего парковочных мест", str(lot["total_spaces"])],
        ["Количество замеров", str(summary.get("measurements") or 0)],
        ["Средняя загруженность", f"{avg_load} %"],
        ["Максимальная загруженность", f"{summary.get('max_load') or 0} %"],
        ["Минимальная загруженность", f"{summary.get('min_load') or 0} %"],
        ["Среднее число свободных мест", str(summary.get("avg_free") or 0)],
        ["Уровень загруженности", _load_level(avg_load)],
    ]
    t = Table(kpi, colWidths=[95 * mm, 79 * mm])
    t.setStyle(table_style(2))
    story.append(t)
    story.append(Spacer(1, 6))

    # --- График по часам ---
    story.append(Paragraph("2. Динамика загруженности по часам суток", h2))
    story.append(RLImage(_chart_hourly(hourly), width=174 * mm, height=70 * mm))
    story.append(Paragraph("Рис. 1. Средняя загруженность парковки по часам суток", cap))

    if hourly:
        peak = max(hourly, key=lambda h: h["avg_load"])
        low = min(hourly, key=lambda h: h["avg_load"])
        story.append(Paragraph(
            f"Пиковая загруженность приходится на {int(peak['hour'])}:00 "
            f"и составляет {peak['avg_load']} %. Минимальная загруженность "
            f"зафиксирована в {int(low['hour'])}:00 — {low['avg_load']} %. "
            f"Рекомендуемое время посещения торгового центра — "
            f"до {int(peak['hour']) - 4}:00.", body))

    # --- График по дням ---
    story.append(Paragraph("3. Загруженность по дням периода", h2))
    story.append(RLImage(_chart_daily(daily), width=174 * mm, height=63 * mm))
    story.append(Paragraph("Рис. 2. Средняя загруженность по дням периода", cap))

    # --- Таблица по дням ---
    story.append(Paragraph("4. Детализация по дням", h2))
    rows = [["Дата", "Замеров", "Средняя загруженность, %", "Максимум, %"]]
    for d in daily:
        rows.append([d["day"], str(d["measurements"]),
                     f"{d['avg_load']}", f"{d['max_load']}"])
    t = Table(rows, colWidths=[44 * mm, 30 * mm, 55 * mm, 45 * mm])
    t.setStyle(table_style(4))
    story.append(t)

    # --- Таблица по местам ---
    story.append(Paragraph("5. Использование парковочных мест", h2))
    rows = [["Место", "Замеров", "Занято раз", "Частота занятости, %"]]
    for u in usage:
        rows.append([u["code"], str(u["total"]), str(u["occupied"]),
                     f"{u['usage_pct']}"])
    t = Table(rows, colWidths=[34 * mm, 40 * mm, 45 * mm, 55 * mm])
    t.setStyle(table_style(4))
    story.append(t)
    story.append(Spacer(1, 6))

    if usage:
        top = max(usage, key=lambda u: u["usage_pct"])
        rare = min(usage, key=lambda u: u["usage_pct"])
        story.append(Paragraph(
            f"Наиболее востребованное место — {top['code']} "
            f"(занято в {top['usage_pct']} % замеров), наименее востребованное — "
            f"{rare['code']} ({rare['usage_pct']} %). Неравномерность использования "
            f"мест позволяет скорректировать схему навигации на парковке.", body))

    # --- Выводы ---
    story.append(Paragraph("6. Выводы и рекомендации", h2))
    concl = (
        f"За период с {date_from} по {date_to} выполнено "
        f"{summary.get('measurements') or 0} автоматических замеров загруженности "
        f"парковки «{lot['name']}». Средний уровень загруженности составил "
        f"{avg_load} % и оценивается как {_load_level(avg_load)}. "
        f"В среднем в течение периода свободными оставались "
        f"{summary.get('avg_free') or 0} мест из {lot['total_spaces']}. "
        f"Выявленная суточная динамика позволяет администрации торгового центра "
        f"планировать работу парковочного пространства, а посетителям — выбирать "
        f"время посещения с наибольшей вероятностью найти свободное место."
    )
    story.append(Paragraph(concl, body))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Отчёт сформирован автоматически системой анализа загруженности парковки "
        f"{datetime.now().strftime('%d.%m.%Y %H:%M')}.", cap))

    doc.build(story)
    database.register_report(lot_id, "pdf", date_from, date_to, file_path)
    return file_path


# ---------- ГЕНЕРАЦИЯ EXCEL-ОТЧЁТА ----------
def generate_excel(lot_id=1, date_from=None, date_to=None):
    """Отчёт в формате Excel: сводка, замеры, часы, места + диаграмма."""
    date_from, date_to = _period_default(date_from, date_to)
    lot = database.get_lot(lot_id) or {"name": "Парковка", "location": "—",
                                       "total_spaces": 0}
    summary = database.get_period_summary(lot_id, date_from, date_to)
    detections = database.get_detections_period(lot_id, date_from, date_to)
    hourly = database.get_hourly_period(lot_id, date_from, date_to)
    usage = database.get_space_usage(lot_id, date_from, date_to)

    file_name = f"report_lot{lot_id}_{date_from}_{date_to}.xlsx"
    file_path = os.path.join(REPORT_DIR, file_name)

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F6F43")
    head_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(bold=True, size=14, color="1F6F43")
    thin = Side(style="thin", color="C7D6CD")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")

    def write_header(ws, headers, row=1):
        for col, name in enumerate(headers, start=1):
            c = ws.cell(row=row, column=col, value=name)
            c.fill, c.font, c.alignment, c.border = head_fill, head_font, center, border
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    def autosize(ws, widths):
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # Лист 1 — Сводка
    ws = wb.active
    ws.title = "Сводка"
    ws["A1"] = "Отчёт о загруженности парковки"
    ws["A1"].font = title_font
    ws["A2"] = f"{lot['name']} · {lot['location']}"
    ws["A3"] = f"Период: {date_from} — {date_to}"
    ws["A4"] = f"Сформирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    write_header(ws, ["Показатель", "Значение"], row=6)
    avg_load = summary.get("avg_load") or 0
    kpi = [
        ("Всего парковочных мест", lot["total_spaces"]),
        ("Количество замеров", summary.get("measurements") or 0),
        ("Средняя загруженность, %", avg_load),
        ("Максимальная загруженность, %", summary.get("max_load") or 0),
        ("Минимальная загруженность, %", summary.get("min_load") or 0),
        ("Среднее число свободных мест", summary.get("avg_free") or 0),
        ("Уровень загруженности", _load_level(avg_load)),
    ]
    for i, (k, v) in enumerate(kpi, start=7):
        ws.cell(row=i, column=1, value=k).border = border
        c = ws.cell(row=i, column=2, value=v)
        c.border, c.alignment = border, center
    autosize(ws, [36, 22])

    # Лист 2 — Замеры
    ws2 = wb.create_sheet("Замеры")
    write_header(ws2, ["Дата и время", "Занято", "Свободно", "Загруженность, %"])
    for i, d in enumerate(detections, start=2):
        ws2.cell(row=i, column=1, value=d["captured_at"]).border = border
        for col, key in enumerate(["occupied", "free", "occupancy_pct"], start=2):
            c = ws2.cell(row=i, column=col, value=d[key])
            c.border, c.alignment = border, center
    last = len(detections) + 1
    if detections:
        ws2.cell(row=last + 2, column=1, value="Среднее за период:").font = Font(bold=True)
        c = ws2.cell(row=last + 2, column=4, value=f"=ROUND(AVERAGE(D2:D{last}),1)")
        c.font, c.alignment = Font(bold=True), center
    autosize(ws2, [22, 12, 12, 18])

    # Лист 3 — По часам (с диаграммой)
    ws3 = wb.create_sheet("По часам")
    write_header(ws3, ["Час", "Средняя загруженность, %", "Замеров"])
    for i, h in enumerate(hourly, start=2):
        c = ws3.cell(row=i, column=1, value=f"{int(h['hour']):02d}:00")
        c.border, c.alignment = border, center
        for col, key in enumerate(["avg_load", "measurements"], start=2):
            c = ws3.cell(row=i, column=col, value=h[key])
            c.border, c.alignment = border, center
    if hourly:
        chart = LineChart()
        chart.title = "Средняя загруженность по часам суток"
        chart.y_axis.title = "Загруженность, %"
        chart.x_axis.title = "Час суток"
        chart.height, chart.width = 8, 18
        data = Reference(ws3, min_col=2, min_row=1, max_row=len(hourly) + 1)
        cats = Reference(ws3, min_col=1, min_row=2, max_row=len(hourly) + 1)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws3.add_chart(chart, "E2")
    autosize(ws3, [12, 26, 12])

    # Лист 4 — По местам
    ws4 = wb.create_sheet("По местам")
    write_header(ws4, ["Место", "Замеров", "Занято раз", "Частота занятости, %"])
    for i, u in enumerate(usage, start=2):
        c = ws4.cell(row=i, column=1, value=u["code"])
        c.border, c.alignment = border, center
        for col, key in enumerate(["total", "occupied", "usage_pct"], start=2):
            c = ws4.cell(row=i, column=col, value=u[key])
            c.border, c.alignment = border, center
    autosize(ws4, [12, 12, 14, 22])

    wb.save(file_path)
    database.register_report(lot_id, "xlsx", date_from, date_to, file_path)
    return file_path


# ---------- ГЕНЕРАЦИЯ CSV-ВЫГРУЗКИ ----------
def generate_csv(lot_id=1, date_from=None, date_to=None):
    """Выгрузка замеров загруженности в формате CSV."""
    date_from, date_to = _period_default(date_from, date_to)
    detections = database.get_detections_period(lot_id, date_from, date_to)

    file_name = f"report_lot{lot_id}_{date_from}_{date_to}.csv"
    file_path = os.path.join(REPORT_DIR, file_name)

    # utf-8-sig — корректное открытие кириллицы в Microsoft Excel
    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Дата и время", "Занято", "Свободно", "Загруженность, %"])
        for d in detections:
            writer.writerow([d["captured_at"], d["occupied"], d["free"],
                             d["occupancy_pct"]])

    database.register_report(lot_id, "csv", date_from, date_to, file_path)
    return file_path


GENERATORS = {"pdf": generate_pdf, "xlsx": generate_excel, "csv": generate_csv}


def generate(report_type, lot_id=1, date_from=None, date_to=None):
    """Единая точка входа: формирование отчёта требуемого формата."""
    if report_type not in GENERATORS:
        raise ValueError(f"Неизвестный формат отчёта: {report_type}")
    return GENERATORS[report_type](lot_id, date_from, date_to)


if __name__ == "__main__":
    database.create_tables()
    database.seed_demo_data()
    for fmt in ("pdf", "xlsx", "csv"):
        print(f"{fmt.upper()}: {generate(fmt)}")
