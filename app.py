"""
Серверная часть веб-приложения анализа загруженности парковки.
Принимает изображение, прогоняет через нейросеть, возвращает статистику
и формирует аналитические отчёты в форматах PDF, Excel и CSV.
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import os

import database
import report_generator

app = FastAPI(title="Parking Occupancy Analysis",
              description="Система анализа загруженности парковки ТЦ "
                          "с генерацией аналитических отчётов",
              version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RESULT_DIR = "static"
os.makedirs(RESULT_DIR, exist_ok=True)

# Координаты размеченных парковочных мест для камеры парковки №1
PARKING_SPACES = [{"code": f"A{i + 1}", "box": (40 + i * 95, 60, 125 + i * 95, 190)}
                  for i in range(8)]

# Соответствие формата отчёта MIME-типу и расширению файла
REPORT_MEDIA = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


# ---------- МОДЕЛИ ДАННЫХ ----------
class SpaceState(BaseModel):
    code: str
    status: str


class AnalyzeResult(BaseModel):
    detection_id: int
    total: int
    occupied: int
    free: int
    occupancy_pct: float
    states: List[SpaceState]
    result_image: str


class DetectionRecord(BaseModel):
    captured_at: str
    occupied: int
    free: int
    occupancy_pct: float


class LotInfo(BaseModel):
    id: int
    name: str
    location: str
    total_spaces: int


class ReportInfo(BaseModel):
    id: int
    report_type: str
    period_from: str
    period_to: str
    file_path: str
    created_at: str


# ---------- ЭНДПОИНТЫ АНАЛИЗА ----------
@app.post("/api/analyze", response_model=AnalyzeResult, tags=["Анализ"])
async def analyze_image(lot_id: int = 1, image: UploadFile = File(...)):
    """Анализ загруженности парковки по загруженному изображению."""
    import cv2
    import numpy as np
    from detector import ParkingAnalyzer

    data = await image.read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Не удалось прочитать изображение")

    analyzer = ParkingAnalyzer("yolov8n.pt", PARKING_SPACES)
    analysis = analyzer.analyze(img)

    result_path = os.path.join(RESULT_DIR, "result.jpg")
    cv2.imwrite(result_path, analyzer.annotate(img, analysis))

    detection_id = database.save_detection(lot_id, analysis, result_path)
    return {
        "detection_id": detection_id,
        "total": analysis["total"],
        "occupied": analysis["occupied"],
        "free": analysis["free"],
        "occupancy_pct": analysis["occupancy_pct"],
        "states": analysis["states"],
        "result_image": "/api/result/" + str(detection_id),
    }


@app.get("/api/result/{detection_id}", tags=["Анализ"])
def get_result_image(detection_id: int):
    """Получение обработанного изображения с разметкой мест."""
    path = os.path.join(RESULT_DIR, "result.jpg")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Изображение не найдено")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/lots", response_model=List[LotInfo], tags=["Справочники"])
def get_lots():
    """Список парковок в системе."""
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM ParkingLots").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/history", response_model=List[DetectionRecord], tags=["Аналитика"])
def get_history(lot_id: int = 1):
    """История замеров загруженности парковки."""
    return database.get_history(lot_id)


@app.get("/api/stats/hourly", tags=["Аналитика"])
def get_hourly(lot_id: int = 1):
    """Средняя загруженность парковки по часам суток."""
    return database.get_hourly_load(lot_id)


# ---------- ЭНДПОИНТЫ ГЕНЕРАЦИИ ОТЧЁТОВ ----------
@app.get("/api/report/preview", tags=["Отчёты"])
def report_preview(lot_id: int = 1,
                   date_from: Optional[str] = Query(None, description="ГГГГ-ММ-ДД"),
                   date_to: Optional[str] = Query(None, description="ГГГГ-ММ-ДД")):
    """Предварительный просмотр содержимого отчёта без выгрузки файла."""
    date_from, date_to = report_generator._period_default(date_from, date_to)
    summary = database.get_period_summary(lot_id, date_from, date_to)
    if not summary.get("measurements"):
        raise HTTPException(status_code=404,
                            detail="За указанный период данные отсутствуют")
    return {
        "lot": database.get_lot(lot_id),
        "period": {"from": date_from, "to": date_to},
        "summary": summary,
        "hourly": database.get_hourly_period(lot_id, date_from, date_to),
        "daily": database.get_daily_period(lot_id, date_from, date_to),
        "spaces": database.get_space_usage(lot_id, date_from, date_to),
    }


@app.get("/api/report/{report_type}", tags=["Отчёты"])
def download_report(report_type: str, lot_id: int = 1,
                    date_from: Optional[str] = Query(None, description="ГГГГ-ММ-ДД"),
                    date_to: Optional[str] = Query(None, description="ГГГГ-ММ-ДД")):
    """Формирование и выгрузка отчёта в формате pdf, xlsx или csv."""
    if report_type not in REPORT_MEDIA:
        raise HTTPException(status_code=400,
                            detail="Допустимые форматы: pdf, xlsx, csv")
    date_from, date_to = report_generator._period_default(date_from, date_to)
    if not database.get_period_summary(lot_id, date_from, date_to).get("measurements"):
        raise HTTPException(status_code=404,
                            detail="За указанный период данные отсутствуют")

    file_path = report_generator.generate(report_type, lot_id, date_from, date_to)
    return FileResponse(file_path,
                        media_type=REPORT_MEDIA[report_type],
                        filename=os.path.basename(file_path))


@app.get("/api/reports", response_model=List[ReportInfo], tags=["Отчёты"])
def list_reports(lot_id: int = 1):
    """Журнал ранее сформированных отчётов."""
    return database.get_reports(lot_id)


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    database.create_tables()
    database.seed_demo_data()
    print("Запуск Parking Occupancy API...")
    print("Веб-интерфейс:  http://localhost:8000")
    print("Документация:   http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
