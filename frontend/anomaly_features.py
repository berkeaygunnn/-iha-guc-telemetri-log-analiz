"""Bir uçuşun akım/voltaj serisinden türetilen küçük, tek amaçlı öznitelik
fonksiyonları. Hepsi saf numpy - Tk'ye ve backend'e bağımlı değil.
"""

import numpy as np


def rms(values) -> float:
    """Etkin (RMS) akım - tepe akımdan farklı olarak sürekli ısınma/yük
    etkisini yansıtır. Boş dizide 0.0 döner."""
    values = np.asarray(values, dtype=float)
    return float(np.sqrt(np.mean(np.square(values)))) if len(values) else 0.0


def peak(values) -> float:
    """Mutlak değerce en büyük örnek (yönden bağımsız tepe akım)."""
    values = np.asarray(values, dtype=float)
    return float(np.max(np.abs(values))) if len(values) else 0.0


def voltage_sag_rate(t, voltage_v) -> np.ndarray:
    """dV/dt zaman serisi (np.gradient). Backend'in tek-sayı "ilk örneğe göre
    %düşüş" kuralından farklı olarak ANİ düşüşleri zaman içinde yakalar;
    pencere bazlı anomali tespitine (anomaly_detect.py) girdi olur."""
    t = np.asarray(t, dtype=float)
    voltage_v = np.asarray(voltage_v, dtype=float)
    if len(t) < 2:
        return np.zeros_like(voltage_v)
    return np.gradient(voltage_v, t)


def current_ripple(values) -> float:
    """std/ortalama oranı (dalgalanma katsayısı) - sağlıklı sabit yükte
    düşük, kararsız/titreşimli çekişte yüksek. Ortalama ~0'a çok yakınsa
    (akım neredeyse sıfır) oran patlayabileceği için 0.0 döner."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return 0.0
    mean = np.mean(values)
    if abs(mean) < 1e-6:
        return 0.0
    return float(np.std(values) / abs(mean))
