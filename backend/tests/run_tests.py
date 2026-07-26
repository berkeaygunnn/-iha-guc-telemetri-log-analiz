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
    """Windows'ta .exe uzantılı, Linux CI'da uzantısız olabileceği için ikisini de dener.
    CMake'in Windows'ta varsayılan ürettiği Visual Studio (MSVC, multi-config)
    generator'ı çıktıyı build/ kökü yerine build/Release/ (ya da build/Debug/)
    altına koyar; bu yüzden üç klasör de sırayla denenir."""
    build_dir = BACKEND_DIR / "build"
    for directory in (build_dir, build_dir / "Release", build_dir / "Debug"):
        for name in ("power_log_backend.exe", "power_log_backend"):
            candidate = directory / name
            if candidate.exists():
                return candidate
    raise FileNotFoundError(
        f"Backend derlenmemiş: {build_dir} altında power_log_backend bulunamadı. "
        "Önce 'cmake --build build' ile derleyin."
    )


def run_backend(input_path: Path, extra_args: list = None) -> dict:
    """Backend'i verilen log dosyasıyla çalıştırıp ürettiği JSON'u döner.
    extra_args, uyarı eşiklerini override eden --voltage-sag=... gibi
    opsiyonel CLI flag'leri geçirmek için (bkz. CapacityAndRemainingTests'e
    paralel eşik override testleri)."""
    backend_exe = find_backend_exe()
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "output.json"
        result = subprocess.run(
            [str(backend_exe), str(input_path), str(output_path), *(extra_args or [])],
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
        # düşümü içeriyor (eşik %15), yani voltaj uyarısı beklenmiyor. Ama
        # batarya2'nin akımı batarya1'in sabit %60'ı olduğu için (bkz.
        # generate()) ortalama akımları da genel ortalamadan %25 sapıyor —
        # batarya-batarya dengesizlik kuralı (motor kuralının eşleniği) bunu
        # da yakalamalı.
        warnings = data["warnings"]
        self.assertEqual(len(warnings), 4)
        self.assertTrue(any("Motor 1" in w for w in warnings))
        self.assertTrue(any("Motor 4" in w for w in warnings))
        self.assertTrue(any("Batarya 1" in w for w in warnings))
        self.assertTrue(any("Batarya 2" in w for w in warnings))

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


class UntestedCodePathTests(unittest.TestCase):
    """Kod var olan ama şu ana kadar hiç test edilmemiş yollar: ArduPilot'ta
    CURR-only (BAT mesajı hiç yok) loglar, hiç batarya içermeyen (sadece ESC)
    loglar ve PX4'te tek bataryalı loglar. Üçü de mevcut sentetik yardımcı
    fonksiyonlarla kuruluyor, yeni bir altyapı gerekmiyor."""

    CURR_TYPE = 102

    def test_ardupilot_curr_only_log(self):
        """extractBatterySample hem 'BAT' hem 'CURR' adlı mesajları kabul
        ediyor (main.cpp) ama şu ana kadar sadece BAT test edilmişti."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            self.CURR_TYPE, "CURR", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += bytes([make_synthetic_log.HEAD1, make_synthetic_log.HEAD2, self.CURR_TYPE])
        out += struct.pack("<QBff", int(1.0 * 1e6), 0, 16.8, 12.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            self.assertAlmostEqual(data["batteries"][0]["voltage_v"][0], 16.8, places=3)
            self.assertAlmostEqual(data["batteries"][0]["current_a"][0], 12.0, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_ardupilot_esc_only_no_battery_fails_with_clear_error(self):
        """Sadece ESC mesajı içeren (hiç BAT/CURR yok) bir log, batarya
        verisi olmadığı için net bir hatayla başarısız olmalı (main.cpp:843
        yolunun regresyon testi — şu ana kadar hiç tetiklenmemişti)."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.ESC_TYPE, "ESC", "QBf", "TimeUS,Instance,Curr"
        )
        out += make_synthetic_log.build_esc_message(1.0, 0, 15.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            with self.assertRaises(RuntimeError):
                run_backend(path)
        finally:
            path.unlink(missing_ok=True)

    def test_px4_single_battery_only(self):
        """Şu ana kadarki PX4 testleri hep 2 bataryalı fixture'lar kullanıyordu
        (gerçek log + sentetik profil); tek bataryalı en basit durum hiç
        ayrıca test edilmemişti."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_battery_data_message(1, 1.0, 16.8, 10.0)
        out += make_synthetic_ulog.build_battery_data_message(1, 2.0, 16.5, 11.0)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            self.assertEqual(data["batteries"][0]["id"], 1)
            self.assertEqual(len(data["batteries"][0]["time_s"]), 2)
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

    def test_negative_current_data_quality_warning_triggers(self):
        """Fiziksel olarak batarya/ESC akımı negatif olmaz; belirgin şekilde
        negatif bir örnek (ör. -0.7A) 'veri kalitesi' uyarısı üretmeli. Voltaj
        sabit tutuluyor (sag uyarısı karışmasın diye), tek batarya var
        (dengesizlik kuralı devreye girmesin diye)."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, -0.7)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            warnings = data["warnings"]
            self.assertEqual(len(warnings), 1)
            self.assertIn("Batarya 1", warnings[0])
            self.assertIn("-0.7", warnings[0])
        finally:
            path.unlink(missing_ok=True)

    def test_small_negative_current_noise_does_not_trigger(self):
        """Eşiğin (-0.1A) hemen üstünde kalan ufak bir negatif değer (-0.05A)
        saf sensör gürültüsü sayılıp uyarı üretmemeli."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, -0.05)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(data["warnings"], [])
        finally:
            path.unlink(missing_ok=True)

    def test_battery_current_imbalance_warning_triggers(self):
        """Motor akım dengesizliği kuralının batarya karşılığı: iki bataryanın
        ortalama akımı genel ortalamadan %20+ sapıyorsa ikisi için de ayrı
        uyarı üretilmeli. Voltaj sabit tutuluyor ki sag uyarısı karışmasın."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        # Batarya 1 ortalama 20A, batarya 2 ortalama 5A -> genel ortalama
        # 12.5A; her ikisi de bu ortalamadan %60 sapıyor (eşik %20).
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 20.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, 20.0)
        out += make_synthetic_log.build_bat_message(0.0, 1, 16.8, 5.0)
        out += make_synthetic_log.build_bat_message(6.0, 1, 16.8, 5.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            warnings = data["warnings"]
            self.assertEqual(len(warnings), 2)
            self.assertTrue(any("Batarya 1" in w for w in warnings))
            self.assertTrue(any("Batarya 2" in w for w in warnings))
        finally:
            path.unlink(missing_ok=True)


class WarningThresholdOverrideTests(unittest.TestCase):
    """Kullanıcının Ayarlar penceresinden değiştirebileceği --voltage-sag=/
    --current-imbalance=/--negative-current= CLI argümanlarının (main.cpp
    main()) computeWarnings'e doğru ulaştığını doğrular: aynı fixture,
    override VERİLMEDEN uyarı üretmemeli, override ile (varsayılandan daha
    sıkı bir eşikle) üretmeli."""

    def test_custom_voltage_sag_threshold_overrides_default(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 15.5, 10.0)  # ~%7.7 düşüş: varsayılan %15 eşiğin altında

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["warnings"], [])  # varsayılanla (%15) tetiklenmemeli
            overridden = run_backend(path, ["--voltage-sag=0.05"])["warnings"]
            self.assertEqual(len(overridden), 1)
            self.assertIn("Batarya 1", overridden[0])
        finally:
            path.unlink(missing_ok=True)

    def test_custom_current_imbalance_threshold_overrides_default(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        # Batarya 1 ortalama 11A, batarya 2 ortalama 9A -> genel ortalama 10A;
        # her ikisi de ortalamadan sadece %10 sapıyor (varsayılan eşik %20).
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 11.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, 11.0)
        out += make_synthetic_log.build_bat_message(0.0, 1, 16.8, 9.0)
        out += make_synthetic_log.build_bat_message(6.0, 1, 16.8, 9.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["warnings"], [])
            overridden = run_backend(path, ["--current-imbalance=0.05"])["warnings"]
            self.assertEqual(len(overridden), 2)
        finally:
            path.unlink(missing_ok=True)

    def test_custom_negative_current_threshold_overrides_default(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, -0.05)  # varsayılan eşiğin (-0.1A) ÜSTÜNDE kalır

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["warnings"], [])
            overridden = run_backend(path, ["--negative-current=-0.01"])["warnings"]
            self.assertEqual(len(overridden), 1)
            self.assertIn("Batarya 1", overridden[0])
        finally:
            path.unlink(missing_ok=True)


