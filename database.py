"""
Модуль работы с базой данных истории замеров загруженности парковки.
Хранит парковки, размеченные места, замеры и состояние мест в каждом замере.
"""
import sqlite3
import random
from datetime import datetime, timedelta

DB_NAME = "parking.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ParkingLots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            total_spaces INTEGER NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Spaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            x1 INTEGER NOT NULL,
            y1 INTEGER NOT NULL,
            x2 INTEGER NOT NULL,
            y2 INTEGER NOT NULL,
            FOREIGN KEY (lot_id) REFERENCES ParkingLots(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            occupied INTEGER NOT NULL,
            free INTEGER NOT NULL,
            occupancy_pct REAL NOT NULL,
            image_path TEXT,
            FOREIGN KEY (lot_id) REFERENCES ParkingLots(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS SpaceStates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_id INTEGER NOT NULL,
            space_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (detection_id) REFERENCES Detections(id),
            FOREIGN KEY (space_id) REFERENCES Spaces(id)
        )
    ''')

    # Журнал сформированных отчётов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL,
            report_type TEXT NOT NULL,
            period_from TEXT NOT NULL,
            period_to TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lot_id) REFERENCES ParkingLots(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Таблицы созданы")


def save_detection(lot_id, analysis, image_path=None):
    """Сохранение результата анализа: замер и состояние каждого места."""
    conn = get_db()
    cursor = conn.cursor()
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO Detections (lot_id, captured_at, occupied, free, occupancy_pct, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (lot_id, captured_at, analysis["occupied"], analysis["free"],
          analysis["occupancy_pct"], image_path))
    detection_id = cursor.lastrowid

    for state in analysis["states"]:
        space = cursor.execute(
            "SELECT id FROM Spaces WHERE lot_id = ? AND code = ?",
            (lot_id, state["code"])).fetchone()
        if space:
            cursor.execute('''
                INSERT INTO SpaceStates (detection_id, space_id, status)
                VALUES (?, ?, ?)
            ''', (detection_id, space["id"], state["status"]))

    conn.commit()
    conn.close()
    return detection_id


def get_history(lot_id, limit=50):
    """История замеров загруженности по парковке."""
    conn = get_db()
    rows = conn.execute('''
        SELECT captured_at, occupied, free, occupancy_pct
        FROM Detections WHERE lot_id = ?
        ORDER BY captured_at DESC LIMIT ?
    ''', (lot_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_hourly_load(lot_id):
    """Средняя загруженность парковки по часам суток."""
    conn = get_db()
    rows = conn.execute('''
        SELECT strftime('%H', captured_at) AS hour,
               ROUND(AVG(occupancy_pct), 1) AS avg_load
        FROM Detections WHERE lot_id = ?
        GROUP BY hour ORDER BY hour
    ''', (lot_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- ВЫБОРКИ ДЛЯ ГЕНЕРАЦИИ ОТЧЁТОВ ----------
def get_lot(lot_id):
    """Информация о парковке."""
    conn = get_db()
    row = conn.execute("SELECT * FROM ParkingLots WHERE id = ?", (lot_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_detections_period(lot_id, date_from, date_to):
    """Замеры парковки за период (включительно)."""
    conn = get_db()
    rows = conn.execute('''
        SELECT id, captured_at, occupied, free, occupancy_pct
        FROM Detections
        WHERE lot_id = ? AND date(captured_at) BETWEEN date(?) AND date(?)
        ORDER BY captured_at
    ''', (lot_id, date_from, date_to)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_period_summary(lot_id, date_from, date_to):
    """Сводные показатели загруженности за период."""
    conn = get_db()
    row = conn.execute('''
        SELECT COUNT(*) AS measurements,
               ROUND(AVG(occupancy_pct), 1) AS avg_load,
               MAX(occupancy_pct) AS max_load,
               MIN(occupancy_pct) AS min_load,
               ROUND(AVG(free), 1) AS avg_free
        FROM Detections
        WHERE lot_id = ? AND date(captured_at) BETWEEN date(?) AND date(?)
    ''', (lot_id, date_from, date_to)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_hourly_period(lot_id, date_from, date_to):
    """Средняя загруженность по часам суток за период."""
    conn = get_db()
    rows = conn.execute('''
        SELECT strftime('%H', captured_at) AS hour,
               ROUND(AVG(occupancy_pct), 1) AS avg_load,
               COUNT(*) AS measurements
        FROM Detections
        WHERE lot_id = ? AND date(captured_at) BETWEEN date(?) AND date(?)
        GROUP BY hour ORDER BY hour
    ''', (lot_id, date_from, date_to)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_period(lot_id, date_from, date_to):
    """Средняя загруженность по дням за период."""
    conn = get_db()
    rows = conn.execute('''
        SELECT date(captured_at) AS day,
               ROUND(AVG(occupancy_pct), 1) AS avg_load,
               MAX(occupancy_pct) AS max_load,
               COUNT(*) AS measurements
        FROM Detections
        WHERE lot_id = ? AND date(captured_at) BETWEEN date(?) AND date(?)
        GROUP BY day ORDER BY day
    ''', (lot_id, date_from, date_to)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_space_usage(lot_id, date_from, date_to):
    """Частота занятости каждого парковочного места за период."""
    conn = get_db()
    rows = conn.execute('''
        SELECT sp.code AS code,
               COUNT(ss.id) AS total,
               SUM(CASE WHEN ss.status = 'occupied' THEN 1 ELSE 0 END) AS occupied
        FROM SpaceStates ss
        JOIN Spaces sp ON sp.id = ss.space_id
        JOIN Detections d ON d.id = ss.detection_id
        WHERE d.lot_id = ? AND date(d.captured_at) BETWEEN date(?) AND date(?)
        GROUP BY sp.code ORDER BY sp.code
    ''', (lot_id, date_from, date_to)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["usage_pct"] = round(100 * d["occupied"] / d["total"], 1) if d["total"] else 0.0
        result.append(d)
    return result


def register_report(lot_id, report_type, date_from, date_to, file_path):
    """Регистрация сформированного отчёта в журнале."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Reports (lot_id, report_type, period_from, period_to, file_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (lot_id, report_type, date_from, date_to, file_path,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return report_id


def get_reports(lot_id, limit=20):
    """Журнал сформированных отчётов."""
    conn = get_db()
    rows = conn.execute('''
        SELECT id, report_type, period_from, period_to, file_path, created_at
        FROM Reports WHERE lot_id = ?
        ORDER BY created_at DESC LIMIT ?
    ''', (lot_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def seed_demo_data(days=14, end_date=None):
    """Наполнение тестовыми данными: парковка, места и история замеров."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO ParkingLots (id, name, location, total_spaces) "
                   "VALUES (1, 'Парковка ТЦ Мега', 'Москва, Калужское ш.', 8)")
    spaces = [(i + 1, 1, f"A{i + 1}", 40 + i * 95, 60, 125 + i * 95, 190)
              for i in range(8)]
    cursor.executemany(
        "INSERT OR IGNORE INTO Spaces (id, lot_id, code, x1, y1, x2, y2) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", spaces)

    # Профиль суточной загруженности парковки ТЦ (часы работы 8:00–22:00)
    profile = {8: 25, 9: 33, 10: 50, 11: 54, 12: 75, 13: 66, 14: 67,
               15: 75, 16: 83, 17: 86, 18: 96, 19: 87, 20: 75, 21: 54, 22: 42}

    rnd = random.Random(42)
    existing = cursor.execute("SELECT COUNT(*) FROM Detections").fetchone()[0]
    if existing == 0:
        last_day = (datetime.strptime(end_date, "%Y-%m-%d")
                    if end_date else datetime.now())
        start = last_day - timedelta(days=days - 1)
        for d in range(days):
            day = start + timedelta(days=d)
            # По выходным загруженность выше
            weekend = 1.12 if day.weekday() >= 5 else 1.0
            for hour, base in profile.items():
                pct = min(100.0, max(0.0, base * weekend + rnd.uniform(-6, 6)))
                occupied = round(8 * pct / 100)
                free = 8 - occupied
                pct = round(100 * occupied / 8, 1)
                captured_at = day.replace(hour=hour, minute=0,
                                          second=0, microsecond=0)
                cursor.execute('''
                    INSERT INTO Detections (lot_id, captured_at, occupied, free,
                                            occupancy_pct, image_path)
                    VALUES (1, ?, ?, ?, ?, ?)
                ''', (captured_at.strftime("%Y-%m-%d %H:%M:%S"),
                      occupied, free, pct, "static/result.jpg"))
                detection_id = cursor.lastrowid
                order = list(range(1, 9))
                rnd.shuffle(order)
                busy = set(order[:occupied])
                for sid in range(1, 9):
                    cursor.execute(
                        "INSERT INTO SpaceStates (detection_id, space_id, status) "
                        "VALUES (?, ?, ?)",
                        (detection_id, sid, "occupied" if sid in busy else "free"))

    conn.commit()
    conn.close()
    print("Демо-данные добавлены")


if __name__ == "__main__":
    create_tables()
    seed_demo_data()
