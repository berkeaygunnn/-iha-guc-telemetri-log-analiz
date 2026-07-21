// İHA Güç/Telemetri Log Analiz Aracı - Backend giriş noktası.
//
// ArduPilot .bin dosyaları "kendini tanımlayan" bir formatta: dosyanın
// başında/ilk kullanımdan önce her mesaj tipi için bir FMT mesajı gelir ve bu
// mesaj, o tipin alanlarının (Volt, Curr, ...) isimlerini ve byte boyutlarını
// tanımlar. Biz de önce FMT mesajlarını okuyup bir "sözlük" oluşturuyoruz,
// sonra "BAT" mesajlarını bu sözlüğe göre çözüp voltaj/akım verisini çıkarıyoruz.
//
// Kaynak: https://github.com/ArduPilot/ardupilot/blob/master/libraries/AP_Logger/LogStructure.h

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
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

// Batarya (BAT) ve motor (ESC) mesajlarından çıkarılan tüm veriler.
// Her ikisi de anahtarı 1'den başlayan motor/batarya no olan bir map: birden
// fazla batarya/motor varsa hepsi ayrı ayrı tutulur.
struct ParsedLog {
    std::string format;  // "ardupilot" ya da "px4"
    std::map<int, std::vector<BatterySamplePoint>> batteries;
    std::map<int, std::vector<MotorSamplePoint>> motors;
};

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