class VehicleSpecificThresholdTests(unittest.TestCase):
    """--vehicle-thresholds=<tip>:<sag>:<dengesizlik>:<negatif> araç tipine
    özel eşikler tanımlar. Eşikler backend'e çağrı ANINDA veriliyor ama araç
    tipi ancak log ayrıştırıldıktan sonra biliniyor; bu yüzden frontend tüm
    tiplerin eşiklerini birden geçiriyor ve doğru olan burada seçiliyor
    (bkz. selectThresholds). Eşleşen tip yoksa genel eşikler geçerli."""

    def _two_battery_imbalance_log(self) -> bytes:
        """Batarya 1 ortalama 11A, batarya 2 ortalama 9A -> genel ortalamadan
        %10 sapma: varsayılan %20 eşiğini geçmez, %5'lik bir eşiği geçer.
        Voltaj sabit tutuluyor ki sag uyarısı karışmasın."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 11.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, 11.0)
        out += make_synthetic_log.build_bat_message(0.0, 1, 16.8, 9.0)
        out += make_synthetic_log.build_bat_message(6.0, 1, 16.8, 9.0)
        return bytes(out)

    def test_matching_vehicle_type_thresholds_are_applied(self):
        """Gerçek bir multirotor logunda, multirotor için tanımlanan sıkı
        eşik devreye girmeli."""
        default_warnings = run_backend(DATA_DIR / "px4_hexarotor_flight.ulg")["warnings"]
        strict = run_backend(
            DATA_DIR / "px4_hexarotor_flight.ulg",
            ["--vehicle-thresholds=multirotor:0.15:0.05:-0.1"],
        )["warnings"]
        self.assertGreater(len(strict), len(default_warnings))

    def test_non_matching_vehicle_type_is_ignored(self):
        """Aynı sıkı eşik BAŞKA bir araç tipine tanımlıysa etkisiz kalmalı —
        özelliğin özü bu ayrım."""
        default_warnings = run_backend(DATA_DIR / "px4_hexarotor_flight.ulg")["warnings"]
        other = run_backend(
            DATA_DIR / "px4_hexarotor_flight.ulg",
            ["--vehicle-thresholds=rover:0.15:0.05:-0.1"],
        )["warnings"]
        self.assertEqual(len(other), len(default_warnings))

    def test_vehicle_specific_overrides_general_flag(self):
        """Hem genel --current-imbalance hem de araç tipine özel eşik varsa,
        araç tipine özel olan kazanmalı (daha dar kapsamlı olan)."""
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(self._two_battery_imbalance_log())
            path = Path(f.name)
        try:
            # Sentetik ArduPilot logunda MSG yok -> vehicle_type "unknown".
            self.assertEqual(run_backend(path)["meta"]["vehicle_type"], "unknown")
            # Genel eşik gevşek (%50) ama "unknown" tipine sıkı (%5) tanımlı.
            warnings = run_backend(path, [
                "--current-imbalance=0.50",
                "--vehicle-thresholds=unknown:0.15:0.05:-0.1",
            ])["warnings"]
            self.assertEqual(len(warnings), 2)
        finally:
            path.unlink(missing_ok=True)

    def test_general_flag_used_when_no_vehicle_entry_matches(self):
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(self._two_battery_imbalance_log())
            path = Path(f.name)
        try:
            warnings = run_backend(path, [
                "--current-imbalance=0.05",
                "--vehicle-thresholds=rover:0.15:0.50:-0.1",  # log "unknown", eslesmez
            ])["warnings"]
            self.assertEqual(len(warnings), 2)  # genel siki esik gecerli
        finally:
            path.unlink(missing_ok=True)

    def test_malformed_vehicle_threshold_arg_is_ignored(self):
        """Eksik alan/sayıya çevrilemeyen değer, diğer --flag'lerdeki gibi
        sessizce yok sayılmalı; backend çökmemeli."""
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(self._two_battery_imbalance_log())
            path = Path(f.name)
        try:
            for bad in ("--vehicle-thresholds=rover:0.15",
                        "--vehicle-thresholds=rover:a:b:c",
                        "--vehicle-thresholds=",
                        "--vehicle-thresholds=:0.1:0.1:-0.1"):
                self.assertEqual(run_backend(path, [bad])["warnings"], [])
        finally:
            path.unlink(missing_ok=True)


class CapacityAndRemainingTests(unittest.TestCase):
    """CurrTot/RemPct (ArduPilot) ve discharged_mah/remaining (PX4) opsiyonel
    kapasite/kalan-yüzde alanlarının doğru parse edildiğini, bu alanlar logda
    yoksa (eski BAT/battery_status tanımı) null döndüğünü doğrular."""

    def test_ardupilot_capacity_and_remaining_parsed_when_present(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBfffB", "TimeUS,Inst,Volt,Curr,CurrTot,RemPct"
        )
        header = bytes([make_synthetic_log.HEAD1, make_synthetic_log.HEAD2, make_synthetic_log.BAT_TYPE])
        out += header + struct.pack("<QBfffB", int(0.0 * 1e6), 0, 16.8, 10.0, 100.0, 90)
        out += header + struct.pack("<QBfffB", int(6.0 * 1e6), 0, 16.5, 10.0, 250.0, 75)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            battery = battery_by_id(data, 1)
            # Son örnekte görülen değerler kalmalı (kümülatif tüketim/kalan yüzde).
            self.assertEqual(battery["capacity_used_mah"], 250.0)
            self.assertEqual(battery["remaining_pct"], 75)
        finally:
            path.unlink(missing_ok=True)

    def test_ardupilot_capacity_fields_null_when_absent(self):
        """CurrTot/RemPct alanı olmayan (eski) bir BAT tanımında bu iki alan
        JSON'da null olmalı — regresyon testi, mevcut davranış bozulmasın."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.5, 10.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            battery = battery_by_id(data, 1)
            self.assertIsNone(battery["capacity_used_mah"])
            self.assertIsNone(battery["remaining_pct"])
        finally:
            path.unlink(missing_ok=True)

    def test_px4_capacity_and_remaining_parsed_when_present(self):
        def build_sample(msg_id, t, volt, curr, discharged, remaining):
            timestamp_us = int(t * 1e6)
            payload = struct.pack("<H", msg_id) + struct.pack(
                "<Qffff", timestamp_us, volt, curr, discharged, remaining
            )
            return make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
            "float discharged_mah;float remaining;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += build_sample(1, 0.0, 16.8, 10.0, 100.0, 0.9)
        out += build_sample(1, 6.0, 16.5, 10.0, 250.0, 0.75)

        with tempfile.NamedTemporaryFile(suffix=".ulg", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            battery = battery_by_id(data, 1)
            self.assertEqual(battery["capacity_used_mah"], 250.0)
            self.assertAlmostEqual(battery["remaining_pct"], 75.0)
        finally:
            path.unlink(missing_ok=True)

    def test_px4_capacity_fields_null_when_absent(self):
        """discharged_mah/remaining alanı olmayan (eski) bir battery_status
        tanımında bu iki alan JSON'da null olmalı."""
        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(make_synthetic_ulog.generate())
            path = Path(f.name)
        try:
            data = run_backend(path)
            battery = battery_by_id(data, 1)
            self.assertIsNone(battery["capacity_used_mah"])
            self.assertIsNone(battery["remaining_pct"])
        finally:
            path.unlink(missing_ok=True)


class BatteryTemperatureTests(unittest.TestCase):
    """ArduPilot BAT'ın 'Temp' (int16, santi-derece) ve PX4 battery_status'ün
    'temperature' (float, °C) alanlarının doğru parse edildiğini, bu alanlar
    logda yoksa boş dizi döndüğünü doğrular (bkz. shared/power_log_schema.md
    temperature_c)."""

    def test_ardupilot_temperature_parsed_when_present(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBffc", "TimeUS,Inst,Volt,Curr,Temp"
        )
        header = bytes([make_synthetic_log.HEAD1, make_synthetic_log.HEAD2, make_synthetic_log.BAT_TYPE])
        out += header + struct.pack("<QBffh", int(0.0 * 1e6), 0, 16.8, 10.0, 2550)  # 25.50 C
        out += header + struct.pack("<QBffh", int(6.0 * 1e6), 0, 16.5, 10.0, 2600)  # 26.00 C

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            battery = battery_by_id(run_backend(path), 1)
            self.assertEqual(len(battery["temperature_c"]), 2)
            self.assertAlmostEqual(battery["temperature_c"][0], 25.5, places=3)
            self.assertAlmostEqual(battery["temperature_c"][1], 26.0, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_ardupilot_temperature_empty_when_absent(self):
        """Temp alanı olmayan (eski) bir BAT tanımında temperature_c boş
        dizi olmalı — regresyon testi, mevcut davranış bozulmasın."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 10.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.5, 10.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(battery_by_id(run_backend(path), 1)["temperature_c"], [])
        finally:
            path.unlink(missing_ok=True)

    def test_px4_temperature_parsed_when_present(self):
        def build_sample(msg_id, t, volt, curr, temp):
            timestamp_us = int(t * 1e6)
            payload = struct.pack("<H", msg_id) + struct.pack("<Qfff", timestamp_us, volt, curr, temp)
            return make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;float temperature;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += build_sample(1, 0.0, 16.8, 10.0, 37.2)
        out += build_sample(1, 6.0, 16.5, 10.0, 37.5)

        with tempfile.NamedTemporaryFile(suffix=".ulg", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            battery = battery_by_id(run_backend(path), 1)
            self.assertEqual(len(battery["temperature_c"]), 2)
            self.assertAlmostEqual(battery["temperature_c"][0], 37.2, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_px4_temperature_empty_when_absent(self):
        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(make_synthetic_ulog.generate())
            path = Path(f.name)
        try:
            self.assertEqual(battery_by_id(run_backend(path), 1)["temperature_c"], [])
        finally:
            path.unlink(missing_ok=True)


class ArduPilotRobustnessTests(unittest.TestCase):
    """Bozuk/tutarsiz ArduPilot .bin dosyalari icin savunma testleri.

    Gercek bir SD karti bozulmasi/yarim yazilmis dosya, bir FMT mesajinin
    'length' baytiyla 'format' string'ini (bit hatasi yuzunden) tutarsiz
    birakabilir: format Volt/Curr gibi alanlar oldugunu soylerken, length o
    alanlara yer olmadigini soyleyebilir. locateField bu durumda hesapladigi
    byte offset'in mesajin gercek payload boyutunu astigini kontrol etmezse,
    buffer sinirlari disinda (heap-buffer-overflow) okuma riski oluşurdu."""

    def test_inconsistent_fmt_length_sample_is_skipped_not_crashed(self):
        fmt_msg = bytearray(make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        ))
        fmt_msg[4] = 3 + 8 + 1  # length bayti: sadece TimeUS+Inst'e yetiyor (Volt/Curr icin yer yok)

        out = bytearray()
        out += bytes(fmt_msg)
        out += bytes([make_synthetic_log.HEAD1, make_synthetic_log.HEAD2, make_synthetic_log.BAT_TYPE])
        out += struct.pack("<QB", int(1.0 * 1e6), 0)  # bozuk FMT'ye uyan, kisa (Volt/Curr'suz) ornek

        # FMT duzeltiliyor ve ardindan gecerli, tam bir ornek geliyor; parser'in
        # bozuk ornekten sonra da senkronize kalip dogru calismaya devam ettigini
        # (crash olmadigini, sadece bozuk orneği atladigini) kanitlamak icin.
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(2.0, 0, 16.8, 12.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            battery = data["batteries"][0]
            self.assertEqual(len(battery["time_s"]), 1)  # sadece bozuk-olmayan ornek sayildi
            self.assertAlmostEqual(battery["voltage_v"][0], 16.8, places=3)
            self.assertAlmostEqual(battery["current_a"][0], 12.0, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_nan_and_infinity_samples_are_skipped(self):
        """Bozuk bir dosyada okunan baytlar bir NaN/Infinity float'a denk
        gelebilir; bu JSON'a yazilamaz (gecerli JSON'da NaN/Infinity yok).
        extractBatterySample boyle bir ornegi sessizce atlamali, geri kalan
        gecerli ornekleri etkilememeli."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, float("nan"), 10.0)
        out += make_synthetic_log.build_bat_message(1.0, 0, float("inf"), 10.0)
        out += make_synthetic_log.build_bat_message(2.0, 0, 16.8, 12.0)  # gecerli

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            battery = data["batteries"][0]
            self.assertEqual(len(battery["time_s"]), 1)
            self.assertAlmostEqual(battery["voltage_v"][0], 16.8, places=3)
            self.assertAlmostEqual(battery["current_a"][0], 12.0, places=3)
        finally:
            path.unlink(missing_ok=True)


class MalformedInputFileTests(unittest.TestCase):
    """Log olmayan/bozuk dosyalarda backend'in cokmeden, net bir hatayla
    (RuntimeError -> hatali cikis kodu) basarisiz oldugunu dogrular."""

    def test_nonexistent_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = Path(tmp_dir) / "olmayan_dosya.bin"
            with self.assertRaises(RuntimeError):
                run_backend(missing_path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            path = Path(f.name)
        try:
            with self.assertRaises(RuntimeError):
                run_backend(path)
        finally:
            path.unlink(missing_ok=True)

    def test_random_garbage_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytes((i * 37 + 11) % 256 for i in range(500)))
            path = Path(f.name)
        try:
            with self.assertRaises(RuntimeError):
                run_backend(path)
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

    def test_nan_and_infinity_samples_are_skipped(self):
        """ArduPilot tarafindakiyle ayni kural PX4 icin de gecerli: bozuk bir
        dosyada NaN/Infinity'ye denk gelen bir ornek, JSON'a yazilamayacagi
        icin sessizce atlanmali."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_battery_data_message(1, 0.0, float("nan"), 10.0)
        out += make_synthetic_ulog.build_battery_data_message(1, 1.0, float("inf"), 10.0)
        out += make_synthetic_ulog.build_battery_data_message(1, 2.0, 16.5, 12.0)  # gecerli

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(len(data["batteries"]), 1)
            battery = data["batteries"][0]
            self.assertEqual(len(battery["time_s"]), 1)
            self.assertAlmostEqual(battery["voltage_v"][0], 16.5, places=3)
            self.assertAlmostEqual(battery["current_a"][0], 12.0, places=3)
        finally:
            path.unlink(missing_ok=True)

    def test_system_power_only_gives_specific_error(self):
        """Bazı Rover yapılandırmaları hiç 'battery_status' loglamıyor, sadece
        dahili güç hatlarını raporlayan 'system_power'ı kullanıyor (gerçek bir
        PX4 flight review logunda bulundu). system_power'daki alanlar ana
        batarya voltajı DEĞİL; bu yüzden veri olarak kullanmıyoruz ama en
        azından kullanıcıya "desteklenmeyen/bozuk log" yerine daha isabetli bir
        hata mesajı veriyoruz."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "system_power:uint64_t timestamp;float voltage5v_v;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "system_power", multi_id=0)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                run_backend(path)
            self.assertIn("system_power", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)


