"""Eşik/z-score tabanlı anomali tespit motoru - ML öncesi temel/güvenilir
tespit katmanı. Zaman PENCERESİ bazlı çalışır (backend'in uçuş-bazlı tek
sayı kurallarından farklı olarak).

TODO (gelecek iş, bilinçli olarak v1 kapsamı DIŞINDA - bkz. CLAUDE.md
"Kapsam dışı bırakılan fikirler"): yeterli/etiketlenmiş gerçek uçuş verisi
birikirse scikit-learn IsolationForest ile çok değişkenli anomali tespitine
geçilebilir; şimdilik tek-değişkenli eşik/z-score yeterli.
"""

from dataclasses import dataclass

import numpy as np

import anomaly_features
import anomaly_spectral
import flight_series


@dataclass
class AnomalyEvent:
    kind: str        # "voltage_sag" | "current_spike" | "propeller_imbalance" | "efficiency_drop"
    # ("Batarya", 1) / ("Motor", 2) - main.py'nin WARNING_TARGET_RE / tıkla-vurgula
    # mekanizmasının kullandığı AYNI sözleşme, mevcut kodu yeniden kullanabilmek için.
    target: tuple
    t_start: float
    t_end: float
    severity: str     # "warning" | "critical"
    message: str


def _rolling_mean_std(values: np.ndarray, window_samples: int):
    """values üzerinde window_samples genişliğinde, merkeze hizalı bir kayan
    ortalama/std hesaplar (tam vektörize - sliding_window_view). Dizinin
    başındaki/sonundaki (window_samples//2 kadar) örnekler, en yakın
    hesaplanabilen pencere değerine sabitlenir (kenarlarda pencere daralmıyor,
    en yakın tam pencereye "clamp" ediliyor - basitlik için kabul edilen bir
    yaklaşıklık)."""
    n = len(values)
    window_samples = int(min(max(window_samples, 1), n))
    if window_samples <= 1:
        return values.copy(), np.zeros(n)

    windows = np.lib.stride_tricks.sliding_window_view(values, window_samples)
    means_interior = windows.mean(axis=1)
    stds_interior = windows.std(axis=1)

    means = np.empty(n)
    stds = np.empty(n)
    half = window_samples // 2
    means[half:half + len(means_interior)] = means_interior
    stds[half:half + len(stds_interior)] = stds_interior
    if half > 0:
        means[:half] = means_interior[0]
        stds[:half] = stds_interior[0]
    tail_start = half + len(means_interior)
    if tail_start < n:
        means[tail_start:] = means_interior[-1]
        stds[tail_start:] = stds_interior[-1]
    return means, stds


