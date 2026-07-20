"""Backend'in ArduPilot/PX4 parser'ları için otomatik regresyon testleri.

Bu ana kadar her yeni özellik sentetik bir dosya üretip JSON çıktısını elle
okuyarak doğrulanmıştı; bu script o kontrolleri gerçek, tekrar çalıştırılabilir
assertion'lara çeviriyor.

Kullanım: python run_tests.py  (backend/tests/ içinden ya da repo kökünden)
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_synthetic_log
import make_synthetic_ulog

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
DATA_DIR = BACKEND_DIR.parent / "data"


def find_backend_exe() -> Path:
    """Windows'ta .exe uzantılı, Linux CI'da uzantısız olabileceği için ikisini de dener."""
    build_dir = BACKEND_DIR / "build"
    for name in ("power_log_backend.exe", "power_log_backend"):
        candidate = build_dir / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Backend derlenmemiş: {build_dir} altında power_log_backend bulunamadı. "
        "Önce 'cmake --build build' ile derleyin."
    )


def run_backend(input_path: Path) -> dict:
    """Backend'i verilen log dosyasıyla çalıştırıp ürettiği JSON'u döner."""
    backend_exe = find_backend_exe()
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "output.json"
        result = subprocess.run(
            [str(backend_exe), str(input_path), str(output_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Backend basarisiz oldu ({input_path.name}): {result.stderr.strip()}")
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)


def battery_by_id(data: dict, battery_id: int) -> dict:
    for battery in data["batteries"]:
        if battery["id"] == battery_id:
            return battery
    raise AssertionError(f"batarya id={battery_id} bulunamadi")


def motor_by_id(data: dict, motor_id: int) -> dict:
    for motor in data["motors"]:
        if motor["id"] == motor_id:
            return motor
    raise AssertionError(f"motor id={motor_id} bulunamadi")


class SyntheticLogTests(unittest.TestCase):
    """Bilinen değerlerle üretilen sentetik dosyalar; hem ArduPilot hem PX4
    aynı uçuş profiliyle üretildiği için beklenen değerler ortak."""

    def _check_two_battery_four_motor_profile(self, data: dict, expected_format: str):
        self.assertEqual(data["meta"]["format"], expected_format)

        self.assertEqual(len(data["batteries"]), 2)
        battery1 = battery_by_id(data, 1)
        battery2 = battery_by_id(data, 2)
        self.assertAlmostEqual(battery1["voltage_v"][0], 16.8, places=3)
        self.assertAlmostEqual(battery1["current_a"][5], 30.0, places=3)
        self.assertAlmostEqual(battery2["voltage_v"][0], 15.8, places=3)  # battery1 - 1.0
        self.assertAlmostEqual(battery2["current_a"][5], 18.0, places=3)  # battery1 * 0.6

        self.assertEqual(len(data["motors"]), 4)
        motor3 = motor_by_id(data, 3)
        self.assertAlmostEqual(motor3["current_a"][5], 8.25, places=3)  # 30/4*1.1

        # motor_factors = [0.8, 0.95, 1.1, 1.25] -> genel ortalama 1.025.
        # motor1 (%-22) ve motor4 (%+22) dengesizlik eşiğini (%20) aşıyor,
        # motor2/motor3 (%±7) aşmıyor. Her iki batarya da sadece ~%9 voltaj
        # düşümü içeriyor (eşik %15), yani voltaj uyarısı beklenmiyor.
        warnings = data["warnings"]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("Motor 1" in w for w in warnings))
        self.assertTrue(any("Motor 4" in w for w in warnings))
        self.assertFalse(any("Batarya" in w for w in warnings))

    def test_ardupilot_synthetic(self):
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(make_synthetic_log.generate())
            path = Path(f.name)
        try:
            data = run_backend(path)
            self._check_two_battery_four_motor_profile(data, "ardupilot")
        finally:
            path.unlink(missing_ok=True)

    def test_px4_synthetic(self):
        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(make_synthetic_ulog.generate())
            path = Path(f.name)
        try:
            data = run_backend(path)
            self._check_two_battery_four_motor_profile(data, "px4")
        finally:
            path.unlink(missing_ok=True)


class WarningRuleTests(unittest.TestCase):
    """Kural tabanlı uyarı motorunun (computeWarnings) voltaj-düşümü kuralı
    için ayrı, bilerek büyük bir düşüş içeren küçük bir fixture ile testi.
    make_synthetic_log.generate()'in ana profili (motor dengesizliği testinde
    kullanılıyor) kasten değiştirilmiyor; bunun yerine aynı dosyanın export
    ettiği alt düzey yapı taşları (build_fmt_message/build_bat_message) ile
    tek amaçlı, minimal bir buffer kuruluyor."""

    def _generate_voltage_sag_log(self) -> bytes:
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(1.0, 0, 12.0, 10.0)  # ~%29 düşüş (eşik %15)
        return bytes(out)

    def test_voltage_sag_warning_triggers(self):
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(self._generate_voltage_sag_log())
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            warnings = data["warnings"]
            self.assertEqual(len(warnings), 1)
            self.assertIn("Batarya 1", warnings[0])
        finally:
            path.unlink(missing_ok=True)


class RealLogRegressionTests(unittest.TestCase):
    """Gerçek örnek loglarla önceden doğrulanmış değerlere karşı regresyon çapası."""

    def test_ardupilot_real_flight(self):
        data = run_backend(DATA_DIR / "ArduCopter-MaxAltFence-00000067.BIN")
        self.assertEqual(data["meta"]["format"], "ardupilot")
        self.assertEqual(len(data["batteries"]), 1)
        self.assertEqual(len(data["batteries"][0]["time_s"]), 960)
        self.assertEqual(len(data["motors"]), 4)
        self.assertIsInstance(data["warnings"], list)

    def test_ardupilot_real_idle(self):
        data = run_backend(DATA_DIR / "ArduCopter-SensorErrorFlags-00000012.BIN")
        self.assertEqual(data["meta"]["format"], "ardupilot")
        self.assertEqual(len(data["batteries"]), 1)
        self.assertEqual(len(data["batteries"][0]["time_s"]), 9)
        self.assertIsInstance(data["warnings"], list)

    def test_px4_real_multi_battery(self):
        data = run_backend(DATA_DIR / "px4_sample_log_small.ulg")
        self.assertEqual(data["meta"]["format"], "px4")
        self.assertEqual(len(data["batteries"]), 2)  # bu logda gercekten 2 farkli guc kaynagi var
        self.assertEqual(len(data["motors"]), 0)  # bu logda esc_status hic loglanmamis
        self.assertIsInstance(data["warnings"], list)


if __name__ == "__main__":
    unittest.main()
