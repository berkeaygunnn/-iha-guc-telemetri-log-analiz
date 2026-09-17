"""Motor akımı/RPM sinyalinden Welch PSD (scipy.signal.welch) ile pervane
dengesizliği tahmini.

NOT: Titreşim (ArduPilot VIBE / PX4 sensor_accel-vehicle_acceleration) verisi
backend'de HİÇ ayrıştırılmıyor - bilinçli olarak v1 kapsamı dışı bırakıldı
(bkz. shared/power_log_schema.md). Bu modül pervane dengesizliğini gerçek bir
titreşim ölçümünden değil, akım/RPM dalgalanmasının DOLAYLI spektral
analizinden tahmin ediyor.
"""

import numpy as np
from scipy.signal import welch

import flight_series
import resample as resample_mod  # ad çakışmasını önlemek için (scipy'de de "resample" var)

# Welch'in pencereleme varsayımı bundan kısa sinyallerde anlamsız.
MIN_DURATION_S = 2.0


def _resample_and_welch(t, values, target_rate_hz: float):
    """t, values'i sabit hıza resample edip Welch PSD'sini döner.

    Hedef hız, sinyalin GERÇEK (orijinal) örnekleme hızıyla sınırlanır
    (min(target_rate_hz, orijinal_hız)) - orijinal hızın üzerine çıkmak
    (upsampling) yeni bilgi katmıyor, sadece interpolasyonun ürettiği yapay
    bir alçak geçiren filtreleme etkisiyle yüksek frekans bantlarını neredeyse
    sıfıra düşürüyor. Bu, PSD'nin "tepeliliğini" (peakiness) ölçen no-RPM
    tespit yoluna GERÇEK olmayan, çok yüksek bir dengesizlik skoru olarak
    yansıyordu (saf beyaz gürültüde bile ölçüldü: peakiness ~14) - ölçülüp
    düzeltildi, artık asla orijinal hızın üzerine çıkılmıyor."""
    t = np.asarray(t, dtype=float)
    if len(t) < 2 or (t[-1] - t[0]) < MIN_DURATION_S:
        return None, None
    original_rate = flight_series.sample_rate_hz(t)
    effective_rate = min(target_rate_hz, original_rate) if original_rate else target_rate_hz
    _, values_uniform = resample_mod.resample_uniform(t, values, effective_rate)
    if len(values_uniform) < 8:
        return None, None
    freqs, psd = welch(values_uniform, fs=effective_rate, nperseg=min(len(values_uniform), 256))
    if len(freqs) < 2:
        return None, None
    return freqs, psd


def dominant_frequency_hz(t, values, target_rate_hz: float = 50.0):
    """Welch PSD'sinde DC (0 Hz) hariç en baskın frekans bileşenini (Hz)
    döner. Yetersiz süre/örnek varsa None döner."""
    freqs, psd = _resample_and_welch(t, values, target_rate_hz)
    if freqs is None:
        return None
    # freqs[0] = 0 Hz (DC bileşeni) - motor dengesizliğiyle ilgisiz, hariç tutuluyor.
    peak_index = 1 + int(np.argmax(psd[1:]))
    return float(freqs[peak_index])


def expected_rotor_frequency_hz(rpm_values):
    """Ortalama RPM'den dönüş frekansı (Hz). Boş/sıfır ortalama RPM'de
    None döner (rotor frekansı tanımsız/anlamsız)."""
    rpm_values = np.asarray(rpm_values, dtype=float)
    if len(rpm_values) == 0:
        return None
    mean_rpm = np.mean(rpm_values)
    return mean_rpm / 60.0 if mean_rpm > 0 else None


def imbalance_score(t, current_a, rpm=None, target_rate_hz: float = 50.0) -> float:
    """0-1 aralığında kaba bir "dengesizlik olasılığı" skoru.

    RPM VARSA: akımın baskın frekansı ile beklenen rotor frekansının (1x)
    ne kadar yakın olduğuna göre - pervane dengesizliği tipik olarak rotor
    frekansında bir akım dalgalanması üretir.
    RPM YOKSA (kabul edilen düşüş - motor bazlı veri yoksa sınırlı olacağı
    zaten biliniyor): sadece akımın PSD'sindeki en baskın tepenin
    belirginliğine (tepe/ortalama PSD oranı) dayalı daha kaba bir skor.
    """
    t = np.asarray(t, dtype=float)
    current_a = np.asarray(current_a, dtype=float)
    dominant_hz = dominant_frequency_hz(t, current_a, target_rate_hz)
    if dominant_hz is None:
        return 0.0

    if rpm is not None and len(rpm) > 0:
        expected_hz = expected_rotor_frequency_hz(rpm)
        if expected_hz is None:
            return 0.0
        relative_error = abs(dominant_hz - expected_hz) / expected_hz
        return float(np.clip(1.0 - relative_error, 0.0, 1.0))

    freqs, psd = _resample_and_welch(t, current_a, target_rate_hz)
    if freqs is None or np.mean(psd[1:]) <= 0:
        return 0.0
    peakiness = np.max(psd[1:]) / np.mean(psd[1:])
    # Deneysel ölçek: peakiness 1 (düz spektrum) -> 0 skor, >=10 -> 1 skor.
    # Kalibre edilmiş bir eşik değil, başlangıç değeri.
    return float(np.clip((peakiness - 1.0) / 9.0, 0.0, 1.0))
