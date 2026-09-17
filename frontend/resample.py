"""(t, values) zaman serisini sabit hızlı bir ızgaraya yeniden örnekler.

Gerekli çünkü: PX4/ArduPilot loglarının doğal örnekleme hızları farklı, hatta
aynı logun time_s dizisi de düzensiz aralıklı olabilir (bkz.
flight_series.sample_rate_hz'deki medyan notu). Welch spektral analizi
(anomaly_spectral.py) sabit örnekleme hızı gerektiriyor.
"""

import numpy as np


def resample_uniform(t, values, target_rate_hz: float):
    """(t, values) dizisini target_rate_hz'de eşit aralıklı bir ızgaraya
    doğrusal interpolasyonla (np.interp) yeniden örnekler. t'nin artan
    sırada olduğu varsayılır (backend zaten sıralı yazıyor). 2'den az
    örnek varsa yeniden örnekleme anlamsız, (t, values) olduğu gibi döner."""
    t = np.asarray(t, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(t) < 2:
        return t, values
    t_uniform = np.arange(t[0], t[-1], 1.0 / target_rate_hz)
    return t_uniform, np.interp(t_uniform, t, values)
