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
  ]
}
```

Kural: her `time_s` dizisiyle eşleştiği veri dizisi (`voltage_v`,
`current_a`, ...) her zaman aynı uzunlukta olmalı.

Örnek dosya: `power_log_example.json` — backend'in gerçek ArduPilot/PX4
loglarından ürettiği JSON da bu yapıya uyar.
