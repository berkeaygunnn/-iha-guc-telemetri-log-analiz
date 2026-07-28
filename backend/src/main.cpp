// İHA Güç/Telemetri Log Analiz Aracı - Backend giriş noktası.
//
// ArduPilot .bin dosyaları "kendini tanımlayan" bir formatta: dosyanın
// başında/ilk kullanımdan önce her mesaj tipi için bir FMT mesajı gelir ve bu
// mesaj, o tipin alanlarının (Volt, Curr, ...) isimlerini ve byte boyutlarını
// tanımlar. Biz de önce FMT mesajlarını okuyup bir "sözlük" oluşturuyoruz,
// sonra "BAT" mesajlarını bu sözlüğe göre çözüp voltaj/akım verisini çıkarıyoruz.
//
// Kaynak: https://github.com/ArduPilot/ardupilot/blob/master/libraries/AP_Logger/LogStructure.h

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif

namespace {

namespace fs = std::filesystem;

// Bir UTF-8 metni dosya yolu olarak yorumlar. Windows'ta bu, dosya adındaki
// Türkçe karakterlerin (ü, İ, ş, ...) sistemin ANSI kod sayfasından bağımsız,
// her zaman doğru şekilde açılmasını sağlar.
fs::path toPath(const std::string& utf8Path) {
    return fs::u8path(utf8Path);
}

constexpr uint8_t HEAD_BYTE1 = 0xA3;
constexpr uint8_t HEAD_BYTE2 = 0x95;
constexpr uint8_t LOG_FORMAT_MSG = 128;  // FMT mesajının sabit tip numarası
constexpr size_t FMT_MESSAGE_LENGTH = 89;  // 3 (header) + 1 + 1 + 4 + 16 + 64

// Bir mesaj tipinin (ör. "BAT") alan yapısını tutar; FMT mesajından okunur.
struct FormatDef {
    uint8_t type = 0;
    uint8_t length = 0;               // header dahil toplam mesaj uzunluğu
    std::string name;                 // örn. "BAT"
    std::string format;               // her karakter bir alanın tipini belirtir
    std::vector<std::string> labels;  // format karakterleriyle eşleşen alan adları
};

struct BatterySamplePoint {
    double time_s;
    double voltage_v;
    double current_a;
};

struct MotorSamplePoint {
    double time_s;
    double current_a;
};

// Bir çıkış kanalının (motor/servo/direksiyon) PWM darbe genişliği.
// DİKKAT: bu bir güç ölçümü DEĞİL, uçuş kontrolcüsünün ürettiği kontrol
// çıktısı. Akım sensörü olmayan araçlarda (birçok rover, bkz. hasCurrentData)
// motor aktivitesinin tek görünür kanıtı olduğu için ayıklanıyor.
struct PwmSamplePoint {
    double time_s;
    double pwm_us;
};

// Batarya (BAT) ve motor (ESC) mesajlarından çıkarılan tüm veriler.
// Her ikisi de anahtarı 1'den başlayan motor/batarya no olan bir map: birden
// fazla batarya/motor varsa hepsi ayrı ayrı tutulur.
struct ParsedLog {
    std::string format;  // "ardupilot" ya da "px4"
    // Aracin tipi ("rover", "fixed_wing", "multirotor", "vtol", ...). Log
    // bunu icermiyorsa ya da taninmayan bir deger tasiyorsa "unknown" kalir.
    // SADECE bilgi amacli bir etiket: hangi panellerin cizilecegi buna gore
    // DEGIL, verinin gercekten var olup olmadigina gore belirlenir (bkz.
    // hasCurrentData) — ayni arac tipinin ESC telemetrisi olan da olmayan da
    // oluyor, yani arac tipi verinin varligi icin guvenilir bir sinyal degil.
    std::string vehicleType = "unknown";
    std::map<int, std::vector<BatterySamplePoint>> batteries;
    std::map<int, std::vector<MotorSamplePoint>> motors;
    // Çıkış kanallarının PWM zaman serileri. Anahtar, doğrudan arayüzde
    // gösterilecek ETİKET ("MAIN 2", "AUX 1", "Kanal 3"): ArduPilot ile PX4
    // kanalları farklı adlandırdığı için etiketi burada üretiyoruz, böylece
    // frontend log formatlarından tamamen habersiz kalmaya devam ediyor
    // (mimarinin temel kuralı). std::map olduğu için etiketler alfabetik
    // sırada, yani kanal sırası tutarlı çıkıyor.
    //
    // Hangi kanalın motor, hangisinin servo/direksiyon olduğu loglarda
    // YAZMIYOR; bu yüzden burada tahmin yürütülmüyor, kanallar oldukları gibi
    // aktarılıyor.
    std::map<std::string, std::vector<PwmSamplePoint>> pwmOutputs;
    // PX4'te bazı araçlar (ör. bazı Rover yapılandırmaları) hiç "battery_status"
    // yayınlamıyor, sadece dahili güç hatlarını raporlayan "system_power"ı
    // kullanıyor. Bu, ana batarya voltajı/akımı DEĞİL (5V/payload hattı gibi
    // dahili rayların voltajı); bu yüzden veri olarak kullanılmıyor, sadece
    // "batarya verisi yok" durumunda kullanıcıya daha isabetli bir hata mesajı
    // verebilmek için bu konunun görülüp görülmediği izleniyor.
    bool hasSystemPowerTopic = false;
    // Kümülatif tüketilen kapasite (mAh) ve kalan yüzde (0-100) — batarya
    // zaman serisinin tamamı değil, o bataryada en son görülen tek değer
    // (ArduPilot'ta "CurrTot"/"RemPct", PX4'te "discharged_mah"/"remaining").
    // Bu alanlar tüm loglarda bulunmayabilir (opsiyonel); yoksa harita hiç
    // doldurulmaz, JSON'a "null" olarak yazılır.
    std::map<int, double> capacityUsedMah;
    std::map<int, double> remainingPct;
    // Batarya sıcaklığı (°C) — CurrTot/RemPct'in aksine bu bir ZAMAN SERİSİ
    // (ArduPilot "Temp", PX4 "temperature"). Bir bataryanın örneklerinde bu
    // alan bulunduysa batteries[id] ile birebir aynı uzunlukta doldurulur
    // (ikisi aynı döngüde, aynı koşulda push edildiği için); alan hiç yoksa
    // harita o id için hiç doldurulmaz, JSON'a boş dizi yazılır.
    std::map<int, std::vector<double>> temperaturesC;
};

// Bir batarya/motorun akım ölçümünün GERÇEK olup olmadığını belirler.
//
// Akım sensörü bağlı değilse ArduPilot/PX4 bu alanı yayınlamayı bırakmaz —
// her örneğe tam 0.0 yazar. Gerçek bir rover logunda (data/
// px4_ground_rover_flight.ulg) 452 örneğin hepsi 0.00 A çıktı; buna karşılık
// akım sensörü olan bir logda araç dururken bile ölçüm küçük bir gürültü/
// kalibrasyon ofseti taşıyor (hexarotor logunda min 0.26 A, hatta -0.73 A).
// Yani "hiçbir örnekte sıfırdan farklı değer yok" pratikte "bu araçta akım
// sensörü yok" demek.
//
// Bu ayrım olmadan frontend "ölçüm yok"u "ölçüm sıfır" gibi gösteriyordu:
// düz sıfır çizgisi, tek renk ısı haritası, 0.0 Wh enerji, 0 W tepe güç.
// Aynı sorun rover'a özgü değil — akım sensörsüz sabit kanat ve eski
// ArduPilot logları da aynı duruma düşüyor.
template <typename SamplePoint>
bool hasCurrentData(const std::vector<SamplePoint>& points) {
    for (const SamplePoint& point : points) {
        if (point.current_a != 0.0) return true;
    }
    return false;
}

// Format karakterinin kapladığı byte sayısı (LogStructure.h'deki tablo).
size_t fieldByteSize(char formatChar) {
    switch (formatChar) {
        case 'a': return 64;  // int16_t[32]
        case 'b': case 'B': case 'M': return 1;
        case 'h': case 'H': case 'c': case 'C': return 2;
        case 'i': case 'I': case 'f': case 'e': case 'E': case 'L': return 4;
        case 'n': return 4;
        case 'N': return 16;
        case 'Z': return 64;
        case 'd': case 'q': case 'Q': return 8;
        default: return 0;  // bilinmeyen karakter
    }
}

// Bir alanın ham baytlarını double'a çevirir. c/C/e/E karakterleri ArduPilot'ta
// 100 ile ölçeklenmiş tam sayılar olduğu için burada geri bölünüyor.
double readFieldAsDouble(const uint8_t* data, char formatChar) {
    switch (formatChar) {
        case 'b': { int8_t v; std::memcpy(&v, data, 1); return v; }
        case 'B': case 'M': { uint8_t v; std::memcpy(&v, data, 1); return v; }
        case 'h': { int16_t v; std::memcpy(&v, data, 2); return v; }
        case 'H': { uint16_t v; std::memcpy(&v, data, 2); return v; }
        case 'c': { int16_t v; std::memcpy(&v, data, 2); return v / 100.0; }
        case 'C': { uint16_t v; std::memcpy(&v, data, 2); return v / 100.0; }
        case 'i': case 'L': { int32_t v; std::memcpy(&v, data, 4); return v; }
        case 'I': { uint32_t v; std::memcpy(&v, data, 4); return v; }
        case 'e': { int32_t v; std::memcpy(&v, data, 4); return v / 100.0; }
        case 'E': { uint32_t v; std::memcpy(&v, data, 4); return v / 100.0; }
        case 'f': { float v; std::memcpy(&v, data, 4); return v; }
        case 'd': { double v; std::memcpy(&v, data, 8); return v; }
        case 'q': { int64_t v; std::memcpy(&v, data, 8); return static_cast<double>(v); }
        case 'Q': { uint64_t v; std::memcpy(&v, data, 8); return static_cast<double>(v); }
        default: return 0.0;
    }
}

std::vector<std::string> splitByComma(const std::string& text) {
    std::vector<std::string> parts;
    std::stringstream ss(text);
    std::string part;
    while (std::getline(ss, part, ',')) {
        parts.push_back(part);
    }
    return parts;
}

// Bir mesaj tanımında (FormatDef) verilen isimdeki alanın byte offset'ini bulur.
struct FieldLocator {
    bool found = false;
    size_t byteOffset = 0;
    char formatChar = ' ';
};

// payloadSize: bu mesaj örneğinin gerçekte kaç bayt veri taşıdığı (header
// haric, def.length - 3). FMT tanımının "format" alanı bozuk bir dosyada
// (bit hatası, yarım yazılmış FMT mesajı vb.) "length" ile tutarsız olabilir;
// bu durumda hesaplanan offset payload'ın dışına taşabilir. Böyle bir alan
// bulunamamış gibi (found=false) sayılır, aksi halde buffer sınırları dışında
// okuma (heap-buffer-overflow) riski oluşur.
FieldLocator locateField(const FormatDef& def, const std::string& fieldName, size_t payloadSize) {
    size_t offset = 0;
    for (size_t i = 0; i < def.labels.size() && i < def.format.size(); ++i) {
        size_t fieldSize = fieldByteSize(def.format[i]);
        if (def.labels[i] == fieldName) {
            if (offset + fieldSize > payloadSize) return FieldLocator{};
            return FieldLocator{true, offset, def.format[i]};
        }
        // Tanınmayan bir format karakteri (fieldByteSize'ın "default: return 0"
        // dalı) burada durdurulmazsa sonraki tüm alanların offset'i sessizce
        // kayar — yanlış bayt aralığı okunur ama yine de payload sınırları
        // içinde kalabileceği için found=true dönerdi. locateUlogField'deki
        // eşdeğer korumayla (bkz. aşağısı) aynı mantık.
        if (fieldSize == 0) break;
        offset += fieldSize;
    }
    return FieldLocator{};
}

// pos konumundaki FMT mesajını okuyup bir FormatDef üretir.
// Bu mesajın kendi yapısı sabittir (type, length, name[4], format[16], labels[64]).
FormatDef parseFormatMessage(const std::vector<uint8_t>& buffer, size_t pos) {
    FormatDef def;
    def.type = buffer[pos + 3];
    def.length = buffer[pos + 4];

    std::string rawName(reinterpret_cast<const char*>(&buffer[pos + 5]), 4);
    std::string rawFormat(reinterpret_cast<const char*>(&buffer[pos + 9]), 16);
    std::string rawLabels(reinterpret_cast<const char*>(&buffer[pos + 25]), 64);

    def.name = rawName.c_str();      // ilk null byte'a kadar keser
    def.format = rawFormat.c_str();
    def.labels = splitByComma(rawLabels.c_str());
    return def;
}

// "BAT" mesajının payload'ından TimeUS/Volt/Curr alanlarını çıkarır ve batarya
// numarasına (Inst + 1) göre gruplar. Not: ArduPilot bu alana BAT mesajında
// "Inst" adını veriyor (ESC mesajında ise "Instance"). "Inst" alanı yoksa
// (çok eski loglar) tek batarya varsayılıp id=1'e yazılır.
//
// "CurrTot" (kümülatif tüketilen kapasite, mAh) ve "RemPct" (kalan yüzde)
// opsiyoneldir — tüm ArduPilot sürümlerinde/batarya izleyici
// yapılandırmalarında yok. Bulunursa her örnekte üzerine yazılır, tarama
// bitince map'te doğal olarak SON (en güncel) değer kalır.
void extractBatterySample(const uint8_t* payload, const FormatDef& def, size_t payloadSize,
                           ParsedLog& result) {
    FieldLocator timeField = locateField(def, "TimeUS", payloadSize);
    FieldLocator voltField = locateField(def, "Volt", payloadSize);
    FieldLocator currField = locateField(def, "Curr", payloadSize);
    FieldLocator instField = locateField(def, "Inst", payloadSize);
    FieldLocator currTotField = locateField(def, "CurrTot", payloadSize);
    FieldLocator remPctField = locateField(def, "RemPct", payloadSize);
    FieldLocator tempField = locateField(def, "Temp", payloadSize);  // santi-derece int16 ('c'), opsiyonel

    if (!timeField.found || !voltField.found || !currField.found) return;

    int instance = 0;
    if (instField.found) {
        instance = static_cast<int>(
            readFieldAsDouble(payload + instField.byteOffset, instField.formatChar));
    }

    double timeUs = readFieldAsDouble(payload + timeField.byteOffset, timeField.formatChar);
    double volt = readFieldAsDouble(payload + voltField.byteOffset, voltField.formatChar);
    double curr = readFieldAsDouble(payload + currField.byteOffset, currField.formatChar);

    // Bozuk bir dosyada okunan bayt bir NaN/Infinity'ye denk gelebilir; bu
    // JSON'a yazilamaz (gecerli JSON'da NaN/Infinity yok). Boyle bir ornegi
    // sessizce atlamak, locateField'in payload-sinirini asan alani atlamasiyla
    // ayni felsefe: bozuk tek bir ornegi at, veriyi kirletme.
    if (!std::isfinite(timeUs) || !std::isfinite(volt) || !std::isfinite(curr)) return;

    int batteryId = instance + 1;
    result.batteries[batteryId].push_back(BatterySamplePoint{timeUs / 1e6, volt, curr});

    if (currTotField.found) {
        double currTot = readFieldAsDouble(payload + currTotField.byteOffset, currTotField.formatChar);
        if (std::isfinite(currTot)) result.capacityUsedMah[batteryId] = currTot;
    }
    if (remPctField.found) {
        double remPct = readFieldAsDouble(payload + remPctField.byteOffset, remPctField.formatChar);
        if (std::isfinite(remPct)) result.remainingPct[batteryId] = remPct;
    }
    if (tempField.found) {
        // temperaturesC[batteryId], batteries[batteryId] ile HER ZAMAN aynı
        // uzunlukta kalmalı (frontend ikisini aynı indeksle eşleştiriyor);
        // bu yüzden (capacityUsedMah/remainingPct'ten farklı olarak) tek bir
        // bozuk örnek bile atlanmaz, 0.0 ile doldurulur.
        double temp = readFieldAsDouble(payload + tempField.byteOffset, tempField.formatChar);
        result.temperaturesC[batteryId].push_back(std::isfinite(temp) ? temp : 0.0);
    }
}

// "ESC" mesajının payload'ından TimeUS/Instance/Curr alanlarını çıkarır ve
// motor numarasına (Instance + 1) göre gruplar.
void extractEscSample(const uint8_t* payload, const FormatDef& def, size_t payloadSize,
                       std::map<int, std::vector<MotorSamplePoint>>& motors) {
    FieldLocator timeField = locateField(def, "TimeUS", payloadSize);
    FieldLocator instField = locateField(def, "Instance", payloadSize);
    FieldLocator currField = locateField(def, "Curr", payloadSize);

    if (!timeField.found || !instField.found || !currField.found) return;

    double timeUs = readFieldAsDouble(payload + timeField.byteOffset, timeField.formatChar);
    int instance = static_cast<int>(
        readFieldAsDouble(payload + instField.byteOffset, instField.formatChar));
    double curr = readFieldAsDouble(payload + currField.byteOffset, currField.formatChar);

    if (!std::isfinite(timeUs) || !std::isfinite(curr)) return;

    motors[instance + 1].push_back(MotorSamplePoint{timeUs / 1e6, curr});
}

// ArduPilot'ta çıkış kanallarının PWM'i "RCOU" mesajında C1..C14 alanları
// olarak tutulur (hepsi uint16, mikrosaniye). Kaç kanalın loglandığı sürüme/
// araca göre değiştiği için alanlar tek tek aranıp bulunamayanlar atlanıyor.
constexpr int ARDUPILOT_MAX_RCOU_CHANNELS = 14;

void extractRcouSample(const uint8_t* payload, const FormatDef& def, size_t payloadSize,
                        std::map<std::string, std::vector<PwmSamplePoint>>& pwmOutputs) {
    FieldLocator timeField = locateField(def, "TimeUS", payloadSize);
    if (!timeField.found) return;

    double timeUs = readFieldAsDouble(payload + timeField.byteOffset, timeField.formatChar);
    if (!std::isfinite(timeUs)) return;

    for (int channel = 1; channel <= ARDUPILOT_MAX_RCOU_CHANNELS; ++channel) {
        FieldLocator channelField = locateField(def, "C" + std::to_string(channel), payloadSize);
        if (!channelField.found) continue;

        double pwm = readFieldAsDouble(payload + channelField.byteOffset, channelField.formatChar);
        if (!std::isfinite(pwm)) continue;

        pwmOutputs["Kanal " + std::to_string(channel)].push_back(
            PwmSamplePoint{timeUs / 1e6, pwm});
    }
}

// ArduPilot .bin'de araç tipi sayısal bir alan olarak loglanmaz; onun yerine
// açılışta yazılan "MSG" (serbest metin) satırlarından biri firmware adını
// içerir: "ArduCopter V4.3.7 (abc123)", "ArduRover V4.4.0" gibi. Araç tipini
// bu isimden çıkarıyoruz. Tanınmayan bir isimse boş string döner (çağıran
// taraf mevcut değeri korur).
std::string vehicleTypeFromFirmwareName(const std::string& text) {
    if (text.find("ArduCopter") != std::string::npos) return "multirotor";
    if (text.find("ArduPlane") != std::string::npos) return "fixed_wing";
    // Eski sürümler "APMrover2" adını kullanıyordu, yenileri "ArduRover".
    if (text.find("ArduRover") != std::string::npos ||
        text.find("APMrover") != std::string::npos) return "rover";
    if (text.find("ArduSub") != std::string::npos) return "submarine";
    return "";
}

// "MSG" mesajının metin alanına bakıp firmware adını yakalamaya çalışır.
// Bir logda yüzlerce MSG satırı olabilir ama firmware adı ilk satırlardan
// birinde geçer; tip bir kez bulunduktan sonra kalan MSG'ler atlanıyor.
void extractVehicleTypeFromMsg(const uint8_t* payload, const FormatDef& def, size_t payloadSize,
                                ParsedLog& result) {
    if (result.vehicleType != "unknown") return;

    FieldLocator msgField = locateField(def, "Message", payloadSize);
    if (!msgField.found) return;

    // 'Z' = char[64]. Metin 64 baytı tam doldurduysa sonunda null olmayabilir,
    // bu yüzden önce payload sınırına göre kırpıp sonra ilk null'a kadar kesiyoruz.
    size_t maxLength = payloadSize - msgField.byteOffset;
    if (maxLength > fieldByteSize(msgField.formatChar)) maxLength = fieldByteSize(msgField.formatChar);
    std::string text(reinterpret_cast<const char*>(payload + msgField.byteOffset), maxLength);
    text = text.c_str();  // ilk null byte'a kadar keser

    std::string vehicleType = vehicleTypeFromFirmwareName(text);
    if (!vehicleType.empty()) result.vehicleType = vehicleType;
}

// ArduPilot .bin buffer'ını baştan sona tarar: FMT mesajlarından sözlüğü
// kurar, "BAT" ve "ESC" mesajlarını tek geçişte çözer.
ParsedLog parseArduPilotBuffer(const std::vector<uint8_t>& buffer) {
    std::map<uint8_t, FormatDef> formats;
    ParsedLog result;
    result.format = "ardupilot";

    size_t pos = 0;
    while (pos + 3 <= buffer.size()) {
        if (buffer[pos] != HEAD_BYTE1 || buffer[pos + 1] != HEAD_BYTE2) {
            std::cerr << "Beklenmeyen bayt (senkron kaybi), offset=" << pos
                      << ". Ayristirma burada durduruldu." << std::endl;
            break;
        }
        uint8_t msgId = buffer[pos + 2];

        if (msgId == LOG_FORMAT_MSG) {
            if (pos + FMT_MESSAGE_LENGTH > buffer.size()) break;
            FormatDef def = parseFormatMessage(buffer, pos);
            formats[def.type] = def;
            pos += FMT_MESSAGE_LENGTH;
            continue;
        }

        auto it = formats.find(msgId);
        if (it == formats.end()) {
            std::cerr << "Bilinmeyen mesaj tipi (FMT tanimi yok): " << static_cast<int>(msgId)
                      << ", offset=" << pos << ". Ayristirma burada durduruldu." << std::endl;
            break;
        }

        const FormatDef& def = it->second;
        if (def.length <= 3) {
            // Gecersiz/bozuk uzunluk; pos hic ilerlemezse sonsuz donguye girer.
            std::cerr << "Gecersiz mesaj uzunlugu (" << static_cast<int>(def.length)
                      << "), offset=" << pos << ". Ayristirma burada durduruldu." << std::endl;
            break;
        }
        if (pos + def.length > buffer.size()) break;

        size_t payloadSize = static_cast<size_t>(def.length) - 3;
        if (def.name == "BAT" || def.name == "CURR") {
            extractBatterySample(buffer.data() + pos + 3, def, payloadSize, result);
        } else if (def.name == "ESC") {
            extractEscSample(buffer.data() + pos + 3, def, payloadSize, result.motors);
        } else if (def.name == "RCOU") {
            extractRcouSample(buffer.data() + pos + 3, def, payloadSize, result.pwmOutputs);
        } else if (def.name == "MSG") {
            extractVehicleTypeFromMsg(buffer.data() + pos + 3, def, payloadSize, result);
        }

        pos += def.length;
    }

    return result;
}

// ---------------------------------------------------------------------------
// PX4 .ulog desteği
//
// ULog da ArduPilot .bin gibi kendini tanımlayan bir format ama yapısı farklı:
// - "F" (Format) mesajları, her biri bir mesaj tipinin (ör. "battery_status")
//   alan listesini metin olarak tanımlar: "battery_status:uint64_t timestamp;
//   float voltage_v;float current_a;..."
// - "A" (Subscription) mesajları, sayısal bir msg_id'yi bir mesaj tipi adına
//   bağlar (ArduPilot'taki FMT'nin type numarasına benzer, ama iki adımlı).
// - "D" (Logged Data) mesajları, o msg_id'ye ait gerçek veriyi taşır.
//
// Kaynak: https://docs.px4.io/main/en/dev_log/ulog_file_format
// ---------------------------------------------------------------------------

constexpr size_t ULOG_HEADER_LENGTH = 16;  // magic(7) + version(1) + timestamp(8)
const uint8_t ULOG_MAGIC[7] = {0x55, 0x4C, 0x6F, 0x67, 0x01, 0x12, 0x35};

// Bir ULog mesaj tipinin (ör. "battery_status") alan adı+tipi listesini tutar.
struct ULogField {
    std::string name;
    std::string elementType;   // "float", "uint64_t", ya da başka bir mesaj adı ("esc_report" gibi)
    int arrayLength = 1;       // 1 = dizi değil
    size_t size = 0;           // TOPLAM bayt (elementSize * arrayLength); nested tipler için
                               // ikinci geçişte (resolveNestedSizes) hesaplanana kadar 0
};

struct ULogFormatDef {
    std::string name;
    std::vector<ULogField> fields;
};

// Bir msg_id'nin hangi mesaj tipine ve hangi örneğe (multi_id) ait olduğunu tutar.
struct ULogSubscription {
    std::string messageName;
    uint8_t multiId = 0;
};

bool isUlogFile(const std::vector<uint8_t>& buffer) {
    if (buffer.size() < sizeof(ULOG_MAGIC)) return false;
    return std::memcmp(buffer.data(), ULOG_MAGIC, sizeof(ULOG_MAGIC)) == 0;
}

// ULog format string'indeki temel tiplerin byte boyutu. Başka bir mesaj
// tipine referans veren (nested) alanlar burada desteklenmiyor (0 döner).
size_t ulogBasicTypeSize(const std::string& type) {
    if (type == "int8_t" || type == "uint8_t" || type == "bool" || type == "char") return 1;
    if (type == "int16_t" || type == "uint16_t") return 2;
    if (type == "int32_t" || type == "uint32_t" || type == "float") return 4;
    if (type == "int64_t" || type == "uint64_t" || type == "double") return 8;
    return 0;
}

// "float voltage_v" ya da "esc_report[12] esc" gibi tek bir alan tanımını çözer.
// Temel tipse boyutu hemen hesaplanır; başka bir mesaj tipine referanssa
// (nested struct) boyut, tüm FMT mesajları okunduktan sonra ikinci geçişte
// (resolveNestedSizes) hesaplanır.
ULogField parseUlogField(const std::string& fieldSpec) {
    size_t spacePos = fieldSpec.find(' ');
    if (spacePos == std::string::npos) return ULogField{};

    std::string type = fieldSpec.substr(0, spacePos);
    std::string name = fieldSpec.substr(spacePos + 1);

    ULogField field;
    field.name = name;
    field.arrayLength = 1;

    size_t bracketPos = type.find('[');
    if (bracketPos != std::string::npos) {
        field.elementType = type.substr(0, bracketPos);
        field.arrayLength = std::atoi(type.c_str() + bracketPos + 1);
    } else {
        field.elementType = type;
    }

    size_t baseSize = ulogBasicTypeSize(field.elementType);
    // Bozuk bir format string'i ("tip[-5]" gibi) negatif bir arrayLength
    // uretebilir; size_t'e cast edilince dev bir sayiya sarip sonraki offset
    // toplamalarini tasirabilirdi. Boyutu 0 (bilinmeyen/cozulemeyen alan)
    // sayip guvenli tarafta kaliyoruz.
    field.size = (baseSize == 0 || field.arrayLength < 0)
        ? 0 : baseSize * static_cast<size_t>(field.arrayLength);
    return field;
}

// "esc_report" gibi başka bir mesaj tipine referans veren alanların boyutunu,
// o tipin kendi FMT tanımına bakarak (gerekirse iç içe) hesaplar.
size_t resolveFormatSize(const std::string& formatName,
                          std::map<std::string, ULogFormatDef>& formats,
                          std::set<std::string>& inProgress) {
    auto it = formats.find(formatName);
    if (it == formats.end() || inProgress.count(formatName)) return 0;
    inProgress.insert(formatName);

    size_t total = 0;
    for (ULogField& field : it->second.fields) {
        if (field.size == 0 && !field.elementType.empty()) {
            size_t elementSize = resolveFormatSize(field.elementType, formats, inProgress);
            field.size = elementSize * static_cast<size_t>(field.arrayLength);
        }
        total += field.size;
    }

    inProgress.erase(formatName);
    return total;
}

// Tüm FMT tanımları okunduktan sonra çağrılır; nested tipli alanların
// boyutlarını (esc_status içindeki esc_report[12] gibi) doldurur.
void resolveNestedSizes(std::map<std::string, ULogFormatDef>& formats) {
    for (auto& entry : formats) {
        std::set<std::string> inProgress;
        resolveFormatSize(entry.first, formats, inProgress);
    }
}

// Bir formatın (resolveNestedSizes sonrası) toplam bayt boyutu. 0 dönerse
// çözülemeyen bir alan var demektir.
//
// Not: PX4'ün mesaj üretici aracı, hizalama için mesajın sonuna "_padding..."
// adlı alanlar ekleyebiliyor; gerçek loglarla doğrulandı ki logger bu SADECE-
// hizalama alanlarını (mesajın en son alanıysa) diske hiç yazmıyor — örneğin
// "battery_status" için hesaplanan toplam 168 bayt ama diskteki gerçek veri
// 167 bayttı, fark tam olarak sondaki 1 baytlık "_padding0" alanıydı. Bu
// yüzden burada sondaki padding alan(lar)ı toplama katılmıyor. Bu, iç içe bir
// tipin dizi elemanı olarak boyutunu hesaplayan resolveFormatSize()'ı
// ETKİLEMEZ — orada tam boyut (padding dahil) doğru, çünkü dizi elemanlarının
// sabit bir stride'ı olması gerekiyor (gerçek "esc_report[8]" verisiyle de
// doğrulandı: o durumda hiç bayt eksilmiyor).
size_t formatTotalSize(const ULogFormatDef& def) {
    size_t lastRealField = def.fields.size();
    while (lastRealField > 0 && def.fields[lastRealField - 1].name.rfind("_padding", 0) == 0) {
        --lastRealField;
    }

    size_t total = 0;
    for (size_t i = 0; i < lastRealField; ++i) total += def.fields[i].size;
    return total;
}

// "battery_status:uint64_t timestamp;float voltage_v;..." formatındaki bir
// Format (F) mesajının payload'ını çözer.
ULogFormatDef parseUlogFormatMessage(const uint8_t* payload, size_t length) {
    std::string text(reinterpret_cast<const char*>(payload), length);

    size_t colonPos = text.find(':');
    if (colonPos == std::string::npos) return ULogFormatDef{};

    ULogFormatDef def;
    def.name = text.substr(0, colonPos);

    std::stringstream ss(text.substr(colonPos + 1));
    std::string fieldSpec;
    while (std::getline(ss, fieldSpec, ';')) {
        if (fieldSpec.empty()) continue;
        def.fields.push_back(parseUlogField(fieldSpec));
    }
    return def;
}

struct ULogFieldLocator {
    bool found = false;
    size_t byteOffset = 0;
    const ULogField* field = nullptr;
};

// payloadSize: bu tanımın alanlarının gerçekte hangi buffer bölgesinden
// okunduğu (üst düzey bir mesaj için mesajın kendi payload boyutu, iç içe bir
// dizi elemanı için o elemanın sabit boyutu). ArduPilot tarafındaki
// locateField ile aynı sebep: bozuk bir dosyada 'format' tanımı ile mesajın
// gerçek boyutu tutarsız olabilir, bu kontrol olmadan buffer sınırları
// dışında okuma riski oluşurdu.
ULogFieldLocator locateUlogField(const ULogFormatDef& def, const std::string& fieldName, size_t payloadSize) {
    size_t offset = 0;
    for (const ULogField& field : def.fields) {
        if (field.name == fieldName) {
            if (offset + field.size > payloadSize) return ULogFieldLocator{};
            return ULogFieldLocator{true, offset, &field};
        }
        if (field.size == 0) break;  // bilinmeyen boyut; sonraki offsetler güvenilir değil
        offset += field.size;
    }
    return ULogFieldLocator{};
}

double readUlogFieldAsDouble(const uint8_t* data, const std::string& type) {
    if (type == "float") { float v; std::memcpy(&v, data, 4); return v; }
    if (type == "double") { double v; std::memcpy(&v, data, 8); return v; }
    if (type == "uint64_t") { uint64_t v; std::memcpy(&v, data, 8); return static_cast<double>(v); }
    if (type == "int64_t") { int64_t v; std::memcpy(&v, data, 8); return static_cast<double>(v); }
    if (type == "uint32_t") { uint32_t v; std::memcpy(&v, data, 4); return v; }
    if (type == "int32_t") { int32_t v; std::memcpy(&v, data, 4); return v; }
    if (type == "uint16_t") { uint16_t v; std::memcpy(&v, data, 2); return v; }
    if (type == "int16_t") { int16_t v; std::memcpy(&v, data, 2); return v; }
    if (type == "uint8_t" || type == "bool" || type == "char") { uint8_t v; std::memcpy(&v, data, 1); return v; }
    if (type == "int8_t") { int8_t v; std::memcpy(&v, data, 1); return v; }
    return 0.0;
}

// "battery_status" verisinden timestamp/voltage_v/current_a çıkarır ve
// batarya numarasına (multiId + 1) göre gruplar. "discharged_mah" (kümülatif
// tüketilen kapasite) ve "remaining" (0-1 kalan oran, ArduPilot'un RemPct'iyle
// aynı birime getirmek için *100 ile yüzdeye çevrilir) opsiyoneldir — bazı
// PX4 araçları bu alanları yayınlamaz.
void extractUlogBatterySample(const uint8_t* payload, const ULogFormatDef& def, size_t payloadSize,
                               int multiId, ParsedLog& result) {
    ULogFieldLocator timeField = locateUlogField(def, "timestamp", payloadSize);
    ULogFieldLocator voltField = locateUlogField(def, "voltage_v", payloadSize);
    ULogFieldLocator currField = locateUlogField(def, "current_a", payloadSize);
    ULogFieldLocator dischargedField = locateUlogField(def, "discharged_mah", payloadSize);
    ULogFieldLocator remainingField = locateUlogField(def, "remaining", payloadSize);
    ULogFieldLocator temperatureField = locateUlogField(def, "temperature", payloadSize);  // float, °C, opsiyonel

    if (!timeField.found || !voltField.found || !currField.found) return;

    double timestamp = readUlogFieldAsDouble(payload + timeField.byteOffset, timeField.field->elementType);
    double volt = readUlogFieldAsDouble(payload + voltField.byteOffset, voltField.field->elementType);
    double curr = readUlogFieldAsDouble(payload + currField.byteOffset, currField.field->elementType);

    if (!std::isfinite(timestamp) || !std::isfinite(volt) || !std::isfinite(curr)) return;

    int batteryId = multiId + 1;
    result.batteries[batteryId].push_back(BatterySamplePoint{timestamp / 1e6, volt, curr});

    if (dischargedField.found) {
        double discharged = readUlogFieldAsDouble(payload + dischargedField.byteOffset, dischargedField.field->elementType);
        if (std::isfinite(discharged)) result.capacityUsedMah[batteryId] = discharged;
    }
    if (remainingField.found) {
        double remaining = readUlogFieldAsDouble(payload + remainingField.byteOffset, remainingField.field->elementType);
        if (std::isfinite(remaining)) result.remainingPct[batteryId] = remaining * 100.0;
    }
    if (temperatureField.found) {
        // temperaturesC, batteries[batteryId] ile aynı uzunlukta kalmalı
        // (bkz. ArduPilot tarafındaki aynı yorum) — bozuk tek örnek atlanmaz.
        double temperature = readUlogFieldAsDouble(payload + temperatureField.byteOffset, temperatureField.field->elementType);
        result.temperaturesC[batteryId].push_back(std::isfinite(temperature) ? temperature : 0.0);
    }
}

// PX4'te araç tipi "vehicle_status" konusunda sayısal bir alan olarak
// yayınlanır; değerler PX4'ün vehicle_status.msg içindeki VEHICLE_TYPE_*
// sabitleriyle eşleşir.
constexpr int PX4_VEHICLE_TYPE_ROTARY_WING = 1;
constexpr int PX4_VEHICLE_TYPE_FIXED_WING = 2;
constexpr int PX4_VEHICLE_TYPE_ROVER = 3;
constexpr int PX4_VEHICLE_TYPE_AIRSHIP = 4;

// "vehicle_status" verisinden araç tipini çıkarır. Bu mesaj uçuş boyunca
// yüzlerce kez tekrarlanır ama tip değişmez; yine de her örnekte okunması
// zararsız (son değer kalır).
void extractUlogVehicleType(const uint8_t* payload, const ULogFormatDef& def, size_t payloadSize,
                             ParsedLog& result) {
    ULogFieldLocator typeField = locateUlogField(def, "vehicle_type", payloadSize);
    if (!typeField.found) return;

    // VTOL araçlar uçuş fazına göre "vehicle_type"ı rotary_wing ile fixed_wing
    // arasında DEĞİŞTİRİR (dikey kalkış -> yatay uçuş). Tek bir etiket üretmek
    // istediğimiz için ayrı "is_vtol" bayrağı varsa ona öncelik veriyoruz;
    // yoksa aynı araç log boyunca tip değiştirmiş gibi görünürdü.
    ULogFieldLocator vtolField = locateUlogField(def, "is_vtol", payloadSize);
    if (vtolField.found &&
        readUlogFieldAsDouble(payload + vtolField.byteOffset, vtolField.field->elementType) != 0.0) {
        result.vehicleType = "vtol";
        return;
    }

    int vehicleType = static_cast<int>(
        readUlogFieldAsDouble(payload + typeField.byteOffset, typeField.field->elementType));
    switch (vehicleType) {
        case PX4_VEHICLE_TYPE_ROTARY_WING: result.vehicleType = "multirotor"; break;
        case PX4_VEHICLE_TYPE_FIXED_WING:  result.vehicleType = "fixed_wing"; break;
        case PX4_VEHICLE_TYPE_ROVER:       result.vehicleType = "rover"; break;
        case PX4_VEHICLE_TYPE_AIRSHIP:     result.vehicleType = "airship"; break;
        default: break;  // 0 = unknown ya da bilmediğimiz yeni bir tip; mevcut değeri koru
    }
}

// "esc_status" mesajından, içindeki "esc_report" dizisinin her elemanı için
// akım değerini çıkarır (motor numarası = dizi indeksi + 1).
void extractUlogEscSamples(const uint8_t* payload, const ULogFormatDef& escStatusDef, size_t payloadSize,
                            const std::map<std::string, ULogFormatDef>& formats,
                            std::map<int, std::vector<MotorSamplePoint>>& motors) {
    ULogFieldLocator timeField = locateUlogField(escStatusDef, "timestamp", payloadSize);
    ULogFieldLocator escCountField = locateUlogField(escStatusDef, "esc_count", payloadSize);
    ULogFieldLocator onlineField = locateUlogField(escStatusDef, "esc_online_flags", payloadSize);
    ULogFieldLocator escArrayField = locateUlogField(escStatusDef, "esc", payloadSize);

    if (!timeField.found || !escArrayField.found || escArrayField.field->arrayLength == 0) return;

    auto nestedIt = formats.find(escArrayField.field->elementType);
    if (nestedIt == formats.end()) return;
    const ULogFormatDef& escReportDef = nestedIt->second;

    // "esc_current" alanı, esc_report'un TEK bir dizi elemanının kendi sabit
    // boyutu (elementSize) içinde aranıyor — üst düzey mesajın toplam
    // payloadSize'ı değil, çünkü diziyi elemanlarına bölecek olan sabit
    // stride budur.
    size_t elementSize = escArrayField.field->size / static_cast<size_t>(escArrayField.field->arrayLength);
    ULogFieldLocator currField = locateUlogField(escReportDef, "esc_current", elementSize);
    if (!currField.found) return;

    double timestamp = readUlogFieldAsDouble(payload + timeField.byteOffset, timeField.field->elementType);
    if (!std::isfinite(timestamp)) return;

    // Hangi dizi elemanlarının gerçekten bağlı bir ESC'ye ait olduğunu bulmak
    // için önce "esc_online_flags" bitmask'ine bakılıyor. Gerçek loglarla
    // denendiğinde "esc_count" alanının bazı firmware/sürümlerde hep 0 olarak
    // bırakıldığı (kullanılmadığı) görüldü — buna körü körüne güvenmek gerçek
    // uçuşlarda dolu bir ESC dizisini "0 motor" olarak yorumlatıyordu.
    uint32_t onlineMask = 0;
    bool haveOnlineMask = false;
    if (onlineField.found) {
        int online = static_cast<int>(readUlogFieldAsDouble(payload + onlineField.byteOffset, onlineField.field->elementType));
        if (online > 0) {
            onlineMask = static_cast<uint32_t>(online);
            haveOnlineMask = true;
        }
    }

    int escCount = escArrayField.field->arrayLength;
    if (!haveOnlineMask && escCountField.found) {
        int reported = static_cast<int>(
            readUlogFieldAsDouble(payload + escCountField.byteOffset, escCountField.field->elementType));
        if (reported > 0 && reported < escCount) escCount = reported;
    }

    for (int i = 0; i < escCount; ++i) {
        // onlineMask 32 bitlik; i>=32 için "onlineMask >> i" tanımsız davranış
        // olurdu (shift genişliği >= operand genişliği). Gerçek PX4 firmware'i
        // 32 ESC'yi aşmıyor ama Format string'indeki arrayLength'e üst sınır
        // konmadığı için özel/uydurma bir log bu yolu tetikleyebilir; 32'yi
        // aşan indeksler haveOnlineMask=false durumundaki gibi (maskeye
        // bakmadan) işlenir.
        if (haveOnlineMask && i < 32 && !((onlineMask >> i) & 1u)) continue;  // bu dizi elemanında bağlı ESC yok
        const uint8_t* elementPtr = payload + escArrayField.byteOffset + static_cast<size_t>(i) * elementSize;
        double curr = readUlogFieldAsDouble(elementPtr + currField.byteOffset, currField.field->elementType);
        if (!std::isfinite(curr)) continue;
        motors[i + 1].push_back(MotorSamplePoint{timestamp / 1e6, curr});
    }
}

// PX4'te bir çıkış grubunun (instance) arayüzde görünecek adı. PX4 donanım
// terminolojisinde instance 0 = MAIN çıkış rayı, 1 = AUX. Gerçek loglarda
// ikisinin de dolu olduğu görüldü ve motorların HANGİSİNDE olduğu araca göre
// değişiyor (hexarotor logunda 6 motor AUX'ta, rover'da tek hareketli kanal
// MAIN'de) — bu yüzden bir grup "asıl motorlar" diye seçilmiyor, ikisi de
// etiketlenip olduğu gibi aktarılıyor.
std::string ulogOutputGroupName(uint8_t multiId) {
    if (multiId == 0) return "MAIN";
    if (multiId == 1) return "AUX";
    return "OUT" + std::to_string(static_cast<int>(multiId));
}

// "actuator_outputs" mesajından her çıkış kanalının PWM değerini ayıklar.
// esc_status'ün aksine burada TÜM instance'lar kabul edilir (yukarıdaki not).
void extractUlogActuatorOutputs(const uint8_t* payload, const ULogFormatDef& def, size_t payloadSize,
                                 uint8_t multiId,
                                 std::map<std::string, std::vector<PwmSamplePoint>>& pwmOutputs) {
    ULogFieldLocator timeField = locateUlogField(def, "timestamp", payloadSize);
    ULogFieldLocator countField = locateUlogField(def, "noutputs", payloadSize);
    ULogFieldLocator outputField = locateUlogField(def, "output", payloadSize);

    if (!timeField.found || !outputField.found || outputField.field->arrayLength <= 0) return;

    double timestamp = readUlogFieldAsDouble(payload + timeField.byteOffset, timeField.field->elementType);
    if (!std::isfinite(timestamp)) return;

    // "output" sabit uzunluklu bir dizi (float[16]) ama araç genelde daha az
    // kanal kullanır; gerisi çöp/sıfır olduğu için noutputs ile sınırlanıyor.
    int channelCount = outputField.field->arrayLength;
    if (countField.found) {
        int reported = static_cast<int>(
            readUlogFieldAsDouble(payload + countField.byteOffset, countField.field->elementType));
        if (reported > 0 && reported < channelCount) channelCount = reported;
    }

    size_t elementSize = outputField.field->size / static_cast<size_t>(outputField.field->arrayLength);
    std::string groupName = ulogOutputGroupName(multiId);

    for (int i = 0; i < channelCount; ++i) {
        const uint8_t* elementPtr = payload + outputField.byteOffset + static_cast<size_t>(i) * elementSize;
        double pwm = readUlogFieldAsDouble(elementPtr, outputField.field->elementType);
        if (!std::isfinite(pwm)) continue;

        pwmOutputs[groupName + " " + std::to_string(i + 1)].push_back(
            PwmSamplePoint{timestamp / 1e6, pwm});
    }
}

// PX4 .ulog buffer'ını iki geçişte tarar: önce tüm Format (F) mesajlarını
// toplayıp nested tiplerin (esc_report gibi) boyutlarını çözer, sonra
// Subscription (A) ve Logged Data (D) mesajlarını bu sözlüğe göre işler.
// İki geçiş gerekli çünkü "esc_status" formatı "esc_report"a referans veriyor
// ve dosyada hangisinin önce tanımlandığı garanti değil.
ParsedLog parseUlogBuffer(const std::vector<uint8_t>& buffer) {
    ParsedLog result;
    result.format = "px4";

    if (buffer.size() < ULOG_HEADER_LENGTH) return result;

    std::map<std::string, ULogFormatDef> formats;  // mesaj adı -> tanım

    size_t pos = ULOG_HEADER_LENGTH;
    while (pos + 3 <= buffer.size()) {
        uint16_t msgSize;
        std::memcpy(&msgSize, &buffer[pos], 2);
        char msgType = static_cast<char>(buffer[pos + 2]);
        size_t payloadStart = pos + 3;
        if (payloadStart + msgSize > buffer.size()) break;

        if (msgType == 'F') {
            ULogFormatDef def = parseUlogFormatMessage(buffer.data() + payloadStart, msgSize);
            if (!def.name.empty()) formats[def.name] = def;
        }
        pos = payloadStart + msgSize;
    }
    resolveNestedSizes(formats);

    std::map<uint16_t, ULogSubscription> subscriptions;  // msg_id -> abonelik

    pos = ULOG_HEADER_LENGTH;
    while (pos + 3 <= buffer.size()) {
        uint16_t msgSize;
        std::memcpy(&msgSize, &buffer[pos], 2);
        char msgType = static_cast<char>(buffer[pos + 2]);
        size_t payloadStart = pos + 3;

        if (payloadStart + msgSize > buffer.size()) {
            std::cerr << "Eksik/bozuk ULog mesaji, offset=" << pos
                      << ". Ayristirma burada durduruldu." << std::endl;
            break;
        }
        const uint8_t* payload = buffer.data() + payloadStart;

        if (msgType == 'A') {
            if (msgSize >= 3) {
                uint8_t multiId = payload[0];
                uint16_t msgId;
                std::memcpy(&msgId, payload + 1, 2);
                std::string name(reinterpret_cast<const char*>(payload + 3), msgSize - 3);
                if (name == "system_power") result.hasSystemPowerTopic = true;
                subscriptions[msgId] = ULogSubscription{name, multiId};
            }
        } else if (msgType == 'D') {
            if (msgSize >= 2) {
                uint16_t msgId;
                std::memcpy(&msgId, payload, 2);
                auto subIt = subscriptions.find(msgId);
                if (subIt != subscriptions.end()) {
                    auto fmtIt = formats.find(subIt->second.messageName);
                    if (fmtIt != formats.end()) {
                        // Format tanımının beklediği boyut, mesajın gerçek boyutundan
                        // büyükse (bozuk/yarım dosya, sürüm uyuşmazlığı vb.) alanları
                        // okumaya çalışmak buffer'daki bir sonraki mesajın baytlarını
                        // yanlışlıkla veri gibi yorumlar; bu yüzden önce doğrulanıyor.
                        size_t expectedSize = formatTotalSize(fmtIt->second);
                        size_t actualSize = static_cast<size_t>(msgSize) - 2;
                        if (expectedSize > 0 && actualSize >= expectedSize) {
                            if (fmtIt->second.name == "battery_status") {
                                // Her batarya kendi multiId'siyle (0, 1, 2, ...) ayrı
                                // subscription/msg_id alır; bu yüzden burada tüm
                                // instance'lar kabul edilip numaralarına göre gruplanıyor.
                                extractUlogBatterySample(payload + 2, fmtIt->second, actualSize,
                                                          subIt->second.multiId, result);
                            } else if (fmtIt->second.name == "esc_status" && subIt->second.multiId == 0) {
                                extractUlogEscSamples(payload + 2, fmtIt->second, actualSize, formats, result.motors);
                            } else if (fmtIt->second.name == "vehicle_status" && subIt->second.multiId == 0) {
                                extractUlogVehicleType(payload + 2, fmtIt->second, actualSize, result);
                            } else if (fmtIt->second.name == "actuator_outputs") {
                                extractUlogActuatorOutputs(payload + 2, fmtIt->second, actualSize,
                                                            subIt->second.multiId, result.pwmOutputs);
                            }
                        }
                    }
                }
            }
        }
        // Diğer tipler (B, I, M, P, Q, L, C, S, O, R) şimdilik atlanıyor;
        // msg_size zaten bilindiği için atlamak güvenli.

        pos = payloadStart + msgSize;
    }

    return result;
}

// computeDuration, computeWarnings (voltaj-düşümü başlangıcı) ve
// MIN_DURATION_FOR_WARNINGS_S kapısı hep front()/back()'in gerçek min/max
// zaman damgası olduğunu varsayıyor. Hiçbir yerde sıralama garantisi yok;
// bozuk bir dosyada ya da birleştirilmiş bir kayıtta bir id'nin örnekleri
// zaman sırasıyla gelmezse bu varsayım sessizce bozulur. Ayrıştırma
// bittikten hemen sonra her diziyi zamana göre sıralamak front()/back()'i
// otomatik olarak gerçek min/max yapar; computeDuration vb. hiç değişmeden
// doğru çalışmaya devam eder.
template <typename SampleMap>
void sortSamplesByTime(SampleMap& samples) {
    for (auto& [id, points] : samples) {
        std::stable_sort(points.begin(), points.end(),
                          [](const auto& a, const auto& b) { return a.time_s < b.time_s; });
    }
}

// Dosya tamamı ayrıştırma başlamadan önce belleğe okunuyor; boyut kontrolü
// olmadan çok büyük/bozuk bir dosya std::bad_alloc ile çökmeye ya da bellek
// tükenmesine yol açabilirdi. Üst sınır bolca pay bırakacak şekilde
// seçildi: en büyük gerçek örnek log ~14 MB, çok saatlik bir kayıt için bile
// 1 GB fazlasıyla yeterli.
constexpr std::uintmax_t MAX_LOG_FILE_BYTES = 1024ull * 1024 * 1024;

// Dosyayı okuyup ilk baytlarına (magic number) göre ArduPilot ya da PX4
// parser'ına yönlendirir.
ParsedLog parseLog(const std::string& logPath) {
    std::error_code sizeError;
    std::uintmax_t fileSize = fs::file_size(toPath(logPath), sizeError);
    if (!sizeError && fileSize > MAX_LOG_FILE_BYTES) {
        std::cerr << "Log dosyasi cok buyuk (" << fileSize << " bayt, sinir "
                   << MAX_LOG_FILE_BYTES << " bayt): " << logPath << std::endl;
        return {};
    }

    std::ifstream in(toPath(logPath), std::ios::binary);
    if (!in.is_open()) {
        std::cerr << "Log dosyasi acilamadi: " << logPath << std::endl;
        return {};
    }

    std::vector<uint8_t> buffer((std::istreambuf_iterator<char>(in)),
                                 std::istreambuf_iterator<char>());

    ParsedLog result = isUlogFile(buffer) ? parseUlogBuffer(buffer) : parseArduPilotBuffer(buffer);
    sortSamplesByTime(result.batteries);
    sortSamplesByTime(result.motors);
    sortSamplesByTime(result.pwmOutputs);
    return result;
}

// JSON'da " ve \ karakterleri kaçışlanmalı; aksi halde geçersiz JSON üretilir.
// RFC 8259, U+0000-U+001F aralığındaki TÜM kontrol karakterlerinin
// kaçışlanmasını şart koşar (sadece \n/\r/\t değil) — burada işlenen
// string'ler çoğunlukla dosya yolu/argv gibi sabit kaynaklardan geliyor ama
// beklenmedik bir kontrol karakteri geçersiz JSON üretmesin diye eklendi.
void writeJsonString(std::ostream& out, const std::string& text) {
    out << '"';
    for (char c : text) {
        unsigned char uc = static_cast<unsigned char>(c);
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (uc < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<int>(uc));
                    out << buf;
                } else {
                    out << c;
                }
        }
    }
    out << '"';
}

