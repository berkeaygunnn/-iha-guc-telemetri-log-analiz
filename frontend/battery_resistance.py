"""Batarya iç direncini voltaj~akım regresyonundan tahmin eden, Tk'siz modül.

Eski main.py'deki tek-adımlı yaklaşıma (tüm uçuşun ham örnekleri, filtresiz
np.polyfit) göre iki iyileştirme: (1) basit bir kayan-ortalama filtresiyle
örnekleme gürültüsü azaltılıyor, (2) regresyon SADECE akımın belirgin
değiştiği örneklerle yapılıyor (durağan/hover pencereleri regresyonu
gürültüyle dolduruyordu). Ayrıca regresyonun R² değeri güven göstergesi
olarak döner.

R²/güven etiketi eşikleri (0.7/0.4) kalibre edilmiş değil, başlangıç
değerleri — anomaly_detect.py'deki aynı felsefe (gerçek bozuk/yaşlı bir
batarya örneğiyle doğrulanmadı).
"""

from dataclasses import dataclass

import numpy as np

MIN_SAMPLES = 5
MIN_CURRENT_RANGE_A = 0.5  # eski koddaki filtreyle aynı: akım hiç değişmiyorsa regresyon anlamsız
SMOOTH_WINDOW_SAMPLES = 5
# Akım, ~bu kadar örnekten oluşan pencerelere bölünüp her pencerenin yerel
# standart sapması genel (uçuş geneli) standart sapmayla kıyaslanıyor.
# Yerel sapma bu oranın ALTINDAYSA pencere "durağan/hover" sayılıp
# regresyondan çıkarılıyor. Nokta-nokta bir |dI/dt| eşiği YERİNE pencere
# kullanılıyor: ilki periyodik bir sinyalde (ör. yumuşak bir sinüs) tepe/dip
# noktalarını dışlayıp orta bölgeye sıkışıyor, bu da regresyonun akım
# aralığını daraltıp doğruluğu KÖTÜLEŞTİRiyordu (ölçüldü, birim testiyle
# yakalandı).
ACTIVE_WINDOW_SAMPLES = 20
ACTIVE_WINDOW_STD_RATIO = 0.15
R_SQUARED_HIGH = 0.7
R_SQUARED_MEDIUM = 0.4


@dataclass
class ResistanceEstimate:
    battery_id: int
    resistance_mohm: float
    r_squared: float  # 0-1, regresyonun veriye ne kadar iyi oturduğu
    confidence_label: str  # "yüksek" | "orta" | "düşük"
    n_samples_used: int


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    """Kenarlarda daralan bir kayan ortalama. Düzensiz örnekleme aralığında
    bile komşu örnekler arası gürültüyü azaltmaya yarar; zaman ekseninde bir
    hız/frekans varsayımı yapmaz.

    np.convolve(..., mode="same") DOĞRUDAN kullanılmıyor: o, pencerenin
    dizi dışına taştığı kenar noktalarında eksik kısmı SIFIRLA dolduruyor
    (zero-padding) ama bölen hâlâ tam pencere genişliği (5) — yani ilk/son
    birkaç örnek gerçek değerinin ~1/5-2/5'ine düşüyor. Bu, gerçek bir hata
    olarak ÖLÇÜLDÜ (birim testinde R² 0.999'dan 0.08'e düştü, ilk 2 örnek
    16.5V yerine 9.8V/13.0V çıktı) ve bu iki bozuk uç nokta 200 örneklik
    regresyonu domine etti. Düzeltme: payı VE payydayı aynı şekilde
    konvolve edip bölmek, kenarlarda gerçek katkı sayısına (5 yerine 3, 4
    gibi) göre normalize ediyor - zero-padding'in yanlı etkisi iptal olur."""
    if window <= 1 or len(values) < window:
        return values
    kernel = np.ones(window)
    weights = np.convolve(np.ones(len(values)), kernel, mode="same")
    return np.convolve(values, kernel, mode="same") / weights


