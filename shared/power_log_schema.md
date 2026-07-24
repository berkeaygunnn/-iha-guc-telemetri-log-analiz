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
      "current_a": [number, ...], // o bataryanın akımı
      "capacity_used_mah": number | null, // kümülatif tüketilen kapasite (log
                                           // içeriyorsa: ArduPilot "CurrTot",
                                           // PX4 "discharged_mah"; yoksa null)
      "remaining_pct": number | null,     // kalan batarya yüzdesi 0-100 (log
                                           // içeriyorsa: ArduPilot "RemPct",
                                           // PX4 "remaining" *100; yoksa null)
      "temperature_c": [number, ...]      // batarya sıcaklığı (°C), time_s ile
                                           // aynı uzunlukta (ArduPilot "Temp",
                                           // PX4 "temperature"); log içermiyorsa
                                           // boş dizi []
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

`capacity_used_mah`/`remaining_pct` bir zaman serisi DEĞİL — o bataryada
görülen son (en güncel) tek değer. Log formatı bu alanları içermiyorsa
(eski ArduPilot logları, ya da bu alanları yayınlamayan PX4 araçları) `null`
olur; bu bir hata değildir, frontend bu durumda ilgili kutucukta "—" gösterir.

`temperature_c` ise (`capacity_used_mah`in aksine) bir zaman serisi — `voltage_v`/
`current_a` gibi `time_s` ile aynı uzunlukta. Log bu alanı hiç içermiyorsa
dizi boştur (`[]`); frontend bu durumda "Sıcaklık" görünümünde o bataryayı
çizmez.

## `warnings` alanı

Backend, basit eşik tabanlı kurallarla (yapay zeka/ML yok) otomatik uyarı
cümleleri üretir; frontend bunları olduğu gibi gösterir. v1 kapsamındaki
kurallar (bkz. `backend/src/main.cpp` içindeki `computeWarnings`):

- **Voltaj düşümü**: bir bataryanın ilk örnek voltajına göre en düşük voltajı
  %15 veya daha fazla düştüyse uyarı üretilir.
- **Motor akım dengesizliği**: bir motorun ortalama akımı, tüm motorların
  ortalamalarının genel ortalamasından %20 veya daha fazla saparsa uyarı üretilir.
- **Batarya akım dengesizliği**: motor kuralının birebir eşleniği — birden
  fazla batarya varsa, birinin ortalama akımı tüm bataryaların genel
  ortalamasından %20 veya daha fazla saparsa uyarı üretilir (ör. yedekli güç
  hatlarından birinin diğerinden belirgin daha fazla/az yük çekmesi).
- **Negatif akım (veri kalitesi)**: bir batarya ya da motorun akım
  örneklerinden en küçüğü -0.1A'nın altındaysa ayrı bir uyarı üretilir.
  Fiziksel olarak ESC/batarya akımı negatif olmaz; bu genelde gerçek bir
  arızadan çok sensör gürültüsü/kalibrasyon sapmasına işaret eder, bu yüzden
  metni diğer kurallardan daha yumuşak ("muhtemelen sensör gürültüsü, gerçek
  bir arıza olmayabilir").

Uyarı yoksa dizi boş (`[]`) döner. Eşikler `main.cpp`'de adlandırılmış sabitler.
PX4'ün herkese açık flight review veritabanından indirilen gerçek, çeşitli
uçuşlarla (30-45 dakikalık, gerçek donanımlı quadrotor uçuşları) test edildi:
sağlıklı bir uçuşta motorlar arası en yüksek sapma ~%14 çıktı (eşiğin altında,
uyarı üretmedi — beklenen), 45 dakikalık ve bataryası iyice boşalmış bir
uçuşta voltaj düşümü ~%21 çıktı (eşiğin üstünde, uyarı üretti — fiziksel olarak
anlamlı: 6S bir batarya dolu-den boşalmaya gitmiş). Bu, %15/%20 eşiklerinin
makul bir aralıkta olduğunu destekliyor; kesin bir arıza örneğiyle (ör.
bilinen bozuk bir motor) doğrulanmadı, o yüzden hâlâ kesin/nihai değerler
olarak değil, gerçek veriyle desteklenmiş başlangıç değerleri olarak görülmeli.

Ayrıca: bir batarya/motorun ilk ve son örneği arasındaki süre 5 saniyeden
kısaysa (`MIN_DURATION_FOR_WARNINGS_S`) o grup için kural hiç değerlendirilmez.
Bu, gerçek loglarla ilk kalibrasyon denemesinde bulundu: çok kısa (arm-öncesi/
idle) kayıtlarda örnekler gerçek uçuş dinamiği yansıtmadığı için yanlış alarma
yol açabiliyordu.

Örnek dosya: `power_log_example.json` — backend'in gerçek ArduPilot/PX4
loglarından ürettiği JSON da bu yapıya uyar.