def _consecutive_true_runs(flags: np.ndarray, min_run_samples: int):
    """flags dizisindeki en az min_run_samples uzunluğundaki ardışık True
    gruplarının (başlangıç_indeks, bitiş_indeks_hariç) listesini döner. Daha
    kısa gruplar gürültü sayılıp atılır."""
    runs = []
    n = len(flags)
    i = 0
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if (j - i) >= min_run_samples:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _rolling_zscore_windows(t, values, window_s: float, z_thresh: float, min_run_samples: int = 3):
    """Kayan pencere ortalama/std'sinden z-score hesaplar, |z| > z_thresh
    olan ardışık örnek gruplarını (t_start, t_end, pencere_içi_en_büyük_|z|)
    üçlülerine dönüştürür. window_s saniye cinsinden verilir, örnekleme
    hızından (flight_series.sample_rate_hz) örnek sayısına çevrilir."""
    t = np.asarray(t, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(values) < min_run_samples:
        return []
    rate = flight_series.sample_rate_hz(t)
    if not rate:
        return []
    window_samples = max(min_run_samples, int(window_s * rate))

    means, stds = _rolling_mean_std(values, window_samples)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(stds > 1e-9, (values - means) / stds, 0.0)

    flags = np.abs(z) > z_thresh
    windows = []
    for i_start, i_end in _consecutive_true_runs(flags, min_run_samples):
        windows.append((float(t[i_start]), float(t[i_end - 1]), float(np.max(np.abs(z[i_start:i_end])))))
    return windows


def _severity(max_z: float, z_thresh: float) -> str:
    return "critical" if max_z > z_thresh * 1.5 else "warning"


def detect_voltage_sag_events(battery: dict, window_s: float = 5.0, z_thresh: float = 3.0) -> list:
    """battery: flight_series.battery_series() çıktısı. Voltajın dV/dt'sinde
    z-score uygular; ani düşüşler (sag) negatif yönde büyük |z| üretir.
    Voltaj her zaman ölçülmüş kabul edilir (has_current_data'nın voltaj
    eşleniği yok - akımdan farklı olarak voltaj sensörsüzlüğü şemada yok)."""
    events = []
    rate_signal = anomaly_features.voltage_sag_rate(battery["t"], battery["voltage_v"])
    for t_start, t_end, max_z in _rolling_zscore_windows(battery["t"], rate_signal, window_s, z_thresh):
        events.append(AnomalyEvent(
            kind="voltage_sag",
            target=("Batarya", battery["id"]),
            t_start=t_start, t_end=t_end,
            severity=_severity(max_z, z_thresh),
            message=f"Batarya {battery['id']}: {t_start:.1f}-{t_end:.1f}s aralığında ani voltaj düşüşü.",
        ))
    return events


def detect_current_spike_events(series: dict, label: str, window_s: float = 2.0, z_thresh: float = 3.0) -> list:
    """series: flight_series.battery_series() ya da motor_series() çıktısı.
    label: hedef/mesajda kullanılacak "Batarya" ya da "Motor". Akım sensörü
    yoksa (has_current_data False) backend'in aynı kuralıyla tutarlı şekilde
    hiç çalışmaz - sensörsüz seri yanlış alarm üretmesin diye."""
    if not series["has_current_data"]:
        return []
    events = []
    for t_start, t_end, max_z in _rolling_zscore_windows(series["t"], series["current_a"], window_s, z_thresh):
        events.append(AnomalyEvent(
            kind="current_spike",
            target=(label, series["id"]),
            t_start=t_start, t_end=t_end,
            severity=_severity(max_z, z_thresh),
            message=f"{label} {series['id']}: {t_start:.1f}-{t_end:.1f}s aralığında akım sıçraması.",
        ))
    return events


def detect_propeller_imbalance_events(motor: dict, score_thresh: float = 0.6) -> list:
    """motor: flight_series.motor_series() çıktısı. Uçuşun TAMAMI için tek
    bir dengesizlik skoru hesaplar (pencere bazlı değil - Welch analizi kısa
    pencerelerde güvenilir değil, bkz. anomaly_spectral.MIN_DURATION_S)."""
    if not motor["has_current_data"]:
        return []
    rpm = motor["rpm"] if motor["has_rpm_data"] else None
    score = anomaly_spectral.imbalance_score(motor["t"], motor["current_a"], rpm=rpm)
    if score < score_thresh:
        return []
    return [AnomalyEvent(
        kind="propeller_imbalance",
        target=("Motor", motor["id"]),
        t_start=float(motor["t"][0]), t_end=float(motor["t"][-1]),
        severity="critical" if score > 0.85 else "warning",
        message=f"Motor {motor['id']}: uçuş genelinde pervane dengesizliği belirtisi (skor {score:.2f}).",
    )]


def detect_efficiency_drop_events(motor: dict, window_s: float = 10.0, slope_thresh: float = -50.0) -> list:
    """motor: flight_series.motor_series() çıktısı. Gerçek bir itki/güç
    sensörü olmadığı için RPM/Akım oranı "verim" için bir VEKİL metrik olarak
    kullanılıyor (daha fazla akımla daha az RPM = verim düşüyor). Pencere
    bazında doğrusal regresyon eğimi (np.polyfit) slope_thresh'ten daha
    negatifse anomali sayılır. SADECE hem has_current_data hem has_rpm_data
    true olan motorlarda çalışır - vekil metrik ikisine de ihtiyaç duyuyor.
    slope_thresh kalibre edilmiş bir değer DEĞİL, başlangıç değeri."""
    if not motor["has_current_data"] or not motor["has_rpm_data"]:
        return []

    t = motor["t"]
    rate = flight_series.sample_rate_hz(t)
    if not rate:
        return []
    window_samples = max(3, int(window_s * rate))
    if window_samples > len(t):
        return []

    with np.errstate(divide="ignore", invalid="ignore"):
        efficiency_proxy = np.where(np.abs(motor["current_a"]) > 1e-6, motor["rpm"] / motor["current_a"], 0.0)

    events = []
    for start in range(0, len(t) - window_samples + 1, window_samples):
        end = start + window_samples
        window_t = t[start:end]
        window_eff = efficiency_proxy[start:end]
        if np.all(window_eff == 0.0):
            continue
        slope, _ = np.polyfit(window_t, window_eff, 1)
        if slope < slope_thresh:
            events.append(AnomalyEvent(
                kind="efficiency_drop",
                target=("Motor", motor["id"]),
                t_start=float(window_t[0]), t_end=float(window_t[-1]), severity="warning",
                message=(
                    f"Motor {motor['id']}: {window_t[0]:.1f}-{window_t[-1]:.1f}s aralığında "
                    "RPM/Akım oranı düşüyor (verim düşüşü belirtisi)."
                ),
            ))
    return events


# Backend'in appendImbalanceWarnings'teki (backend/src/main.cpp) akım
# dengesizlik eşiğiyle AYNI değer - tutarlılık için. RPM/PWM eşikleri için
# kalibre edilmiş bir referans yok, aynı başlangıç değeri kullanılıyor.
DEFAULT_MOTOR_IMBALANCE_THRESHOLD = 0.20


def detect_motor_imbalance_events(motors: list, current_threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD,
                                   rpm_threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD) -> list:
    """motors: flight_series.motor_series() çıktılarının listesi. Motorlar
    arasında akım VE (varsa) RPM ortalamalarını kıyaslar - backend'in
    appendImbalanceWarnings'teki "ortalamaların ortalaması ± eşik%" mantığının
    AYNISI, RPM boyutu eklenmiş hali (backend sadece akıma bakıyor). Bir motor
    HER İKİ metrikte de sapıyorsa mesajda ikisi de belirtilir, severity
    "critical" olur (tek metrikte sapma "warning").

    En az 2 motor has_current_data taşımıyorsa (kıyaslanacak bir şey yok) boş
    liste döner - bu durumda çağıran (bkz. detect_motor_imbalance) PWM kanal
    fallback'ine düşmeli."""
    usable = [m for m in motors if m["has_current_data"] and len(m["current_a"]) > 0]
    if len(usable) < 2:
        return []

    current_means = {m["id"]: float(np.mean(m["current_a"])) for m in usable}
    overall_current_mean = float(np.mean(list(current_means.values())))

    rpm_capable = [m for m in usable if m["has_rpm_data"] and len(m["rpm"]) > 0]
    rpm_means, overall_rpm_mean = {}, None
    if len(rpm_capable) >= 2:
        rpm_means = {m["id"]: float(np.mean(m["rpm"])) for m in rpm_capable}
        overall_rpm_mean = float(np.mean(list(rpm_means.values())))

    events = []
    for motor in usable:
        deviations = []
        if overall_current_mean > 0:
            current_dev = (current_means[motor["id"]] - overall_current_mean) / overall_current_mean
            if abs(current_dev) >= current_threshold:
                deviations.append(f"akım %{current_dev * 100:.0f}")
        if overall_rpm_mean and motor["id"] in rpm_means:
            rpm_dev = (rpm_means[motor["id"]] - overall_rpm_mean) / overall_rpm_mean
            if abs(rpm_dev) >= rpm_threshold:
                deviations.append(f"RPM %{rpm_dev * 100:.0f}")
        if not deviations:
            continue
        events.append(AnomalyEvent(
            kind="motor_imbalance",
            target=("Motor", motor["id"]),
            t_start=float(motor["t"][0]), t_end=float(motor["t"][-1]),
            severity="critical" if len(deviations) >= 2 else "warning",
            message=f"Motor {motor['id']}: diğer motorlardan sistematik sapma ({', '.join(deviations)}).",
        ))
    return events


def detect_pwm_channel_imbalance_notes(pwm_outputs: list, threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD) -> list:
    """pwm_outputs: backend JSON'daki ham liste (id/label/time_s/pwm_us).

    ESC telemetrisi (akım/RPM) hiç yoksa, PWM kanalları üzerinde AYNI
    "ortalamaların ortalaması ± eşik%" mantığı uygulanır - ama sonuç bir
    AnomalyEvent (motor hedefi) DEĞİL, düz metin notu olarak döner. Sebep:
    PWM kanalının hangi motora ait olduğu loglarda hiç yazmıyor (bkz.
    shared/power_log_schema.md "Kanal ≠ motor") - backend bu yüzden tahmin
    yürütmüyor, bu fonksiyon da aynı disipline uyuyor: kanal etiketiyle
    ("Kanal 1"/"MAIN 2") raporlar, motor numarası İDDİA ETMEZ."""
    channels = [ch for ch in pwm_outputs if ch.get("pwm_us")]
    if len(channels) < 2:
        return []

    means = {ch["label"]: float(np.mean(ch["pwm_us"])) for ch in channels}
    overall_mean = float(np.mean(list(means.values())))
    if overall_mean <= 0:
        return []

    notes = []
    for label, mean in means.items():
        deviation = (mean - overall_mean) / overall_mean
        if abs(deviation) >= threshold:
            notes.append(
                f"{label}: ortalama PWM'den %{deviation * 100:.0f} sapma "
                "(ESC telemetrisi yok - kanal bazlı karşılaştırma, motor eşlemesi garanti değil)."
            )
    return notes


def detect_motor_imbalance(data: dict, current_threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD,
                            rpm_threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD,
                            pwm_threshold: float = DEFAULT_MOTOR_IMBALANCE_THRESHOLD):
    """Motorlar arası dengesizlik tespitinin orkestratörü. Önce akım/RPM ile
    dener (detect_motor_imbalance_events); HİÇBİR motorda (ya da 2'den azında)
    akım verisi yoksa PWM kanal fallback'ine düşer (detect_pwm_channel_
    imbalance_notes) - kanal bazlı, motor iddiası taşımayan ayrı bir liste.

    Döner: (motor_events: list[AnomalyEvent], pwm_notes: list[str])."""
    motors = [flight_series.motor_series(m) for m in data.get("motors", [])]
    usable = [m for m in motors if m["has_current_data"] and len(m["current_a"]) > 0]
    if len(usable) >= 2:
        return detect_motor_imbalance_events(usable, current_threshold, rpm_threshold), []
    return [], detect_pwm_channel_imbalance_notes(data.get("pwm_outputs", []), pwm_threshold)


def detect_all(data: dict) -> list:
    """Backend JSON'ından (_normalize_time_axis sonrası) TÜM anomali
    olaylarını üretir. Sensörsüz seriler (has_current_data/has_rpm_data
    False) ilgili tespit fonksiyonları tarafından zaten atlanıyor."""
    events = []
    for battery_json in data.get("batteries", []):
        battery = flight_series.battery_series(battery_json)
        events.extend(detect_voltage_sag_events(battery))
        events.extend(detect_current_spike_events(battery, "Batarya"))
    for motor_json in data.get("motors", []):
        motor = flight_series.motor_series(motor_json)
        events.extend(detect_current_spike_events(motor, "Motor"))
        events.extend(detect_propeller_imbalance_events(motor))
        events.extend(detect_efficiency_drop_events(motor))
    return events