void writeNumberArray(std::ostream& out, const std::vector<double>& values) {
    out << "[";
    for (size_t i = 0; i < values.size(); ++i) {
        if (i > 0) out << ", ";
        out << values[i];
    }
    out << "]";
}

void writeStringArray(std::ostream& out, const std::vector<std::string>& values) {
    out << "[";
    for (size_t i = 0; i < values.size(); ++i) {
        if (i > 0) out << ", ";
        writeJsonString(out, values[i]);
    }
    out << "]";
}

void writeMotors(std::ostream& out, const std::map<int, std::vector<MotorSamplePoint>>& motors) {
    out << "  \"motors\": [\n";
    size_t written = 0;
    for (const auto& entry : motors) {
        int id = entry.first;
        const std::vector<MotorSamplePoint>& points = entry.second;

        std::vector<double> time_s, current_a;
        for (const auto& point : points) {
            time_s.push_back(point.time_s);
            current_a.push_back(point.current_a);
        }

        out << "    {\n      \"id\": " << id << ",\n      \"time_s\": ";
        writeNumberArray(out, time_s);
        out << ",\n      \"current_a\": ";
        writeNumberArray(out, current_a);
        out << ",\n      \"has_current_data\": " << (hasCurrentData(points) ? "true" : "false");
        out << "\n    }";
        if (++written < motors.size()) out << ",";
        out << "\n";
    }
    out << "  ]";
}

