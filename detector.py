"""
Модуль анализа загруженности парковки ТЦ на основе нейросети YOLOv8.
Определяет занятость размеченных парковочных мест по изображению с камеры.
"""
import cv2
import numpy as np
from ultralytics import YOLO

# Классы транспортных средств из набора COCO, занимающих парковочное место
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Порог перекрытия площади места рамкой автомобиля для статуса «занято»
OCCUPANCY_THRESHOLD = 0.30
# Порог уверенности детектора
CONF_THRESHOLD = 0.35


class ParkingAnalyzer:
    """Анализатор загруженности парковки на основе детекции транспорта."""

    def __init__(self, model_path="yolov8n.pt", spaces=None):
        # Загрузка предобученной модели YOLOv8 (детекция объектов)
        self.model = YOLO(model_path)
        # spaces — список парковочных мест вида {"code": "A1", "box": (x1, y1, x2, y2)}
        self.spaces = spaces or []

    def detect_vehicles(self, image):
        """Прогон изображения через нейросеть и возврат рамок транспорта."""
        results = self.model(image, conf=CONF_THRESHOLD, verbose=False)[0]
        boxes = []
        for box in results.boxes:
            cls = int(box.cls[0])
            if cls in VEHICLE_CLASSES:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                boxes.append({
                    "bbox": (x1, y1, x2, y2),
                    "cls": VEHICLE_CLASSES[cls],
                    "conf": float(box.conf[0]),
                })
        return boxes

    @staticmethod
    def _overlap_ratio(space_box, car_box):
        """Доля площади парковочного места, перекрытая рамкой автомобиля."""
        ax1, ay1, ax2, ay2 = space_box
        bx1, by1, bx2, by2 = car_box
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        space_area = (ax2 - ax1) * (ay2 - ay1)
        return inter / space_area if space_area > 0 else 0.0

    def analyze(self, image):
        """Полный анализ: статус каждого места и сводная статистика."""
        cars = self.detect_vehicles(image)
        states = []
        for sp in self.spaces:
            occupied = any(
                self._overlap_ratio(sp["box"], car["bbox"]) >= OCCUPANCY_THRESHOLD
                for car in cars
            )
            states.append({
                "code": sp["code"],
                "box": sp["box"],
                "status": "occupied" if occupied else "free",
            })
        total = len(states)
        occupied = sum(1 for s in states if s["status"] == "occupied")
        free = total - occupied
        pct = round(100 * occupied / total, 1) if total else 0.0
        return {
            "cars": cars, "states": states, "total": total,
            "occupied": occupied, "free": free, "occupancy_pct": pct,
        }

    def annotate(self, image, analysis):
        """Отрисовка результата: статусы мест (зелёный — свободно, красный — занято)."""
        img = image.copy()
        for s in analysis["states"]:
            x1, y1, x2, y2 = s["box"]
            color = (0, 0, 255) if s["status"] == "occupied" else (0, 170, 0)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(img, s["code"], (x1 + 4, y1 + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        summary = (f"Occupied: {analysis['occupied']}/{analysis['total']} "
                   f"({analysis['occupancy_pct']}%)")
        cv2.putText(img, summary, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
        return img


if __name__ == "__main__":
    # Координаты размеченных парковочных мест (для конкретной камеры)
    spaces = [{"code": f"A{i + 1}", "box": (40 + i * 95, 60, 125 + i * 95, 190)}
              for i in range(8)]
    analyzer = ParkingAnalyzer("yolov8n.pt", spaces)
    image = cv2.imread("parking.jpg")
    result = analyzer.analyze(image)
    print(f"Свободно мест: {result['free']} из {result['total']} "
          f"(загруженность {result['occupancy_pct']}%)")
    cv2.imwrite("result.jpg", analyzer.annotate(image, result))
