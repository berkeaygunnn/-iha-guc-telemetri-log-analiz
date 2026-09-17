"""Backend'in ürettiği JSON'daki batarya/motor sözlüklerini numpy dizilerine
çeviren ince bir adaptör katmanı. Tk'ye hiç bağımlı değil (Tk'siz, hızlı test
edilebilsin diye) - anomaly_* modülleri bu katmanın üzerine kurulacak.

rpm/has_rpm_data alanları backend'e Aşama 1'de eklendi; eski (bu alanları
içermeyen) bir JSON okunursa .get() ile güvenli varsayılana düşülür.
"""

import numpy as np


def battery_series(battery: dict) -> dict:
    """Backend JSON'daki tek bir batarya sözlüğünü numpy dizilerine çevirir.
    has_current_data bayrağı olduğu gibi taşınır - main.py'deki yorumla AYNI
    anlam, iki yerde farklı yorumlanmasın diye burada ham float dönüşümü
    dışında hiçbir karar verilmez."""
    return {
        "id": battery["id"],
        "t": np.asarray(battery["time_s"], dtype=float),
        "voltage_v": np.asarray(battery["voltage_v"], dtype=float),
        "current_a": np.asarray(battery["current_a"], dtype=float),
        "has_current_data": battery.get("has_current_data", True),
    }


def motor_series(motor: dict) -> dict:
    """battery_series'in motor eşleniği. rpm/has_rpm_data alanları JSON'da
    yoksa (Aşama 1 öncesi eski çıktı) güvenli varsayılana düşer: boş RPM
    dizisi, has_rpm_data=False."""
    return {
        "id": motor["id"],
        "t": np.asarray(motor["time_s"], dtype=float),
        "current_a": np.asarray(motor["current_a"], dtype=float),
        "has_current_data": motor.get("has_current_data", True),
        "rpm": np.asarray(motor.get("rpm", []), dtype=float),
        "has_rpm_data": motor.get("has_rpm_data", False),
    }


def sample_rate_hz(t: np.ndarray):
    """Medyan örnekleme aralığından örnekleme hızını (Hz) tahmin eder.
    Medyan kullanılıyor, ortalama DEĞİL - log başındaki tekil büyük
    boşluklar (ör. ilk örnekten önceki gecikme) ortalamayı çarpıtır ama
    medyanı etkilemez. 2'den az örnek varsa None döner (hız hesaplanamaz)."""
    if len(t) < 2:
        return None
    dt = float(np.median(np.diff(t)))
    return 1.0 / dt if dt > 0 else None