class PX4RobustnessTests(unittest.TestCase):
    """ArduPilotRobustnessTests'in PX4 karşılığı: bozuk/tutarsız bir Format (F)
    mesajı karşısında parser'ın çökmeden, veri kirletmeden güvenli şekilde
    davrandığını doğrular. locateUlogField, bir alanın adı yoksa ya da
    kendisinden önceki bir alanın boyutu çözülemiyorsa (bit hatasıyla bozulmuş
    bir alan tanımı gibi) o alanı 'bulunamadı' sayar; extractUlogBatterySample
    bu durumda örneği sessizce atlamalı, buffer sınırları dışına taşmamalı."""

    def test_malformed_field_spec_before_target_field_is_skipped_not_crashed(self):
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        # "bosluksuzalan" bosluk icermiyor (parseUlogField boyle bir spec'i
        # tip/isim olarak ayiramaz), bu yuzden boyutu cozulemez (size=0) ve
        # ondan SONRAKI alanlarin (voltage_v/current_a) offset'i guvenilmez
        # sayilip bulunamadi olarak isaretlenir - bit hatasiyla bozulmus bir
        # FMT metnini simule ediyor.
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;bosluksuzalan;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_battery_data_message(1, 1.0, 16.5, 12.0)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            with self.assertRaises(RuntimeError):
                run_backend(path)  # voltage_v/current_a bulunamadigi icin hic ornek cikarilamaz
        finally:
            path.unlink(missing_ok=True)


