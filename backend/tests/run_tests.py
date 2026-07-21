"""Backend'in ArduPilot/PX4 parser'ları için otomatik regresyon testleri.

Bu ana kadar her yeni özellik sentetik bir dosya üretip JSON çıktısını elle
okuyarak doğrulanmıştı; bu script o kontrolleri gerçek, tekrar çalıştırılabilir
assertion'lara çeviriyor.

Kullanım: python run_tests.py  (backend/tests/ içinden ya da repo kökünden)
"""

import json
import struct
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
    tek amaçlı, minimal bir buffer kuruluyor.

    Not: main.cpp'deki MIN_DURATION_FOR_WARNINGS_S (5s) eşiği yüzünden, bu
    fixture'daki örnekler en az 5 saniye arayla olmalı, yoksa "çok kısa/idle
    veri" olarak muaf tutulup uyarı hiç üretilmez."""

    def _generate_voltage_sag_log(self) -> bytes:
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 12.0, 10.0)  # ~%29 düşüş (eşik %15), 6s aralık (eşik 5s)
        return bytes(out)

    def _generate_short_but_large_sag_log(self) -> bytes:
        """Aynı büyük voltaj düşümü ama süre eşiğinin (5s) altında — gerçek bir
        idle/arm-öncesi ArduPilot logunda (9 örnek, ~1.6s) yanlışlıkla motor
        dengesizliği uyarısı üretilmesiyle keşfedilen sorunun regresyon testi."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(2.0, 0, 12.0, 10.0)  # ~%29 düşüş ama sadece 2s arayla
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

    def test_short_duration_does_not_trigger_warning(self):
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(self._generate_short_but_large_sag_log())
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(data["warnings"], [])
        finally:
            path.unlink(missing_ok=True)


class RealPx4LogParsingBugTests(unittest.TestCase):
    """PX4'ün herkese açık flight review veritabanından indirilen gerçek (60-100MB'lık,
    bu yüzden repoya eklenmeyen) uçuş loglarıyla kalibrasyon denenirken bulunan iki
    gerçek ayrıştırma hatasının regresyon testleri. İkisi de düşük seviyeli
    make_synthetic_ulog yapı taşlarıyla kurulan minimal, tekrar üretilebilir fixture'lar.
    """

    def test_trailing_padding_field_not_counted_in_message_size(self):
        """Gerçek loglarda PX4'ün ULog logger'ı, bir mesajın SON alanı salt
        hizalama için eklenmiş bir "_padding..." alanıysa bu baytları diske hiç
        yazmıyor (battery_status'ta 168 bayt hesaplanırken gerçek veri 167 bayttı).
        Bunu bilmeyen bir formatTotalSize(), boyut doğrulamasını her zaman
        başarısız sayıp veriyi hiç okumuyordu."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;uint8_t _padding0;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)

        # D mesajının gerçek verisi, "_padding0" alanı hiç yazılmamış gibi 1 bayt
        # kısa: msg_id(2) + timestamp(8) + voltage_v(4) + current_a(4) = 18 bayt,
        # naif alan toplamı (padding dahil) 19 bayt olurdu.
        timestamp_us = int(2.0 * 1e6)
        payload = struct.pack("<H", 1) + struct.pack("<Qff", timestamp_us, 16.5, 12.0)
        out += make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            battery = data["batteries"][0]
            self.assertAlmostEqual(battery["voltage_v"][0], 16.5, places=3)
            self.assertAlmostEqual(battery["current_a"][0], 12.0, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_esc_online_flags_used_when_esc_count_is_zero(self):
        """Gerçek loglarda bazı araçlar "esc_count" alanını hiç kullanmıyor (hep 0
        bırakıyor), gerçekte bağlı ESC'leri "esc_online_flags" bitmask'inde
        işaretliyor. Eskiden sadece esc_count'a güvenilmesi, dolu bir ESC dizisini
        "0 motor" olarak yorumlatıyordu. Burada esc_count=0 ama esc_online_flags
        sadece 0. ve 2. indeksleri (0b0101=5) işaretliyor; sadece o iki motorun
        çıkması, 1. ve 3. indekslerin (bağlı değil) hiç görünmemesi bekleniyor."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        # Backend en az bir batarya örneği bulunmasını şart koşuyor (aksi halde
        # hata döndürüyor); bu testin odağı olmadığı için minimal bir tane ekleniyor.
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_format_message(
            "esc_status:uint64_t timestamp;uint8_t esc_count;uint8_t esc_online_flags;esc_report[4] esc;"
        )
        out += make_synthetic_ulog.build_format_message(
            "esc_report:uint64_t timestamp;float esc_current;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_subscription_message(2, "esc_status", multi_id=0)
        out += make_synthetic_ulog.build_battery_data_message(1, 3.0, 16.5, 12.0)

        timestamp_us = int(3.0 * 1e6)
        currents = [10.0, 20.0, 30.0, 40.0]  # sadece indeks 0 (id=1) ve 2 (id=3) "bağlı"
        payload = struct.pack("<H", 2) + struct.pack("<Q", timestamp_us)
        payload += struct.pack("<B", 0)      # esc_count = 0 (bu araçta kullanılmıyor)
        payload += struct.pack("<B", 0b0101)  # esc_online_flags: sadece bit0 ve bit2
        for curr in currents:
            payload += struct.pack("<Qf", timestamp_us, curr)
        out += make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            motor_ids = sorted(m["id"] for m in data["motors"])
            self.assertEqual(motor_ids, [1, 3])
            self.assertAlmostEqual(motor_by_id(data, 1)["current_a"][0], 10.0, places=3)
            self.assertAlmostEqual(motor_by_id(data, 3)["current_a"][0], 30.0, places=3)
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
        # Bu log sadece ~1.6 saniyelik arm-öncesi/idle veri içeriyor; kalibrasyon
        # sırasında motorlar arasında (anlamsız) %59 dengesizlik uyarısı ürettiği
        # görüldü. MIN_DURATION_FOR_WARNINGS_S eklenmesinin regresyon testi.
        self.assertEqual(data["warnings"], [])

    def test_px4_real_multi_battery(self):
        data = run_backend(DATA_DIR / "px4_sample_log_small.ulg")
        self.assertEqual(data["meta"]["format"], "px4")
        self.assertEqual(len(data["batteries"]), 2)  # bu logda gercekten 2 farkli guc kaynagi var
        self.assertEqual(len(data["motors"]), 0)  # bu logda esc_status hic loglanmamis
        self.assertIsInstance(data["warnings"], list)


if __name__ == "__main__":
    unittest.main()