FieldLocator locateField(const FormatDef& def, const std::string& fieldName) {
    size_t offset = 0;
    for (size_t i = 0; i < def.labels.size() && i < def.format.size(); ++i) {
        if (def.labels[i] == fieldName) {
            return FieldLocator{true, offset, def.format[i]};
        }
        offset += fieldByteSize(def.format[i]);
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
void extractBatterySample(const uint8_t* payload, const FormatDef& def,
                           std::map<int, std::vector<BatterySamplePoint>>& batteries) {
    FieldLocator timeField = locateField(def, "TimeUS");
    FieldLocator voltField = locateField(def, "Volt");
    FieldLocator currField = locateField(def, "Curr");
    FieldLocator instField = locateField(def, "Inst");

    if (!timeField.found || !voltField.found || !currField.found) return;

    int instance = 0;
    if (instField.found) {
        instance = static_cast<int>(
            readFieldAsDouble(payload + instField.byteOffset, instField.formatChar));
    }

    double timeUs = readFieldAsDouble(payload + timeField.byteOffset, timeField.formatChar);
    double volt = readFieldAsDouble(payload + voltField.byteOffset, voltField.formatChar);
    double curr = readFieldAsDouble(payload + currField.byteOffset, currField.formatChar);

    batteries[instance + 1].push_back(BatterySamplePoint{timeUs / 1e6, volt, curr});
}

// "ESC" mesajının payload'ından TimeUS/Instance/Curr alanlarını çıkarır ve
// motor numarasına (Instance + 1) göre gruplar.
void extractEscSample(const uint8_t* payload, const FormatDef& def,
                       std::map<int, std::vector<MotorSamplePoint>>& motors) {
    FieldLocator timeField = locateField(def, "TimeUS");
    FieldLocator instField = locateField(def, "Instance");
    FieldLocator currField = locateField(def, "Curr");

    if (!timeField.found || !instField.found || !currField.found) return;

    double timeUs = readFieldAsDouble(payload + timeField.byteOffset, timeField.formatChar);
    int instance = static_cast<int>(
        readFieldAsDouble(payload + instField.byteOffset, instField.formatChar));
    double curr = readFieldAsDouble(payload + currField.byteOffset, currField.formatChar);

    motors[instance + 1].push_back(MotorSamplePoint{timeUs / 1e6, curr});
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

        if (def.name == "BAT" || def.name == "CURR") {
            extractBatterySample(buffer.data() + pos + 3, def, result.batteries);
        } else if (def.name == "ESC") {
            extractEscSample(buffer.data() + pos + 3, def, result.motors);
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
    field.size = (baseSize == 0) ? 0 : baseSize * static_cast<size_t>(field.arrayLength);
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
size_t formatTotalSize(const ULogFormatDef& def) {
    size_t total = 0;
    for (const ULogField& field : def.fields) total += field.size;
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

ULogFieldLocator locateUlogField(const ULogFormatDef& def, const std::string& fieldName) {
    size_t offset = 0;
    for (const ULogField& field : def.fields) {
        if (field.name == fieldName) {
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
// batarya numarasına (multiId + 1) göre gruplar.
void extractUlogBatterySample(const uint8_t* payload, const ULogFormatDef& def, int multiId,
                               std::map<int, std::vector<BatterySamplePoint>>& batteries) {
    ULogFieldLocator timeField = locateUlogField(def, "timestamp");
    ULogFieldLocator voltField = locateUlogField(def, "voltage_v");
    ULogFieldLocator currField = locateUlogField(def, "current_a");

    if (!timeField.found || !voltField.found || !currField.found) return;

    double timestamp = readUlogFieldAsDouble(payload + timeField.byteOffset, timeField.field->elementType);
    double volt = readUlogFieldAsDouble(payload + voltField.byteOffset, voltField.field->elementType);
    double curr = readUlogFieldAsDouble(payload + currField.byteOffset, currField.field->elementType);

    batteries[multiId + 1].push_back(BatterySamplePoint{timestamp / 1e6, volt, curr});
}

// "esc_status" mesajından, içindeki "esc_report" dizisinin her elemanı için
// akım değerini çıkarır (motor numarası = dizi indeksi + 1).
void extractUlogEscSamples(const uint8_t* payload, const ULogFormatDef& escStatusDef,
                            const std::map<std::string, ULogFormatDef>& formats,
                            std::map<int, std::vector<MotorSamplePoint>>& motors) {
    ULogFieldLocator timeField = locateUlogField(escStatusDef, "timestamp");
    ULogFieldLocator escCountField = locateUlogField(escStatusDef, "esc_count");
    ULogFieldLocator escArrayField = locateUlogField(escStatusDef, "esc");

    if (!timeField.found || !escArrayField.found || escArrayField.field->arrayLength == 0) return;

    auto nestedIt = formats.find(escArrayField.field->elementType);
    if (nestedIt == formats.end()) return;
    const ULogFormatDef& escReportDef = nestedIt->second;

    ULogFieldLocator currField = locateUlogField(escReportDef, "esc_current");
    if (!currField.found) return;

    double timestamp = readUlogFieldAsDouble(payload + timeField.byteOffset, timeField.field->elementType);

    int escCount = escArrayField.field->arrayLength;
    if (escCountField.found) {
        int reported = static_cast<int>(
            readUlogFieldAsDouble(payload + escCountField.byteOffset, escCountField.field->elementType));
        if (reported >= 0 && reported < escCount) escCount = reported;
    }

    size_t elementSize = escArrayField.field->size / static_cast<size_t>(escArrayField.field->arrayLength);
    for (int i = 0; i < escCount; ++i) {
        const uint8_t* elementPtr = payload + escArrayField.byteOffset + static_cast<size_t>(i) * elementSize;
        double curr = readUlogFieldAsDouble(elementPtr + currField.byteOffset, currField.field->elementType);
        motors[i + 1].push_back(MotorSamplePoint{timestamp / 1e6, curr});
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
                                extractUlogBatterySample(payload + 2, fmtIt->second,
                                                          subIt->second.multiId, result.batteries);
                            } else if (fmtIt->second.name == "esc_status" && subIt->second.multiId == 0) {
                                extractUlogEscSamples(payload + 2, fmtIt->second, formats, result.motors);
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

// Dosyayı okuyup ilk baytlarına (magic number) göre ArduPilot ya da PX4
// parser'ına yönlendirir.
ParsedLog parseLog(const std::string& logPath) {
    std::ifstream in(toPath(logPath), std::ios::binary);
    if (!in.is_open()) {
        std::cerr << "Log dosyasi acilamadi: " << logPath << std::endl;
        return {};
    }

    std::vector<uint8_t> buffer((std::istreambuf_iterator<char>(in)),
                                 std::istreambuf_iterator<char>());

    if (isUlogFile(buffer)) {
        return parseUlogBuffer(buffer);
    }
    return parseArduPilotBuffer(buffer);
}

// JSON'da " ve \ karakterleri kaçışlanmalı; aksi halde geçersiz JSON üretilir.
void writeJsonString(std::ostream& out, const std::string& text) {
    out << '"';
    for (char c : text) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default: out << c;
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
        out << "\n    }";
        if (++written < motors.size()) out << ",";
        out << "\n";
    }
    out << "  ]";
}

void writeBatteries(std::ostream& out, const std::map<int, std::vector<BatterySamplePoint>>& batteries) {
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
        out << "\n    }";
        if (++written < batteries.size()) out << ",";
        out << "\n";
    }
    out << "  ]";
}

// Tüm bataryalar arasında en son örneğin zamanını (uçuşun toplam süresi) bulur.
double computeDuration(const std::map<int, std::vector<BatterySamplePoint>>& batteries) {
    double duration = 0.0;
    for (const auto& entry : batteries) {
        if (entry.second.empty()) continue;
        double lastTime = entry.second.back().time_s;
        if (lastTime > duration) duration = lastTime;
    }
    return duration;
}

// Kural tabanlı otomatik yorumlama eşikleri: gerçek loglarla kalibre edilene
// kadar makul başlangıç değerleri (CLAUDE.md'deki "gelecek özellikler" notu).
constexpr double VOLTAGE_SAG_WARNING_THRESHOLD = 0.15;          // ilk voltaja göre %15 düşüş
constexpr double MOTOR_CURRENT_IMBALANCE_THRESHOLD = 0.20;      // motorlar arası genel ortalamadan %20 sapma

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

// Batarya voltaj düşümü ve motor akım dengesizliği için basit, eşik tabanlı
// kurallar uygulayıp insan-okunur uyarı metinleri üretir. Yapay zeka/ML
// gerektirmez; ileride bu fonksiyonun yerini alacak bir model gelirse burası
// değiştirilir (bkz. CLAUDE.md "Gelecek özellikler").
std::vector<std::string> computeWarnings(const ParsedLog& log) {
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
        if (sagRatio >= VOLTAGE_SAG_WARNING_THRESHOLD) {
            std::ostringstream msg;
            msg << "Batarya " << entry.first << ": voltaj %" << roundToPercent(sagRatio)
                << " düştü (" << first << "V → " << minVoltage << "V)";
            warnings.push_back(msg.str());
        }
    }

    if (!log.motors.empty()) {
        std::map<int, double> motorAverages;
        for (const auto& entry : log.motors) {
            const std::vector<MotorSamplePoint>& points = entry.second;
            if (points.empty()) continue;
            if (points.back().time_s - points.front().time_s < MIN_DURATION_FOR_WARNINGS_S) continue;
            double sum = 0.0;
            for (const auto& point : points) sum += point.current_a;
            motorAverages[entry.first] = sum / static_cast<double>(points.size());
        }

        if (!motorAverages.empty()) {
            double overallMean = 0.0;
            for (const auto& entry : motorAverages) overallMean += entry.second;
            overallMean /= static_cast<double>(motorAverages.size());

            if (overallMean > 0.0) {
                for (const auto& entry : motorAverages) {
                    double deviation = (entry.second - overallMean) / overallMean;
                    if (deviation >= MOTOR_CURRENT_IMBALANCE_THRESHOLD ||
                        deviation <= -MOTOR_CURRENT_IMBALANCE_THRESHOLD) {
                        std::ostringstream msg;
                        msg << "Motor " << entry.first << ": ortalama akımı diğer motorlardan %"
                            << roundToPercent(deviation < 0 ? -deviation : deviation)
                            << (deviation > 0 ? " daha fazla" : " daha az");
                        warnings.push_back(msg.str());
                    }
                }
            }
        }
    }

    return warnings;
}

// Basarili olursa (en az bir batarya bulunduysa) true doner.
bool writePowerLogJson(const std::string& inputLogPath, const std::string& outputPath) {
    ParsedLog parsed = parseLog(inputLogPath);

    std::ofstream out(toPath(outputPath));
    if (!out.is_open()) {
        std::cerr << "Cikti dosyasi acilamadi: " << outputPath << std::endl;
        return false;
    }

    double duration_s = computeDuration(parsed.batteries);
    std::vector<std::string> warnings = computeWarnings(parsed);

    out << "{\n";
    out << "  \"meta\": {\n";
    out << "    \"source_file\": ";
    writeJsonString(out, inputLogPath);
    out << ",\n";
    out << "    \"format\": ";
    writeJsonString(out, parsed.format);
    out << ",\n";
    out << "    \"duration_s\": " << duration_s << "\n";
    out << "  },\n";
    writeBatteries(out, parsed.batteries);
    out << ",\n";
    writeMotors(out, parsed.motors);
    out << ",\n  \"warnings\": ";
    writeStringArray(out, warnings);
    out << "\n}\n";

    std::cout << "JSON yazildi: " << outputPath << " (" << parsed.format << ", " << parsed.batteries.size()
              << " batarya, " << parsed.motors.size() << " motor, " << warnings.size() << " uyari)" << std::endl;

    if (parsed.batteries.empty()) {
        std::cerr << "Uyari: dosyada batarya verisi bulunamadi. Desteklenmeyen ya da bozuk "
                     "bir log dosyasi olabilir (ArduPilot .bin ya da PX4 .ulog bekleniyor)."
                  << std::endl;
        return false;
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
        std::cerr << "Kullanim: power_log_backend <girdi_log.bin> <cikti.json>" << std::endl;
        return 1;
    }
    bool ok = writePowerLogJson(args[1], args[2]);
    return ok ? 0 : 2;
}