class VehicleTypeTests(unittest.TestCase):
    """meta.vehicle_type alanı: PX4'te vehicle_status.vehicle_type sayısal
    alanından, ArduPilot'ta ise açılışta yazılan "MSG" satırlarındaki firmware
    adından ("ArduCopter V4.x") çıkarılır. Log bu bilgiyi hiç içermiyorsa
    "unknown" kalır (bkz. shared/power_log_schema.md).

    Bu alan SADECE bilgi amaçlıdır; hangi panellerin çizileceğini belirlemez
    (o karar has_current_data'ya bakar) — çünkü aynı araç tipinin ESC
    telemetrisi olan da olmayan da oluyor."""

    def test_px4_rover_detected(self):
        data = run_backend(DATA_DIR / "px4_ground_rover_flight.ulg")
        self.assertEqual(data["meta"]["vehicle_type"], "rover")

    def test_px4_fixed_wing_detected(self):
        data = run_backend(DATA_DIR / "px4_fixed_wing_flight.ulg")
        self.assertEqual(data["meta"]["vehicle_type"], "fixed_wing")

    def test_px4_multirotor_detected(self):
        data = run_backend(DATA_DIR / "px4_hexarotor_flight.ulg")
        self.assertEqual(data["meta"]["vehicle_type"], "multirotor")

    def test_px4_vtol_takes_precedence_over_rotary_wing(self):
        """VTOL araçlar uçuş fazına göre vehicle_type'ı rotary_wing ile
        fixed_wing arasında değiştirir; ayrı "is_vtol" bayrağı olduğu için
        etiket sabit "vtol" olmalı (bu log is_vtol=1, vehicle_type=1 taşıyor)."""
        data = run_backend(DATA_DIR / "px4_sample_log_small.ulg")
        self.assertEqual(data["meta"]["vehicle_type"], "vtol")

    def test_ardupilot_multirotor_from_firmware_message(self):
        data = run_backend(DATA_DIR / "ArduCopter-MaxAltFence-00000067.BIN")
        self.assertEqual(data["meta"]["vehicle_type"], "multirotor")

    def test_unknown_when_log_has_no_vehicle_info(self):
        """Sentetik test dosyalarında ne MSG ne vehicle_status var; alan
        uydurulmamalı, "unknown" kalmalı."""
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(make_synthetic_log.generate())
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["meta"]["vehicle_type"], "unknown")
        finally:
            path.unlink(missing_ok=True)

    def test_ardupilot_rover_firmware_name_recognized(self):
        """Repoda gerçek bir ArduRover .bin örneği yok; firmware adından tip
        çıkarma mantığı sentetik bir MSG mesajıyla test ediliyor. 'Z' formatı
        char[64] olduğu için metin 64 bayta yastıklanır."""
        MSG_TYPE = 103
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(MSG_TYPE, "MSG", "QZ", "TimeUS,Message")
        out += bytes([make_synthetic_log.HEAD1, make_synthetic_log.HEAD2, MSG_TYPE])
        out += struct.pack("<Q", int(0.5 * 1e6)) + b"ArduRover V4.4.0".ljust(64, b"\x00")
        # Backend en az bir batarya örneği bulunmasını şart koşuyor.
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(1.0, 0, 12.6, 5.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["meta"]["vehicle_type"], "rover")
        finally:
            path.unlink(missing_ok=True)


