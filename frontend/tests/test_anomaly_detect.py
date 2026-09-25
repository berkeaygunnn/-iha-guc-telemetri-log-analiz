"""anomaly_detect.py için birim testleri. Tk'ye ve backend'e gerek yok -
doğrudan backend-JSON şeklinde elle kurulmuş sözlüklerle çalışır."""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import anomaly_detect
import flight_series


def _clean_battery_json(battery_id=1, n=200, dt=0.1):
    """Gerçekçi "sağlıklı uçuş" profili: yavaş düzgün düşüş + küçük RASTGELE
    gürültü. Bilerek tek-frekanslı bir sinüs KULLANILMIYOR - saf bir sinüs
    dalgası PSD'de tek baskın tepe ürettiği için propeller_imbalance
    tespitini (RPM'siz PSD-peakiness yolu) yanlışlıkla tetikleyebilir; gerçek
    "gürültülü ama dengesizlik imzası olmayan" bir akımı temsil etmiyor."""
    rng = np.random.default_rng(7)
    t = (np.arange(n) * dt).tolist()
    voltage = (16.8 - 0.01 * np.arange(n)).tolist()  # yavaş, düzgün bir düşüş - anomali DEĞİL
    current = (10.0 + rng.normal(0.0, 0.3, n)).tolist()
    return {
        "id": battery_id, "time_s": t, "voltage_v": voltage, "current_a": current,
        "has_current_data": True,
    }


def _clean_motor_json(motor_id=1, n=200, dt=0.1):
    rng = np.random.default_rng(11)
    t = (np.arange(n) * dt).tolist()
    current = (5.0 + rng.normal(0.0, 0.15, n)).tolist()
    return {
        "id": motor_id, "time_s": t, "current_a": current, "has_current_data": True,
        "rpm": [0.0] * n, "has_rpm_data": False,
    }


class VoltageSagDetectionTests(unittest.TestCase):
    def test_injected_sharp_sag_is_detected(self):
        # Yüksek örnekleme hızı + uzun temel çizgi: pencere, ani düşüşü
        # sulandırmadan (rolling z-score kendi penceresinde anomaliyi de
        # içerdiği için kısa/düşük hızlı bir sinyalde sulanma z'yi eşiğin
        # altına düşürebiliyor) net şekilde ayırt edebilsin diye.
        n, dt = 1500, 0.02  # 30s, 50 Hz
        t = np.arange(n) * dt
        voltage = np.full(n, 16.8)
        # t=15.0 - 15.08s arasinda 16.8 -> 10.0'a ANİ bir düşüş.
        sag_start, sag_end = 750, 754
        voltage[sag_start:sag_end] = np.linspace(16.8, 10.0, sag_end - sag_start)
        voltage[sag_end:] = 10.0

        battery = flight_series.battery_series({
            "id": 1, "time_s": t.tolist(), "voltage_v": voltage.tolist(),
            "current_a": [0.0] * n, "has_current_data": True,
        })
        events = anomaly_detect.detect_voltage_sag_events(battery)
        self.assertTrue(events, "Enjekte edilmiş voltaj düşüşü hiç tespit edilmedi")
        event = events[0]
        self.assertEqual(event.kind, "voltage_sag")
        self.assertEqual(event.target, ("Batarya", 1))
        # Tespit edilen pencere, enjekte edilen sag aralığına yakın olmalı.
        self.assertLess(abs(event.t_start - t[sag_start]), 1.0)


class CurrentSpikeDetectionTests(unittest.TestCase):
    def test_injected_spike_is_detected(self):
        n, dt = 1500, 0.02  # 30s, 50 Hz - bkz. voltaj testindeki sulanma notu
        t = np.arange(n) * dt
        current = np.full(n, 10.0)
        current[750:754] = 40.0  # ani sıçrama

        motor = flight_series.motor_series({
            "id": 2, "time_s": t.tolist(), "current_a": current.tolist(),
            "has_current_data": True,
        })
        events = anomaly_detect.detect_current_spike_events(motor, "Motor")
        self.assertTrue(events, "Enjekte edilmiş akım sıçraması hiç tespit edilmedi")
        event = events[0]
        self.assertEqual(event.kind, "current_spike")
        self.assertEqual(event.target, ("Motor", 2))

    def test_skips_series_without_current_data(self):
        n, dt = 1500, 0.02
        t = np.arange(n) * dt
        current = np.full(n, 10.0)
        current[750:754] = 40.0
        motor = flight_series.motor_series({
            "id": 3, "time_s": t.tolist(), "current_a": current.tolist(),
            "has_current_data": False,
        })
        self.assertEqual(anomaly_detect.detect_current_spike_events(motor, "Motor"), [])


class PropellerImbalanceDetectionTests(unittest.TestCase):
    def test_matching_rpm_and_ripple_produces_event(self):
        n, dt = 1000, 0.01  # 10s, 100 Hz
        t = np.arange(n) * dt
        current = 10.0 + 2.0 * np.sin(2 * np.pi * 5.0 * t)  # 5 Hz dalgalanma
        rpm = np.full(n, 300.0)  # 300 RPM = 5 Hz rotor frekansı -> eşleşiyor
        motor = flight_series.motor_series({
            "id": 1, "time_s": t.tolist(), "current_a": current.tolist(),
            "has_current_data": True, "rpm": rpm.tolist(), "has_rpm_data": True,
        })
        events = anomaly_detect.detect_propeller_imbalance_events(motor)
        self.assertTrue(events)
        self.assertEqual(events[0].kind, "propeller_imbalance")
        self.assertEqual(events[0].target, ("Motor", 1))