// Bir PWM kanalının uçuş boyunca hiç değişmediğini (min == max) söyler.
//
// Bir aracın çıkış rayında genelde kullanılmayan kanallar da bulunur; bunlar
// log boyunca sabit bir değerde durur (kullanılmıyorsa 0, servo nötr konumu
// için 1500, kilitli motor için 1000). Gerçek loglarda ölçüldü: rover'ın 4
// kanalından 2'si, hexarotor'un MAIN grubundaki bir kanal böyle. Bunları
// JSON'a yazmak hem çıktıyı gereksiz şişirir (rover için ~29.000 anlamsız
// sayı) hem de arayüzde düz çizgiler olarak gürültü yapar.
bool isConstantPwmSeries(const std::vector<PwmSamplePoint>& points) {
    if (points.empty()) return true;
    double minPwm = points.front().pwm_us;
    double maxPwm = minPwm;
    for (const PwmSamplePoint& point : points) {
        if (point.pwm_us < minPwm) minPwm = point.pwm_us;
        if (point.pwm_us > maxPwm) maxPwm = point.pwm_us;
    }
    return minPwm == maxPwm;
}

// Sabit olmayan (gerçekten kullanılan) PWM kanallarını yazar. "id" alanı
// sadece gösterim sırası/renk indeksi; anlamlı olan "label".
void writePwmOutputs(std::ostream& out,
                     const std::map<std::string, std::vector<PwmSamplePoint>>& pwmOutputs) {
    std::vector<const std::pair<const std::string, std::vector<PwmSamplePoint>>*> active;
    for (const auto& entry : pwmOutputs) {
        if (!isConstantPwmSeries(entry.second)) active.push_back(&entry);
    }

    out << "  \"pwm_outputs\": [\n";
    for (size_t i = 0; i < active.size(); ++i) {
        const std::string& label = active[i]->first;
        const std::vector<PwmSamplePoint>& points = active[i]->second;

        std::vector<double> time_s, pwm_us;
        for (const PwmSamplePoint& point : points) {
            time_s.push_back(point.time_s);
            pwm_us.push_back(point.pwm_us);
        }

        out << "    {\n      \"id\": " << (i + 1) << ",\n      \"label\": ";
        writeJsonString(out, label);
        out << ",\n      \"time_s\": ";
        writeNumberArray(out, time_s);
        out << ",\n      \"pwm_us\": ";
        writeNumberArray(out, pwm_us);
        out << "\n    }";
        if (i + 1 < active.size()) out << ",";
        out << "\n";
    }
    out << "  ]";
}

