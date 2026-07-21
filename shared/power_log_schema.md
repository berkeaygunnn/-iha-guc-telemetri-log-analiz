# Güç Log JSON Şeması (backend ↔ frontend sözleşmesi)

Backend, bir log dosyasını (.bin/.ulog) işledikten sonra bu şemaya uygun bir
JSON dosyası üretir. Frontend sadece bu JSON'u okur, log formatlarıyla hiç
uğraşmaz.

MVP kapsamı: batarya voltaj/akımı ve motor bazlı akım çekimi (zaman serisi).
Birden fazla batarya ya da motor olabileceği için ikisi de dizi.

```
{
  "meta": {
    "source_file": string,   // orijinal log dosyasının adı
    "format": "ardupilot" | "px4",
    "duration_s": number     // uçuşun toplam süresi (tüm bataryaların en sonuncusu)
  },
  "batteries": [
    {
      "id": number,              // batarya sırası (1, 2, ...)
      "time_s": [number, ...],    // örnekleme zaman damgaları (saniye)
      "voltage_v": [number, ...], // batteries[i].time_s ile aynı uzunlukta
      "current_a": [number, ...]  // o bataryanın akımı
    }
  ],
  "motors": [
    {
      "id": number,              // motor/ESC sırası (1, 2, 3, ...)
      "time_s": [number, ...],
      "current_a": [number, ...] // o motorun çektiği akım
    }
  ],
  "warnings": [string, ...]  // kural tabanlı, hazır gösterilecek uyarı cümleleri (bkz. aşağı)
}
```

Kural: her `time_s` dizisiyle eşleştiği veri dizisi (`voltage_v`,
`current_a`, ...) her zaman aynı uzunlukta olmalı.

## `warnings` alanı

Backend, basit eşik tabanlı kurallarla (yapay zeka/ML yok) otomatik uyarı
cümleleri üretir; frontend bunları olduğu gibi gösterir. v1 kapsamındaki
kurallar (bkz. `backend/src/main.cpp` içindeki `computeWarnings`):

- **Voltaj düşümü**: bir bataryanın ilk örnek voltajına göre en düşük voltajı
  %15 veya daha fazla düştüyse uyarı üretilir.
- **Motor akım dengesizliği**: bir motorun ortalama akımı, tüm motorların
  ortalamalarının genel ortalamasından %20 veya daha fazla saparsa uyarı üretilir.

Uyarı yoksa dizi boş (`[]`) döner. Eşikler `main.cpp`'de adlandırılmış sabitler;
gerçek uçuş verisiyle kalibre edilene kadar başlangıç değerleridir.

Ayrıca: bir batarya/motorun ilk ve son örneği arasındaki süre 5 saniyeden
kısaysa (`MIN_DURATION_FOR_WARNINGS_S`) o grup için kural hiç değerlendirilmez.
Bu, gerçek loglarla ilk kalibrasyon denemesinde bulundu: çok kısa (arm-öncesi/
idle) kayıtlarda örnekler gerçek uçuş dinamiği yansıtmadığı için yanlış alarma
yol açabiliyordu.

Örnek dosya: `power_log_example.json` — backend'in gerçek ArduPilot/PX4
loglarından ürettiği JSON da bu yapıya uyar.
