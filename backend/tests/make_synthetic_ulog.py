"""Backend'in PX4 .ulog parser'ını test etmek için sentetik bir log üretir.

ArduPilot tarafında yaptığımızın aynısı: gerçek/rastgele bir .ulog dosyası
bulmaya bağımlı olmadan, sadece Header + Flag Bits + Format + Subscription +
Logged Data mesajlarından oluşan, "battery_status" alanları bilinen değerlerle
değişen küçük bir dosya üretiyoruz.

Kullanım: python make_synthetic_ulog.py <cikti.ulog>
"""

import struct
import sys

MAGIC = bytes([0x55, 0x4C, 0x6F, 0x67, 0x01, 0x12, 0x35])
VERSION = 1

MSG_FLAG_BITS = ord("B")
MSG_FORMAT = ord("F")
MSG_SUBSCRIPTION = ord("A")
MSG_DATA = ord("D")

BATTERY_MSG_ID = 1
ESC_STATUS_MSG_ID = 2
BATTERY2_MSG_ID = 3
MOTOR_COUNT = 4


def build_message(msg_type: int, payload: bytes) -> bytes:
    return struct.pack("<HB", len(payload), msg_type) + payload


def build_header() -> bytes:
    return MAGIC + struct.pack("<B", VERSION) + struct.pack("<Q", 0)


def build_flag_bits_message() -> bytes:
    payload = bytes(8) + bytes(8) + bytes(24)  # compat + incompat + appended_offsets
    return build_message(MSG_FLAG_BITS, payload)


def build_format_message(fmt: str) -> bytes:
    return build_message(MSG_FORMAT, fmt.encode("ascii"))


def build_subscription_message(msg_id: int, name: str, multi_id: int = 0) -> bytes:
    payload = struct.pack("<B", multi_id) + struct.pack("<H", msg_id) + name.encode("ascii")
    return build_message(MSG_SUBSCRIPTION, payload)


def build_battery_data_message(msg_id: int, time_s: float, voltage_v: float, current_a: float) -> bytes:
    timestamp_us = int(time_s * 1e6)
    payload = struct.pack("<H", msg_id) + struct.pack("<Qff", timestamp_us, voltage_v, current_a)
    return build_message(MSG_DATA, payload)


def build_esc_status_data_message(time_s: float, motor_currents: list) -> bytes:
    """esc_status:uint64_t timestamp;uint8_t esc_count;esc_report[N] esc;
    esc_report:uint64_t timestamp;float esc_current;
    """
    timestamp_us = int(time_s * 1e6)
    payload = struct.pack("<H", ESC_STATUS_MSG_ID)
    payload += struct.pack("<Q", timestamp_us)
    payload += struct.pack("<B", len(motor_currents))
    for curr in motor_currents:
        payload += struct.pack("<Qf", timestamp_us, curr)
    return build_message(MSG_DATA, payload)


def build_esc_status_data_message_with_rpm(time_s: float, motor_currents: list, motor_rpms: list) -> bytes:
    """esc_rpm alanı eklenmiş esc_report varyantı:
    esc_report:uint64_t timestamp;float esc_current;float esc_rpm;
    generate()'in varsayılan (RPM'siz) format string'ini bozmamak için bu,
    ayrı bir fonksiyon - kullanan test kendi format/subscription mesajını
    da RPM'li olarak yazmalı."""
    timestamp_us = int(time_s * 1e6)
    payload = struct.pack("<H", ESC_STATUS_MSG_ID)
    payload += struct.pack("<Q", timestamp_us)
    payload += struct.pack("<B", len(motor_currents))
    for curr, rpm in zip(motor_currents, motor_rpms):
        payload += struct.pack("<Qff", timestamp_us, curr, rpm)
    return build_message(MSG_DATA, payload)


def generate() -> bytes:
    out = bytearray()
    out += build_header()
    out += build_flag_bits_message()
    out += build_format_message("battery_status:uint64_t timestamp;float voltage_v;float current_a;")
    out += build_format_message("esc_report:uint64_t timestamp;float esc_current;")
    out += build_format_message(f"esc_status:uint64_t timestamp;uint8_t esc_count;esc_report[{MOTOR_COUNT}] esc;")
    out += build_subscription_message(BATTERY_MSG_ID, "battery_status", multi_id=0)
    out += build_subscription_message(ESC_STATUS_MSG_ID, "esc_status", multi_id=0)
    out += build_subscription_message(BATTERY2_MSG_ID, "battery_status", multi_id=1)

    # 10 saniyelik, akımın yükselip alçaldığı basit bir uçuş profili.
    pack_currents = [5, 10, 15, 20, 25, 30, 25, 20, 15, 10]
    voltages = [16.8, 16.5, 16.2, 15.9, 15.6, 15.3, 15.6, 15.9, 16.2, 16.5]
    # Her motorun payı farklı bir katsayıyla (grafikte satırlar birbirinden
    # ayırt edilebilsin diye); dördü de birbirinden farklı.
    motor_factors = [0.8, 0.95, 1.1, 1.25]

    for t, (volt, pack_curr) in enumerate(zip(voltages, pack_currents)):
        out += build_battery_data_message(BATTERY_MSG_ID, float(t), volt, float(pack_curr))
        # İkinci batarya (multi_id=1): birden fazla batarya gruplamasını test
        # etmek için, birincinden bilerek farklı (daha küçük) değerlerle.
        out += build_battery_data_message(BATTERY2_MSG_ID, float(t), volt - 1.0, float(pack_curr) * 0.6)
        motor_currents = [pack_curr / MOTOR_COUNT * factor for factor in motor_factors]
        out += build_esc_status_data_message(float(t), motor_currents)

    return bytes(out)


if __name__ == "__main__":
    output_path = sys.argv[1] if len(sys.argv) > 1 else "synthetic_test_log.ulog"
    with open(output_path, "wb") as f:
        f.write(generate())
    print(f"Sentetik ulog yazildi: {output_path}")