// capacityUsedMah/remainingPct opsiyoneldir (bkz. ParsedLog) — bir batarya
// id'si haritada yoksa şemaya göre "null" yazılır.
void writeBatteries(std::ostream& out, const std::map<int, std::vector<BatterySamplePoint>>& batteries,
                     const std::map<int, double>& capacityUsedMah, const std::map<int, double>& remainingPct,
                     const std::map<int, std::vector<double>>& temperaturesC) {
    out << "  \"batteries\": [\n";
    size_t written = 0;
    for (const auto& entry : batteries) {
        int id = entry.first;
        const std::vector<BatterySamplePoint>& points = entry.second;

        std::vector<double> time_s, voltage_v, current_a;
        for (const auto& point : points) {
            time_s.push_back(point.time_s);
            voltage_v.push_back(point.voltage_v);
            current_a.push_back(point.current_a);
        }

        out << "    {\n      \"id\": " << id << ",\n      \"time_s\": ";
        writeNumberArray(out, time_s);
        out << ",\n      \"voltage_v\": ";
        writeNumberArray(out, voltage_v);
        out << ",\n      \"current_a\": ";
        writeNumberArray(out, current_a);

        out << ",\n      \"capacity_used_mah\": ";
        auto capacityIt = capacityUsedMah.find(id);
        if (capacityIt != capacityUsedMah.end()) out << capacityIt->second; else out << "null";

        out << ",\n      \"remaining_pct\": ";
        auto remainingIt = remainingPct.find(id);
        if (remainingIt != remainingPct.end()) out << remainingIt->second; else out << "null";

        out << ",\n      \"temperature_c\": ";
        auto temperatureIt = temperaturesC.find(id);
        if (temperatureIt != temperaturesC.end()) writeNumberArray(out, temperatureIt->second);
        else out << "[]";

        out << ",\n      \"has_current_data\": " << (hasCurrentData(points) ? "true" : "false");

        out << "\n    }";
        if (++written < batteries.size()) out << ",";
        out << "\n";
    }
    out << "  ]";
}

