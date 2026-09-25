"""PWM kanallarının üst sınıra (doygunluk) ne kadar yakın kaldığını
hesaplayan, Tk'siz modül.

"Üst sınır" sabit yazılmıyor - yapılandırılabilir bir eşik parametresi
alıyor (varsayılan aşağıdaki DEFAULT_SATURATION_THRESHOLD_US, tipik ESC/
servo tam gaz aralığına yakın bir BAŞLANGIÇ değeri - kalibre edilmedi,
main.py'deki Ayarlar dialogundan değiştirilebilir).

Sonuç kanal ETİKETİYLE ("Kanal 1"/"MAIN 2") raporlanır, "motor" denmez -
PWM kanalının hangi motora ait olduğu loglarda hiç yazmıyor (bkz.
shared/power_log_schema.md "Kanal ≠ motor"), anomaly_detect.py'deki PWM
fallback'iyle AYNI disiplin.
"""

from dataclasses import dataclass

import numpy as np

DEFAULT_SATURATION_THRESHOLD_US = 1900.0


@dataclass
class ChannelSaturation:
    channel_label: str
    saturated_pct: float  # 0-100: örneklerin yüzde kaçı eşiğin üstünde/eşit


def compute_channel_saturation(pwm_outputs: list, threshold_us: float = DEFAULT_SATURATION_THRESHOLD_US):
    """Her PWM kanalı için, değerin threshold_us'in ÜSTÜNDE (>=) kaldığı
    örnek yüzdesini hesaplar.

    Döner: (per_channel: list[ChannelSaturation], overall_pct: float | None).
    PWM verisi hiç yoksa ([], None) - çökme yerine "hesaplanamadı" hali."""
    channels = [ch for ch in pwm_outputs if ch.get("pwm_us")]
    if not channels:
        return [], None

    per_channel = []
    for ch in channels:
        values = np.asarray(ch["pwm_us"], dtype=float)
        saturated_pct = float(np.mean(values >= threshold_us)) * 100.0
        per_channel.append(ChannelSaturation(channel_label=ch["label"], saturated_pct=saturated_pct))

    # "Toplam": kanal başına yüzdelerin basit ortalaması. Kanallar farklı
    # uzunlukta zaman serisine sahip olabildiği için (bkz. backend'in sabit
    # kanalları eleme kuralı) ortak bir zaman ızgarasına oturtup örnek bazlı
    # birleştirmek bu basit özet için gereksiz karmaşıklık olurdu.
    overall_pct = float(np.mean([r.saturated_pct for r in per_channel]))
    return per_channel, overall_pct