class HasCurrentDataTests(unittest.TestCase):
    """has_current_data bayrağı: akım sensörü bağlı değilken ArduPilot/PX4 bu
    alanı boş bırakmaz, her örneğe TAM 0.0 yazar. Bu durumu "akım gerçekten
    0 A" ile karıştırmamak için backend ayrı bir bayrak üretiyor — yoksa
    frontend düz bir sıfır çizgisi, 0.0 Wh enerji ve 0 W tepe güç gösteriyordu
    (gerçek bir rover logunda görüldü, bkz. shared/power_log_schema.md)."""

    def test_real_rover_log_has_no_current_data(self):
        """data/px4_ground_rover_flight.ulg'de battery_status'ün current_a
        alanı 452 örneğin hepsinde tam 0.00 — bu araçta akım sensörü yok."""
        data = run_backend(DATA_DIR / "px4_ground_rover_flight.ulg")
        battery = battery_by_id(data, 1)
        self.assertFalse(battery["has_current_data"])
        # Voltaj ölçümü ise gerçek; bayrak akımı işaretler, bataryayı değil.
        self.assertGreater(min(battery["voltage_v"]), 10.0)

    def test_real_multirotor_log_has_current_data(self):
        data = run_backend(DATA_DIR / "px4_hexarotor_flight.ulg")
        self.assertTrue(battery_by_id(data, 1)["has_current_data"])
        self.assertTrue(motor_by_id(data, 1)["has_current_data"])

    def test_all_zero_current_battery_flagged(self):
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 0.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.5, 0.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertFalse(battery_by_id(run_backend(path), 1)["has_current_data"])
        finally:
            path.unlink(missing_ok=True)

    def test_single_nonzero_sample_counts_as_real_data(self):
        """Eşik yok, kural katı: bir tek örnek bile sıfırdan farklıysa sensör
        var sayılır. Gerçek bir sensör araç dururken bile küçük bir gürültü/
        ofset üretir, tam sıfır dizisi ancak sensör yokken oluşur."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 0.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.5, 0.02)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertTrue(battery_by_id(run_backend(path), 1)["has_current_data"])
        finally:
            path.unlink(missing_ok=True)

    def test_zero_current_battery_excluded_from_imbalance_rule(self):
        """Bu, bayrağın düzelttiği somut yanlış alarmın regresyon testi: biri
        gerçek ölçüm yapan, diğeri akım sensörsüz iki batarya. Sensörsüz olan
        ortalamalara katılırsa genel ortalama 20A'dan 10A'ya iner ve İKİSİ
        birden "%100 sapma" uyarısı üretir — halbuki sensörsüz olanın ölçümü
        hiç yok. Voltaj sabit tutuluyor ki sag uyarısı karışmasın."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        out += make_synthetic_log.build_bat_message(0.0, 0, 16.8, 20.0)
        out += make_synthetic_log.build_bat_message(6.0, 0, 16.8, 20.0)
        out += make_synthetic_log.build_bat_message(0.0, 1, 16.8, 0.0)
        out += make_synthetic_log.build_bat_message(6.0, 1, 16.8, 0.0)

        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            self.assertEqual(data["warnings"], [])
            self.assertTrue(battery_by_id(data, 1)["has_current_data"])
            self.assertFalse(battery_by_id(data, 2)["has_current_data"])
        finally:
            path.unlink(missing_ok=True)