// Tüm bataryalar arasında en son örneğin zamanını (uçuşun toplam süresi) bulur.
// Verilen örnek dizisinin (batarya/motor/PWM, hepsi bir time_s alanı taşır)
// ilk ve son zaman damgası arasındaki FARKI döner — son damganın kendisini
// DEĞİL: PX4 logları uçuş kontrolcüsünün açılışından beri geçen süreyi
// damgalıyor. Sadece son damgayı almak "px4_fixed_wing_flight.ulg" için
// 4104.7 s veriyordu — gerçek kayıt 90.8 saniye.
template <typename KeyType, typename SamplePoint>
double computeTimeRange(const std::map<KeyType, std::vector<SamplePoint>>& samples) {
    bool haveSample = false;
    double firstTime = 0.0;
    double lastTime = 0.0;
    for (const auto& entry : samples) {
        if (entry.second.empty()) continue;
        double entryFirst = entry.second.front().time_s;
        double entryLast = entry.second.back().time_s;
        if (!haveSample) {
            firstTime = entryFirst;
            lastTime = entryLast;
            haveSample = true;
            continue;
        }
        if (entryFirst < firstTime) firstTime = entryFirst;
        if (entryLast > lastTime) lastTime = entryLast;
    }
    return haveSample ? lastTime - firstTime : 0.0;
}