class CleanFlightControlTests(unittest.TestCase):
    """Sağlıklı, anomalisiz bir uçuşta hiç olay üretilmemeli - yanlış alarm
    olmadığının kanıtı (backend'in warnings kalibrasyon felsefesiyle aynı)."""

    def test_detect_all_returns_empty_on_clean_flight(self):
        data = {"batteries": [_clean_battery_json()], "motors": [_clean_motor_json()]}
        self.assertEqual(anomaly_detect.detect_all(data), [])

    def test_detect_all_skips_missing_keys_gracefully(self):
        self.assertEqual(anomaly_detect.detect_all({}), [])


def _motor(motor_id, current_mean, rpm_mean=None, n=100, dt=0.1):
    t = (np.arange(n) * dt).tolist()
    current = (np.full(n, current_mean) + np.random.default_rng(motor_id).normal(0, 0.1, n)).tolist()
    json_dict = {"id": motor_id, "time_s": t, "current_a": current, "has_current_data": True}
    if rpm_mean is not None:
        json_dict["rpm"] = (np.full(n, rpm_mean)).tolist()
        json_dict["has_rpm_data"] = True
    return flight_series.motor_series(json_dict)


class MotorImbalanceEventsTests(unittest.TestCase):
    def test_deviating_motor_current_is_flagged(self):
        # 3 motor ~10A, biri ~15A (%50 sapma, eşiğin (0.20) çok üstünde).
        motors = [_motor(1, 10.0), _motor(2, 10.0), _motor(3, 15.0)]
        events = anomaly_detect.detect_motor_imbalance_events(motors)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target, ("Motor", 3))
        self.assertIn("akım", events[0].message)

    def test_deviating_motor_rpm_is_also_flagged(self):
        motors = [
            _motor(1, 10.0, rpm_mean=3000.0), _motor(2, 10.0, rpm_mean=3000.0),
            _motor(3, 10.0, rpm_mean=1800.0),  # akım aynı, RPM %40 düşük
        ]
        events = anomaly_detect.detect_motor_imbalance_events(motors)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target, ("Motor", 3))
        self.assertIn("RPM", events[0].message)
        self.assertEqual(events[0].severity, "warning")  # tek metrikte sapma

    def test_deviating_in_both_metrics_is_critical(self):
        motors = [
            _motor(1, 10.0, rpm_mean=3000.0), _motor(2, 10.0, rpm_mean=3000.0),
            _motor(3, 15.0, rpm_mean=1800.0),  # hem akım hem RPM sapıyor
        ]
        events = anomaly_detect.detect_motor_imbalance_events(motors)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].severity, "critical")

    def test_balanced_motors_produce_no_events(self):
        motors = [_motor(1, 10.0), _motor(2, 10.1), _motor(3, 9.9)]
        self.assertEqual(anomaly_detect.detect_motor_imbalance_events(motors), [])

    def test_fewer_than_two_usable_motors_returns_empty(self):
        motors = [_motor(1, 10.0)]
        self.assertEqual(anomaly_detect.detect_motor_imbalance_events(motors), [])


class PwmChannelImbalanceNotesTests(unittest.TestCase):
    def _channel(self, label, mean_pwm, n=50):
        return {"id": 1, "label": label, "time_s": list(range(n)), "pwm_us": [mean_pwm] * n}

    def test_deviating_channel_produces_note_with_channel_label_not_motor(self):
        channels = [self._channel("Kanal 1", 1500), self._channel("Kanal 2", 1500),
                    self._channel("Kanal 3", 2200)]
        notes = anomaly_detect.detect_pwm_channel_imbalance_notes(channels)
        self.assertEqual(len(notes), 1)
        self.assertIn("Kanal 3", notes[0])
        self.assertNotIn("Motor", notes[0])

    def test_balanced_channels_produce_no_notes(self):
        channels = [self._channel("Kanal 1", 1500), self._channel("Kanal 2", 1510)]
        self.assertEqual(anomaly_detect.detect_pwm_channel_imbalance_notes(channels), [])

    def test_fewer_than_two_channels_returns_empty(self):
        self.assertEqual(anomaly_detect.detect_pwm_channel_imbalance_notes([self._channel("Kanal 1", 1500)]), [])


class DetectMotorImbalanceOrchestratorTests(unittest.TestCase):
    def test_uses_current_rpm_path_when_motors_have_current_data(self):
        data = {
            "motors": [
                {"id": 1, "time_s": list(range(50)), "current_a": [10.0] * 50, "has_current_data": True},
                {"id": 2, "time_s": list(range(50)), "current_a": [15.0] * 50, "has_current_data": True},
            ],
            "pwm_outputs": [],
        }
        events, notes = anomaly_detect.detect_motor_imbalance(data)
        self.assertTrue(events)
        self.assertEqual(notes, [])

    def test_falls_back_to_pwm_when_no_motor_current_data(self):
        data = {
            "motors": [],
            "pwm_outputs": [
                {"id": 1, "label": "Kanal 1", "time_s": list(range(50)), "pwm_us": [1500] * 50},
                {"id": 2, "label": "Kanal 2", "time_s": list(range(50)), "pwm_us": [2500] * 50},
            ],
        }
        events, notes = anomaly_detect.detect_motor_imbalance(data)
        self.assertEqual(events, [])
        self.assertTrue(notes)
        self.assertNotIn("Motor", notes[0])


if __name__ == "__main__":
    unittest.main()