def _select_active_windows(current_a: np.ndarray, window_samples: int, std_ratio: float) -> np.ndarray:
    """current_a'yı window_samples uzunluğunda pencerelere böler, yerel
    standart sapması genel sapmanın std_ratio katından DÜŞÜK olan pencereleri
    (durağan/hover bölümleri) dışlayan bir boolean maske döner. Hiçbir
    pencere kalmazsa (uçun tamamı durağansa) tüm seri kullanılır - tamamen
    reddetmek yerine daha az güvenilir ama yine de bir tahmin vermek adına."""
    n = len(current_a)
    overall_std = float(np.std(current_a))
    if n < window_samples or overall_std <= 0:
        return np.ones(n, dtype=bool)

    threshold = overall_std * std_ratio
    mask = np.zeros(n, dtype=bool)
    for start in range(0, n, window_samples):
        end = min(start + window_samples, n)
        if np.std(current_a[start:end]) >= threshold:
            mask[start:end] = True

    return mask if mask.any() else np.ones(n, dtype=bool)


def estimate_battery_resistance(battery: dict):
    """Tek bir bataryanın iç direncini tahmin eder.

    Döner: (ResistanceEstimate, None) ya da (None, "hesaplanamadı: <sebep>").
    Çökme yerine her zaman bu iki durumdan biri döner."""
    if not battery.get("has_current_data", True):
        return None, "hesaplanamadı: akım sensörü verisi yok"

    current_a = np.asarray(battery["current_a"], dtype=float)
    voltage_v = np.asarray(battery["voltage_v"], dtype=float)
    if len(current_a) < MIN_SAMPLES:
        return None, "hesaplanamadı: log çok kısa"
    if np.ptp(current_a) < MIN_CURRENT_RANGE_A:
        return None, "hesaplanamadı: akım yeterince değişmiyor"

    current_smooth = _moving_average(current_a, SMOOTH_WINDOW_SAMPLES)
    voltage_smooth = _moving_average(voltage_v, SMOOTH_WINDOW_SAMPLES)

    active_mask = _select_active_windows(current_smooth, ACTIVE_WINDOW_SAMPLES, ACTIVE_WINDOW_STD_RATIO)
    if np.count_nonzero(active_mask) < MIN_SAMPLES:
        active_mask = np.ones_like(current_smooth, dtype=bool)

    current_selected = current_smooth[active_mask]
    voltage_selected = voltage_smooth[active_mask]

    slope, intercept = np.polyfit(current_selected, voltage_selected, 1)
    resistance_mohm = abs(slope) * 1000.0

    predicted = slope * current_selected + intercept
    ss_res = float(np.sum((voltage_selected - predicted) ** 2))
    ss_tot = float(np.sum((voltage_selected - np.mean(voltage_selected)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    r_squared = max(0.0, min(1.0, r_squared))

    if r_squared >= R_SQUARED_HIGH:
        confidence_label = "yüksek"
    elif r_squared >= R_SQUARED_MEDIUM:
        confidence_label = "orta"
    else:
        confidence_label = "düşük"

    return ResistanceEstimate(
        battery_id=battery["id"], resistance_mohm=resistance_mohm, r_squared=r_squared,
        confidence_label=confidence_label, n_samples_used=int(np.count_nonzero(active_mask)),
    ), None


def estimate_worst_battery_resistance(batteries: list):
    """Birden fazla batarya varsa en yüksek (en zayıf görünen) tahmini döner
    (eski _battery_internal_resistance_estimate'in "en zayıfı seç" davranışı
    korunuyor). Döner: (ResistanceEstimate, None) ya da (None, sebep)."""
    estimates = []
    reasons = []
    for battery in batteries:
        estimate, reason = estimate_battery_resistance(battery)
        if estimate is not None:
            estimates.append(estimate)
        elif reason:
            reasons.append(reason)

    if not estimates:
        if len(reasons) == 1:
            return None, reasons[0]
        return None, "hesaplanamadı: hiçbir batarya için yeterli veri yok"

    worst = max(estimates, key=lambda e: e.resistance_mohm)
    return worst, None