double computeDuration(const std::map<int, std::vector<BatterySamplePoint>>& batteries) {
    return computeTimeRange(batteries);
}

// Kural tabanlı otomatik yorumlama eşikleri: gerçek loglarla kalibre edilene
// kadar makul başlangıç değerleri (CLAUDE.md'deki "gelecek özellikler" notu).
// Kullanıcı bunları frontend'deki Ayarlar penceresinden değiştirebilir; --flag
// verilmezse (bkz. main()) burada tanımlı varsayılanlar kullanılır.
constexpr double DEFAULT_VOLTAGE_SAG_WARNING_THRESHOLD = 0.15;          // ilk voltaja göre %15 düşüş
constexpr double DEFAULT_CURRENT_IMBALANCE_THRESHOLD = 0.20;            // motor/batarya arası genel ortalamadan %20 sapma
constexpr double DEFAULT_NEGATIVE_CURRENT_WARNING_THRESHOLD_A = -0.1;   // bu değerin altı saf sensör gürültüsünden öte, anlamlı negatif sayılır

// Üç eşik hep birlikte taşındığı için (computeWarnings ve iki yardımcısına)
// tek tek parametre yerine tek bir struct olarak geçiliyor.
struct WarningThresholds {
    double voltageSag = DEFAULT_VOLTAGE_SAG_WARNING_THRESHOLD;
    double currentImbalance = DEFAULT_CURRENT_IMBALANCE_THRESHOLD;
    double negativeCurrent = DEFAULT_NEGATIVE_CURRENT_WARNING_THRESHOLD_A;
};

