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
    "vehicle_type": "multirotor" | "fixed_wing" | "rover" | "vtol" |
                    "airship" | "submarine" | "unknown",  // bkz. aşağı
    "duration_s": number     // uçuşun toplam süresi (tüm bataryaların en sonuncusu)
  },
  "batteries": [
    {
      "id": number,              // batarya sırası (1, 2, ...)
      "time_s": [number, ...],    // örnekleme zaman damgaları (saniye)
      "voltage_v": [number, ...], // batteries[i].time_s ile aynı uzunlukta
      "current_a": [number, ...], // o bataryanın akımı
      "has_current_data": boolean, // current_a gerçek bir ölçüm mü (bkz. aşağı)
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
      "current_a": [number, ...], // o motorun çektiği akım
      "has_current_data": boolean // bataryadaki alanın birebir eşleniği
    }
  ],
  "pwm_outputs": [
    {
      "id": number,            // sadece gösterim sırası/renk indeksi
      "label": string,         // kanalın adı: "MAIN 2", "AUX 1", "Kanal 3"
      "time_s": [number, ...],
      "pwm_us": [number, ...]  // darbe genişliği (mikrosaniye), time_s ile aynı uzunlukta
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

## `has_current_data` alanı

Akım sensörü bağlı **değilse** ArduPilot/PX4 `current_a` alanını boş
bırakmaz — her örneğe **tam 0.0** yazar. Yani JSON'daki sıfırlar iki farklı
anlama gelebilir: "ölçüm yapıldı, sonuç 0 A" ya da "ölçüm diye bir şey yok".
Backend bu ikisini ayırır: bir batarya/motorun **tüm** `current_a` örnekleri
tam 0.0 ise `has_current_data: false` olur.

Eşik yok, kural katı (bir tek örnek bile sıfırdan farklıysa `true`). Sebebi:
gerçek bir sensör araç dururken bile küçük bir gürültü/kalibrasyon ofseti
üretir — `data/px4_hexarotor_flight.ulg`'de en düşük değerler 0.26 A ve
-0.73 A. Tam sıfırdan oluşan bir dizi pratikte ancak sensör yokken oluşur;
`data/px4_ground_rover_flight.ulg`'de 452 örneğin hepsi 0.00 A çıktı.

Frontend bu bayrağa göre davranır:

- Akım/motor panelinde o seriyi çizmez; hiçbirinde ölçüm yoksa düz bir sıfır
  çizgisi (ya da tek renk ısı haritası) yerine "Bu logda akım sensörü verisi
  yok." mesajı gösterir.
- Akıma dayanan istatistik kutucuklarını (akım aralığı, enerji, tepe güç, iç
  direnç, tüketilen kapasite, kalan süre) `0` yerine `—` gösterir.

Backend de aynı bayrağa göre uyarı kurallarını uygular: ölçümü olmayan bir
grup ne dengesizlik ne negatif-akım kuralına girer. Bu, somut bir yanlış
alarmı önlüyor — biri sensörlü biri sensörsüz iki bataryası olan bir araçta
genel ortalama yarıya iniyor ve **ikisi birden** "%100 sapma" uyarısı
üretiyordu.

Bu durum rover'a özgü değil: akım sensörsüz sabit kanat ve eski ArduPilot
logları da aynı duruma düşüyor (`ArduCopter-SensorErrorFlags-00000012.BIN`
bunun bir örneği).

## `pwm_outputs` alanı

Uçuş kontrolcüsünün çıkış kanallarına gönderdiği PWM darbe genişliği
(mikrosaniye, tipik aralık 1000–2000). Kaynak: PX4'te `actuator_outputs`,
ArduPilot'ta `RCOU` (`C1`..`C14`).

**Bu bir güç ölçümü DEĞİL, kontrol çıktısıdır.** Akım/voltaj gibi ölçülmüş bir
büyüklük değil, kontrolcünün motorlara/servolara verdiği komut. Şemaya
girmesinin sebebi pratik: akım sensörü olmayan araçlarda (birçok rover, bkz.
`has_current_data`) motor aktivitesinin tek görünür kanıtı bu.

Bu yüzden `pwm_outputs` **uyarı kurallarına hiç girmez** — dengesizlik ve
voltaj düşümü kuralları akım/voltaj içindir.

### Kanal ≠ motor

Aynı çıkış dizisinde motor, servo, direksiyon, gimbal karışık durur ve
**hangi kanalın ne olduğu logda yazmaz.** Bu yüzden backend tahmin yürütmez;
kanallar donanımdaki adlarıyla aktarılır ve `label` alanında hazır gelir
(frontend'in format bilgisine ihtiyacı olmasın diye):

- PX4, instance 0 → `"MAIN 1"`, `"MAIN 2"`, ...
- PX4, instance 1 → `"AUX 1"`, `"AUX 2"`, ...
- PX4, diğer instance'lar → `"OUT2 1"`, ...
- ArduPilot → `"Kanal 1"`, `"Kanal 2"`, ...

Tüm instance'lar aktarılır, biri "asıl motor grubu" diye seçilmez: gerçek
loglarda motorların hangi grupta olduğu araca göre değişiyor —
`px4_hexarotor_flight.ulg`'de 6 motor **AUX**'ta, `px4_ground_rover_flight.ulg`'de
hareketli kanallar **MAIN**'de. PX4 tarafında okunacak kanal sayısı
`noutputs` alanıyla sınırlanır (`output` sabit uzunluklu bir dizidir, gerisi
kullanılmaz).

### Sabit kanallar yazılmaz

Bir aracın çıkış rayında kullanılmayan kanallar da bulunur ve bunlar log
boyunca sabit bir değerde durur (kullanılmıyorsa 0, servo nötr konumu için
1500, kilitli motor için 1000). Değeri hiç değişmeyen (`min == max`) kanallar
JSON'a **hiç yazılmaz**: çıktıyı gereksiz şişirirler (tek bir rover logu için
~29.000 anlamsız sayı) ve arayüzde düz çizgi olarak gürültü yaparlar.
Sonuç olarak `px4_ground_rover_flight.ulg`'de 4 kanaldan 2'si, hexarotor'un
MAIN grubunda ise bir kanal eleniyor.

Log hiç çıkış mesajı içermiyorsa ya da tüm kanallar sabitse dizi boş (`[]`)
olur; frontend bu durumda "Bu logda PWM çıkış verisi yok." mesajını gösterir.

## `vehicle_type` alanı

Aracın tipi. PX4'te `vehicle_status` konusundaki `vehicle_type` sayısal
alanından (1=rotary_wing, 2=fixed_wing, 3=rover, 4=airship), ArduPilot'ta
ise açılışta yazılan `MSG` satırlarındaki firmware adından (`ArduCopter`,
`ArduPlane`, `ArduRover`/`APMrover`, `ArduSub`) çıkarılır. Log bu bilgiyi
içermiyorsa (sentetik test dosyaları, çok eski loglar) `"unknown"` olur.

VTOL araçlar `vehicle_type`'ı uçuş fazına göre rotary_wing ile fixed_wing
arasında değiştirir; ayrı bir `is_vtol` bayrağı taşıdıkları için tek ve
sabit bir etiket üretmek adına ona öncelik verilir (`"vtol"`).

**Bu alan yalnızca bilgi amaçlıdır** — frontend'de dosya adının yanında bir
etiket olarak gösterilir. Hangi panellerin çizileceği buna göre **belirlenmez**;
o karar `has_current_data`'ya bakar. Sebebi ölçülmüş bir gerçek: araç tipi,
verinin varlığı için güvenilir bir sinyal değil. Örnek loglarda `fixed_wing`
ve `multirotor` tiplerinin hem ESC telemetrisi olanı hem olmayanı var
(`px4_fixed_wing_flight.ulg` ve `px4_sample_log_small.ulg` esc_status
içermiyor, `px4_hexarotor_flight.ulg` içeriyor). "Rover ise motor panelini
gizle" gibi bir kural, akım sensörlü bir rover'da paneli haksız yere gizler,
ESC'siz bir multirotor'da ise sahte sıfırı göstermeye devam ederdi.

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

### Araç tipine göre eşikler

Eşikler araç tipi başına ayarlanabilir. Frontend, tanımlı **tüm** tiplerin
eşiklerini tek seferde `--vehicle-thresholds=<tip>:<sag>:<dengesizlik>:<negatif>`
argümanlarıyla (tekrarlanabilir) geçirir; backend log'u ayrıştırıp
`meta.vehicle_type`'ı belirledikten sonra eşleşeni seçer, eşleşen yoksa genel
eşikler (`--voltage-sag` vb.) geçerli olur. Bu sıra zorunlu: eşikler çağrı
anında veriliyor ama araç tipi ancak ayrıştırmadan sonra biliniyor — böylece
log yine **tek geçişte** okunuyor.

Varsayılanlar tüm araç tiplerinde **aynıdır**. Örnek loglar ölçüldüğünde
voltaj düşümü her tipte %0.5–6.2 aralığında çıktı (eşik %15) ve tipler
arasında anlamlı bir ayrışma görülmedi; dengesizlik ise her tipte yalnızca
1–2 logda ölçülebiliyor. Tipe özel sayılar uydurmak yerine yapı kurulup
kalibrasyon kullanıcıya bırakıldı.

Ayrıca: bir batarya/motorun ilk ve son örneği arasındaki süre 5 saniyeden
kısaysa (`MIN_DURATION_FOR_WARNINGS_S`) o grup için kural hiç değerlendirilmez.
Bu, gerçek loglarla ilk kalibrasyon denemesinde bulundu: çok kısa (arm-öncesi/
idle) kayıtlarda örnekler gerçek uçuş dinamiği yansıtmadığı için yanlış alarma
yol açabiliyordu.

Örnek dosya: `power_log_example.json` — backend'in gerçek ArduPilot/PX4
loglarından ürettiği JSON da bu yapıya uyar.