class PwmOutputTests(unittest.TestCase):
    """pwm_outputs dizisi: uçuş kontrolcüsünün çıkış kanallarının PWM darbe
    genişliği (PX4'te "actuator_outputs", ArduPilot'ta "RCOU"). Bu bir GÜÇ
    ölçümü değil, kontrol çıktısı — akım sensörü olmayan araçlarda (birçok
    rover) motor aktivitesinin tek görünür kanıtı.

    İki tasarım kuralı test ediliyor (bkz. shared/power_log_schema.md):
    - Hiç değişmeyen kanallar (kullanılmayan çıkış, servo nötr konumu) JSON'a
      hiç yazılmaz.
    - Hangi kanalın motor hangisinin servo olduğu loglarda yazmadığı için
      tahmin yürütülmez; kanallar donanım adlarıyla (MAIN/AUX/Kanal) aktarılır.
    """

    def pwm_labels(self, data: dict) -> list:
        return [output["label"] for output in data["pwm_outputs"]]

    def test_rover_constant_channels_are_dropped(self):
        """Rover'ın 4 çıkış kanalından sadece 2'si hareketli (gaz +
        direksiyon); kalan ikisi log boyunca sabit 1500 (servo nötr) ve
        listede hiç görünmemeli."""
        data = run_backend(DATA_DIR / "px4_ground_rover_flight.ulg")
        self.assertEqual(self.pwm_labels(data), ["MAIN 2", "MAIN 4"])

        channel = data["pwm_outputs"][0]
        self.assertEqual(len(channel["time_s"]), len(channel["pwm_us"]))
        self.assertGreater(max(channel["pwm_us"]), min(channel["pwm_us"]))

    def test_hexarotor_six_motors_found_in_aux_group(self):
        """Bu logda 6 motor MAIN'de değil AUX grubunda; "asıl motor grubu"
        diye bir seçim yapılmadığının, tüm instance'ların aktarıldığının
        kanıtı (MAIN'deki iki hareketli kanal da listede)."""
        labels = self.pwm_labels(run_backend(DATA_DIR / "px4_hexarotor_flight.ulg"))
        for channel in range(1, 7):
            self.assertIn(f"AUX {channel}", labels)
        self.assertIn("MAIN 2", labels)

    def test_ardupilot_rcou_channels_parsed(self):
        """ArduPilot'ta kanallar RCOU mesajının C1..C14 alanlarında; bu
        quadrotor logunda 4 motor kanalı hareketli."""
        data = run_backend(DATA_DIR / "ArduCopter-MaxAltFence-00000067.BIN")
        self.assertEqual(self.pwm_labels(data), ["Kanal 1", "Kanal 2", "Kanal 3", "Kanal 4"])
        self.assertEqual(len(data["pwm_outputs"][0]["time_s"]), 959)

    def test_empty_when_log_has_no_output_messages(self):
        """Sentetik log ne RCOU ne actuator_outputs içeriyor; alan
        uydurulmamalı, boş dizi olmalı."""
        with tempfile.NamedTemporaryFile(suffix=".BIN", delete=False) as f:
            f.write(make_synthetic_log.generate())
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["pwm_outputs"], [])
        finally:
            path.unlink(missing_ok=True)

    def test_all_constant_channels_produce_empty_list(self):
        """Tüm kanalları sabit olan bir araç (ör. hiç arm edilmemiş bir log)
        boş bir liste vermeli — sabit filtresinin sınır durumu."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_format_message(
            "actuator_outputs:uint64_t timestamp;uint32_t noutputs;float[4] output;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_subscription_message(2, "actuator_outputs", multi_id=0)
        out += make_synthetic_ulog.build_battery_data_message(1, 1.0, 16.5, 12.0)

        for time_s in (1.0, 2.0):
            payload = struct.pack("<H", 2) + struct.pack("<QI", int(time_s * 1e6), 4)
            payload += struct.pack("<4f", 1500.0, 1500.0, 1000.0, 0.0)  # hepsi sabit
            out += make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            self.assertEqual(run_backend(path)["pwm_outputs"], [])
        finally:
            path.unlink(missing_ok=True)

    def test_noutputs_limits_channels_read(self):
        """"output" sabit uzunluklu bir dizi ama araç daha az kanal kullanır;
        noutputs'un ötesindeki elemanlar (çöp/artık veri) okunmamalı."""
        out = bytearray()
        out += make_synthetic_ulog.build_header()
        out += make_synthetic_ulog.build_flag_bits_message()
        out += make_synthetic_ulog.build_format_message(
            "battery_status:uint64_t timestamp;float voltage_v;float current_a;"
        )
        out += make_synthetic_ulog.build_format_message(
            "actuator_outputs:uint64_t timestamp;uint32_t noutputs;float[4] output;"
        )
        out += make_synthetic_ulog.build_subscription_message(1, "battery_status", multi_id=0)
        out += make_synthetic_ulog.build_subscription_message(2, "actuator_outputs", multi_id=1)
        out += make_synthetic_ulog.build_battery_data_message(1, 1.0, 16.5, 12.0)

        # noutputs=2: sadece ilk iki kanal gerçek. 3. ve 4. de değişiyor ama
        # bildirilmedikleri için okunmamalılar.
        for time_s, values in ((1.0, (1100.0, 1200.0, 1300.0, 1400.0)),
                                (2.0, (1600.0, 1700.0, 1800.0, 1900.0))):
            payload = struct.pack("<H", 2) + struct.pack("<QI", int(time_s * 1e6), 2)
            payload += struct.pack("<4f", *values)
            out += make_synthetic_ulog.build_message(make_synthetic_ulog.MSG_DATA, payload)

        with tempfile.NamedTemporaryFile(suffix=".ulog", delete=False) as f:
            f.write(bytes(out))
            path = Path(f.name)
        try:
            data = run_backend(path)
            # multi_id=1 -> AUX grubu.
            self.assertEqual([o["label"] for o in data["pwm_outputs"]], ["AUX 1", "AUX 2"])
            self.assertEqual(data["pwm_outputs"][0]["pwm_us"], [1100.0, 1600.0])
        finally:
            path.unlink(missing_ok=True)

    def test_pwm_does_not_affect_warning_rules(self):
        """PWM bir güç ölçümü olmadığı için computeWarnings kapsamı dışında;
        gerçek loglarda uyarı sayıları PWM eklendikten sonra değişmemeli."""
        self.assertEqual(run_backend(DATA_DIR / "px4_ground_rover_flight.ulg")["warnings"], [])
        self.assertEqual(len(run_backend(DATA_DIR / "px4_hexarotor_flight.ulg")["warnings"]), 2)


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