// Gerçek loglarla ilk kalibrasyon denemesinde şu görüldü: çok kısa (ör. arm
// öncesi/idle, birkaç saniyelik) kayıtlarda akım/voltaj örnekleri anlamlı bir
// uçuş dinamiği yansıtmıyor ve kurallar yanlış alarm üretebiliyor (ör. 9
// örneklik, ~1.6 saniyelik bir idle logda motorlar arasında %59 "dengesizlik"
// uyarısı çıkmıştı — gerçekte uçuş bile yoktu). Bu yüzden bir batarya/motor
// grubunun ilk ve son örneği arasındaki süre bu eşiğin altındaysa o grup için
// kural hiç değerlendirilmiyor.
constexpr double MIN_DURATION_FOR_WARNINGS_S = 5.0;

// Yüzdeyi tam sayıya yuvarlar (ekstra <cmath> bağımlılığı almamak için elle).
int roundToPercent(double ratio) {
    return static_cast<int>(ratio * 100.0 + 0.5);
}

// Bir grubun (motor ya da batarya) her elemanının ortalama akımını genel
// ortalamayla karşılaştırıp eşik aşıldığında uyarı ekler. Motor ve batarya
// dengesizlik kuralları birebir aynı mantığı kullandığı için ortak bu
// yardımcıya çıkarıldı (computeWarnings içinde iki kullanım noktası).
void appendImbalanceWarnings(const std::map<int, double>& averages, const std::string& labelPrefix,
                              std::vector<std::string>& warnings, double currentImbalanceThreshold) {
    if (averages.empty()) return;

    double overallMean = 0.0;
    for (const auto& entry : averages) overallMean += entry.second;
    overallMean /= static_cast<double>(averages.size());
    if (overallMean <= 0.0) return;

    for (const auto& entry : averages) {
        double deviation = (entry.second - overallMean) / overallMean;
        if (deviation >= currentImbalanceThreshold || deviation <= -currentImbalanceThreshold) {
            std::ostringstream msg;
            msg << labelPrefix << " " << entry.first << ": ortalama akımı diğerlerinden %"
                << roundToPercent(deviation < 0 ? -deviation : deviation)
                << (deviation > 0 ? " daha fazla." : " daha az.");
            warnings.push_back(msg.str());
        }
    }
}

// Her batarya/motor için akım örneklerinin en küçüğünü bulup eşik altına
// düşenler için ayrı bir "veri kalitesi" uyarısı üretir. Fiziksel olarak ESC/
// batarya akımı negatif olmaz; ufak negatif değerler genelde sensör
// gürültüsü/kalibrasyon sapmasıdır, gerçek bir arıza değildir — bu yüzden
// dengesizlik uyarılarından farklı, daha yumuşak bir dille işaretleniyor.
template <typename SamplePoint>
void appendNegativeCurrentWarnings(const std::map<int, std::vector<SamplePoint>>& groups,
                                    const std::string& labelPrefix,
                                    std::vector<std::string>& warnings, double negativeCurrentThreshold) {
    for (const auto& entry : groups) {
        const auto& points = entry.second;
        if (points.empty()) continue;
        if (points.back().time_s - points.front().time_s < MIN_DURATION_FOR_WARNINGS_S) continue;
        if (!hasCurrentData(points)) continue;  // akım sensörü yok; "ölçüm" diye bir şey yok

        double minCurrent = points.front().current_a;
        for (const auto& point : points) {
            if (point.current_a < minCurrent) minCurrent = point.current_a;
        }
        if (minCurrent < negativeCurrentThreshold) {
            std::ostringstream msg;
            msg << labelPrefix << " " << entry.first << ": akım verisinde negatif değer görüldü ("
                << minCurrent << " A) — muhtemelen sensör gürültüsü, gerçek bir arıza olmayabilir.";
            warnings.push_back(msg.str());
        }
    }
}

// Batarya voltaj düşümü, motor/batarya akım dengesizliği ve negatif akım veri
// kalitesi kontrolü için basit, eşik tabanlı kurallar uygulayıp insan-okunur
// uyarı metinleri üretir. Yapay zeka/ML gerektirmez (bkz. CLAUDE.md "Kapsam
// dışı bırakılan fikirler").
std::vector<std::string> computeWarnings(const ParsedLog& log, const WarningThresholds& thresholds = WarningThresholds{}) {
    std::vector<std::string> warnings;

    for (const auto& entry : log.batteries) {
        const std::vector<BatterySamplePoint>& points = entry.second;
        if (points.empty()) continue;
        if (points.back().time_s - points.front().time_s < MIN_DURATION_FOR_WARNINGS_S) continue;

        double first = points.front().voltage_v;
        double minVoltage = first;
        for (const auto& point : points) {
            if (point.voltage_v < minVoltage) minVoltage = point.voltage_v;
        }
        if (first <= 0.0) continue;  // bozuk/eksik veri; oran anlamsız olur

        double sagRatio = (first - minVoltage) / first;
        if (sagRatio >= thresholds.voltageSag) {
            std::ostringstream msg;
            msg << "Batarya " << entry.first << ": voltaj %" << roundToPercent(sagRatio)
                << " düştü (" << first << "V → " << minVoltage << "V).";
            warnings.push_back(msg.str());
        }
    }

    appendNegativeCurrentWarnings(log.batteries, "Batarya", warnings, thresholds.negativeCurrent);
    appendNegativeCurrentWarnings(log.motors, "Motor", warnings, thresholds.negativeCurrent);

    // Akım sensörü olmayan (tüm örnekleri tam 0.0) bir grup ortalamalara HİÇ
    // katılmamalı. Aksi halde bir gerçek + bir sensörsüz batarya bulunan bir
    // araçta genel ortalama yarıya iner ve İKİSİ birden "%100 sapma" uyarısı
    // üretir — tamamen yanlış bir alarm, çünkü sensörsüz olanın ölçümü yok.
    std::map<int, double> batteryAverages;
    for (const auto& entry : log.batteries) {
        const std::vector<BatterySamplePoint>& points = entry.second;
        if (points.empty()) continue;
        if (points.back().time_s - points.front().time_s < MIN_DURATION_FOR_WARNINGS_S) continue;
        if (!hasCurrentData(points)) continue;
        double sum = 0.0;
        for (const auto& point : points) sum += point.current_a;
        batteryAverages[entry.first] = sum / static_cast<double>(points.size());
    }
    appendImbalanceWarnings(batteryAverages, "Batarya", warnings, thresholds.currentImbalance);

    std::map<int, double> motorAverages;
    for (const auto& entry : log.motors) {
        const std::vector<MotorSamplePoint>& points = entry.second;
        if (points.empty()) continue;
        if (points.back().time_s - points.front().time_s < MIN_DURATION_FOR_WARNINGS_S) continue;
        if (!hasCurrentData(points)) continue;  // akım bildirmeyen ESC (bkz. batarya tarafındaki not)
        double sum = 0.0;
        for (const auto& point : points) sum += point.current_a;
        motorAverages[entry.first] = sum / static_cast<double>(points.size());
    }
    appendImbalanceWarnings(motorAverages, "Motor", warnings, thresholds.currentImbalance);

    return warnings;
}

// Araç tipine özel eşikler: "--vehicle-thresholds=rover:0.12:0.25:-0.1"
// biçimindeki (tekrarlanabilir) argümandan doldurulur.
//
// Neden burada, frontend'de değil: eşiklerin backend'e verilmesi gerekiyor
// ama araç tipi ancak log AYRIŞTIRILDIKTAN sonra biliniyor. Frontend tüm
// tiplerin eşiklerini birden geçiriyor, doğru olanı burada seçiyoruz —
// böylece log tek geçişte okunuyor.
//
// Eşleşen bir tip yoksa genel eşikler (--voltage-sag vb. ya da DEFAULT_*)
// kullanılır; yani bu tamamen opsiyonel bir üst katman.
using VehicleThresholdMap = std::map<std::string, WarningThresholds>;

const WarningThresholds& selectThresholds(const VehicleThresholdMap& perVehicle,
                                           const WarningThresholds& fallback,
                                           const std::string& vehicleType) {
    auto it = perVehicle.find(vehicleType);
    return it != perVehicle.end() ? it->second : fallback;
}

// "rover:0.12:0.25:-0.1" -> perVehicle["rover"]. Bozuk/eksik bir değer varsa
// o kayıt sessizce yok sayılır (diğer --flag'lerdeki davranışın aynısı: bunlar
// kullanıcının ayarlar penceresinden gelen opsiyonel değerler).
void parseVehicleThresholdArg(const std::string& value, VehicleThresholdMap& perVehicle) {
    std::vector<std::string> parts;
    std::stringstream stream(value);
    std::string part;
    while (std::getline(stream, part, ':')) parts.push_back(part);
    if (parts.size() != 4 || parts[0].empty()) return;

    try {
        WarningThresholds thresholds;
        thresholds.voltageSag = std::stod(parts[1]);
        thresholds.currentImbalance = std::stod(parts[2]);
        thresholds.negativeCurrent = std::stod(parts[3]);
        perVehicle[parts[0]] = thresholds;
    } catch (const std::exception&) {
        // Sayıya çevrilemeyen bir değer: bu araç tipi için özel eşik yok sayılır.
    }
}

// Basarili olursa (en az bir batarya/motor/PWM verisi bulunduysa) true doner.
bool writePowerLogJson(const std::string& inputLogPath, const std::string& outputPath,
                       const WarningThresholds& thresholds,
                       const VehicleThresholdMap& perVehicleThresholds = {}) {
    ParsedLog parsed = parseLog(inputLogPath);

    std::ofstream out(toPath(outputPath));
    if (!out.is_open()) {
        std::cerr << "Cikti dosyasi acilamadi: " << outputPath << std::endl;
        return false;
    }
    // Varsayilan ostream hassasiyeti (6 anlamli basamak) uzun ucuslarda
    // time_s'i yuvarlayip alt-saniye cozunurlugu kaybediyordu (ornek:
    // 12345.6789 -> "12345.7"). double'in tasiyabildigi ~15-17 basamaga
    // cikariyoruz.
    out << std::setprecision(15);

    double duration_s = computeDuration(parsed.batteries);
    // Bataryası olmayan (ör. gerçek bir ArduPilot QuadPlane SITL logu --
    // birçoğu güç izleme simüle etmiyor ama gerçek çok motorlu ESC/PWM
    // verisi taşıyor) loglarda süreyi motor/PWM zaman damgalarından
    // hesapla; aksi halde duration_s hep 0 kalırdı.
    if (duration_s == 0.0 && parsed.batteries.empty()) {
        duration_s = computeTimeRange(parsed.motors);
        if (duration_s == 0.0) {
            duration_s = computeTimeRange(parsed.pwmOutputs);
        }
    }
    std::vector<std::string> warnings = computeWarnings(
        parsed, selectThresholds(perVehicleThresholds, thresholds, parsed.vehicleType));

    out << "{\n";
    out << "  \"meta\": {\n";
    out << "    \"source_file\": ";
    writeJsonString(out, inputLogPath);
    out << ",\n";
    out << "    \"format\": ";
    writeJsonString(out, parsed.format);
    out << ",\n";
    out << "    \"vehicle_type\": ";
    writeJsonString(out, parsed.vehicleType);
    out << ",\n";
    out << "    \"duration_s\": " << duration_s << "\n";
    out << "  },\n";
    writeBatteries(out, parsed.batteries, parsed.capacityUsedMah, parsed.remainingPct, parsed.temperaturesC);
    out << ",\n";
    writeMotors(out, parsed.motors);
    out << ",\n";
    writePwmOutputs(out, parsed.pwmOutputs);
    out << ",\n  \"warnings\": ";
    writeStringArray(out, warnings);
    out << "\n}\n";

    std::cout << "JSON yazildi: " << outputPath << " (" << parsed.format << ", " << parsed.batteries.size()
              << " batarya, " << parsed.motors.size() << " motor, " << warnings.size() << " uyari)" << std::endl;

    if (parsed.batteries.empty()) {
        // Batarya yoksa da motor/PWM verisi varsa log yine islenebilir --
        // gercek bir ArduPilot QuadPlane SITL logunda goruldu: cogu boyle
        // bir test guc izleme simule etmiyor ama gercek coklu-motor ESC/PWM
        // verisi tasiyor. Sadece HICBIR kullanisli veri yoksa reddediliyor.
        bool hasOtherUsefulData = !parsed.motors.empty() || !parsed.pwmOutputs.empty();
        if (!hasOtherUsefulData) {
            if (parsed.hasSystemPowerTopic) {
                // Ozellikle bazi Rover yapilandirmalarinda goruldu: bu arac ana
                // batarya (battery_status) yerine sadece dahili guc hatlarini
                // (system_power - 5V/payload rayi, ana batarya degil) logluyor.
                std::cerr << "Uyari: bu log dosyasinda ana batarya (battery_status) verisi yok, "
                             "sadece dahili guc hatti (system_power) verisi bulundu. Bu arac/"
                             "yapilandirma su an desteklenmiyor."
                          << std::endl;
            } else {
                std::cerr << "Uyari: dosyada batarya verisi bulunamadi. Desteklenmeyen ya da bozuk "
                             "bir log dosyasi olabilir (ArduPilot .bin ya da PX4 .ulog bekleniyor)."
                          << std::endl;
            }
            return false;
        }
        std::cerr << "Bilgi: bu logda batarya verisi yok, sadece motor/PWM verisiyle devam "
                     "ediliyor."
                  << std::endl;
    }
    return true;
}

#ifdef _WIN32
std::string wideToUtf8(const wchar_t* wide) {
    int size = WideCharToMultiByte(CP_UTF8, 0, wide, -1, nullptr, 0, nullptr, nullptr);
    std::string utf8(size > 0 ? size - 1 : 0, '\0');
    if (size > 0) {
        WideCharToMultiByte(CP_UTF8, 0, wide, -1, utf8.data(), size, nullptr, nullptr);
    }
    return utf8;
}

// Windows'ta main()'e gelen argv, sistemin ANSI kod sayfasında kodlanmıştır;
// Türkçe karakterler içeren yollarda bu bozulmaya yol açar. Komut satırını
// burada Unicode (wide) olarak yeniden okuyup UTF-8'e çeviriyoruz.
std::vector<std::string> getUtf8Args() {
    int wargc = 0;
    LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    std::vector<std::string> args;
    for (int i = 0; i < wargc; ++i) {
        args.push_back(wideToUtf8(wargv[i]));
    }
    LocalFree(wargv);
    return args;
}
#endif

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    std::vector<std::string> args = getUtf8Args();
#else
    std::vector<std::string> args(argv, argv + argc);
#endif

    if (args.size() < 3) {
        std::cerr << "Kullanim: power_log_backend <girdi_log.bin> <cikti.json> "
                     "[--voltage-sag=0.15] [--current-imbalance=0.20] [--negative-current=-0.1] "
                     "[--vehicle-thresholds=<tip>:<sag>:<dengesizlik>:<negatif> ...]"
                  << std::endl;
        return 1;
    }

    // Uyarı eşikleri için opsiyonel argümanlar (frontend'in Ayarlar
    // penceresinden geçirilir). Verilmezse yukarıdaki DEFAULT_* değerleri
    // kullanılır (WarningThresholds'un kendi varsayılanları). Tanınmayan/
    // hatalı bir flag sessizce yok sayılır — bu, kullanıcının kendi ayarlar
    // penceresinden girdiği sayılar için sıkı bir doğrulama gerektirmeyen,
    // opsiyonel bir özellik.
    WarningThresholds thresholds;
    VehicleThresholdMap perVehicleThresholds;
    for (size_t i = 3; i < args.size(); ++i) {
        try {
            if (args[i].rfind("--voltage-sag=", 0) == 0) {
                thresholds.voltageSag = std::stod(args[i].substr(14));
            } else if (args[i].rfind("--current-imbalance=", 0) == 0) {
                thresholds.currentImbalance = std::stod(args[i].substr(20));
            } else if (args[i].rfind("--negative-current=", 0) == 0) {
                thresholds.negativeCurrent = std::stod(args[i].substr(19));
            } else if (args[i].rfind("--vehicle-thresholds=", 0) == 0) {
                // Tekrarlanabilir: her araç tipi için ayrı bir tane verilir.
                parseVehicleThresholdArg(args[i].substr(21), perVehicleThresholds);
            }
        } catch (const std::exception&) {
            // Sayıya çevrilemeyen bir değer geldiyse o eşik varsayılanında kalır.
        }
    }

    bool ok = writePowerLogJson(args[1], args[2], thresholds, perVehicleThresholds);
    return ok ? 0 : 2;
}
