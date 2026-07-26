# İHA Güç/Telemetri Log Analiz Aracı

## Proje amacı

ArduPilot (.bin) veya PX4 (.ulog) uçuş kontrolcülerinden alınan uçuş sonrası
(post-flight) log dosyalarını okuyup; motorların çektiği akımı, batarya voltaj
düşümlerini (voltage sag) ve güç dağıtım (busbar) yüklenmelerini grafiklere ve
ısı haritalarına dönüştüren bir masaüstü uygulaması.

İlk sürümde (MVP) odak: güç/batarya analizi (motor/ESC akım çekimi, voltaj
düşümü grafikleri).

## Mimari

İki dilli (polyglot) yapı, dosya tabanlı haberleşme (JSON/CSV üzerinden):

- **backend/** — C++. Görevi: log dosyasını (.bin/.ulog) okumak, parse etmek,
  ilgili güç/akım/voltaj verilerini ayıklayıp `shared/` altında tanımlı JSON
  şemasına uygun bir çıktı dosyası üretmek.
- **frontend/** — Python + CustomTkinter. Görevi: kullanıcı arayüzü. Log dosyası
  yükleme, backend'in ürettiği JSON'u okuma, Matplotlib ile grafik/ısı haritası
  çizme.
- **shared/** — backend ile frontend arasındaki ortak veri sözleşmesi (örnek
  JSON çıktı şeması burada tanımlı; iki taraf da buna göre çalışır).
- **data/** — test için örnek uçuş log dosyaları.

Backend ve frontend birbirinden bağımsız geliştirilir ve test edilir; aralarında
doğrudan bir çağrı (pybind11 vb.) YOK, sadece dosya üzerinden veri alışverişi var.

## Teknoloji tercihleri

- Backend: C++ (CMake ile derleme)
- Frontend: Python, CustomTkinter (arayüz), Matplotlib/Plotly (grafik)
- IDE: VS Code (hem backend hem frontend aynı pencerede)
- Versiyon kontrol: Git

## Hedeflenen log kaynakları

- ArduPilot .bin log formatı (GPLv3, tamamen açık/belgelenmiş format)
- PX4 .ulog formatı (BSD lisanslı)
  İkisi de telif açısından serbestçe kullanılabilir; format dökümantasyonuna
  sadık kalınacak.

## Lisans / dağıtım hedefi

Proje MIT lisansıyla açık kaynak olarak GitHub'da paylaşılacak.

## Geliştirme sırası (yol haritası)

1. Python tarafında boş/sahte veriyle çalışan bir UI iskeleti kurmak
   (pencere, tema, dosya yükleme butonu, boş grafik alanı).
2. UI'da hangi verilerin gösterileceğine karar verip, C++ tarafında sadece o
   verileri ayıklayan bir parser yazmaya başlamak.
3. C++ çıktısını (JSON/CSV) `shared/` şemasına göre üretmek.
4. Python tarafını gerçek C++ çıktısına bağlamak.
5. Gerçek örnek loglarla test etmek, arayüzü ve hata yönetimini iyileştirmek.

## Tamamlanan sonraki adımlar

- **Kural tabanlı otomatik yorumlama:** Voltaj düşümü (%15 eşik) ve motor akım
  dengesizliği (%20 eşik) için if/else tabanlı uyarı üretimi tamamlandı,
  gerçek loglarla kalibre edildi (bkz. `shared/power_log_schema.md` `warnings`
  bölümü). JSON çıktısında `warnings` alanı var, frontend'de gösteriliyor.
- **Paketleme / dağıtım:** `scripts/build_release.ps1` ile PyInstaller onedir
  paketi, uygulama ikonu ve backend .exe'sinin pakete dahil edilmesi tamamlandı.
- **Rover / araç tipi desteği:** `meta.vehicle_type` (rover, sabit kanat,
  multirotor, VTOL...) ve batarya/motor başına `has_current_data` bayrağı
  eklendi (bkz. `shared/power_log_schema.md`).

  Önemli tasarım kararı: **panel/istatistik dallanması araç tipine göre
  DEĞİL, verinin gerçekten var olup olmadığına göre yapılır.** Araç tipi
  sadece bilgi amaçlı bir etiket. Sebebi örnek loglarla ölçüldü: aynı araç
  tipinin ESC telemetrisi olan da olmayan da var, yani araç tipi verinin
  varlığı için güvenilir bir sinyal değil. Rover'da asıl sorun formatı
  ayrıştıramamak değildi (`battery_status` alan yapısı multirotor'la birebir
  aynı) — akım sensörü hiç bağlı olmadığı için tüm `current_a` örnekleri tam
  0.0 geliyordu ve arayüz bunu "ölçüm sıfır" gibi gösteriyordu.
- **PWM çıkış paneli:** `pwm_outputs` (PX4 `actuator_outputs`, ArduPilot
  `RCOU`) eklendi; motor paneline iki görünüm modu olarak giriyor
  ("Motor Görünümü: Çizgi / Isı Haritası / PWM Çıkışı / PWM Sapma"). Akım
  sensörü olmayan araçlarda motor aktivitesinin tek görünür kanıtı bu.

  İki kural: (1) PWM bir güç ölçümü değil kontrol çıktısı olduğu için uyarı
  kurallarına hiç girmiyor; (2) hangi kanalın motor hangisinin servo olduğu
  loglarda yazmadığı için tahmin yürütülmüyor — kanallar donanım adlarıyla
  (MAIN/AUX/Kanal) aktarılıyor. Hiç değişmeyen kanallar (kullanılmayan çıkış,
  servo nötr konumu) JSON'a yazılmıyor.

  **PWM ısı haritası neden "sapma" gösteriyor:** mutlak PWM skalası gerçek
  loglarla denendi ve okunaksız çıktı — aynı haritadaki servo/gimbal
  kanalları (900-2100, çoğu zaman uçta sabit) skalayı domine edip motorlar
  arasındaki asıl bilgiyi tek düze bir renge çeviriyordu. Kanal başına
  normalize etmek de çözmedi (motorlar zaten senkron hareket ediyor).
  Kasıtlı olarak +120 µs kaydırılmış bir motorla test edildiğinde SADECE
  "aynı çıkış rayındaki kanalların o andaki ortalamasından fark" görünümü
  dengesizliği açıkça gösterdi. Gruplama etiketin ilk kelimesine (MAIN/AUX/
  Kanal) göre; bu donanımdaki ayrı raylar, yani yine motor tahmini yok.
- **Araç tipine göre uyarı eşikleri:** eşikler artık araç tipi başına
  saklanabiliyor (Ayarlar penceresinde "Hangi araç için?" seçicisi). Frontend
  tanımlı tüm tiplerin eşiklerini `--vehicle-thresholds=<tip>:<sag>:<deng>:<neg>`
  ile geçiriyor, backend `meta.vehicle_type`'a göre eşleşeni seçiyor — eşikler
  çağrı anında veriliyor ama araç tipi ancak ayrıştırmadan sonra bilindiği için
  bu sıra zorunlu (log tek geçişte okunuyor).

  **Varsayılanlar tipe göre DEĞİŞMİYOR** ve bu bilinçli: örnek loglar ölçüldü,
  voltaj düşümü her araç tipinde %0.5–6.2 çıktı (eşik %15) ve tipler arasında
  anlamlı bir ayrışma yok; dengesizlik her tipte 1–2 logda ölçülebiliyor. Tipe
  özel sayı uydurmak, mevcut eşiklerin "gerçek loglarla kalibre edildi"
  standardını düşürürdü. Yapı kuruldu, kalibrasyon kullanıcıya bırakıldı.
- **Çoklu uçuş karşılaştırma:** giriş ekranındaki Geçmiş Dosyalar panelinde
  "⇄ Uçuşları Karşılaştır" butonu; seçilen 2+ uçuşun özet metrikleri (araç
  tipi, süre, voltaj/akım aralığı, enerji, tepe güç, kapasite, iç direnç,
  voltaj düşümü, uyarı sayısı) yan yana tabloda gösteriliyor.

  **Neden grafik değil tablo:** örnek loglar ölçüldüğünde uçuşların zaman
  eksenleri hiç örtüşmüyordu (ilk damgalar 3 s ile 4014 s arasında) ve
  batarya sınıfları farklıydı (3S/6S/12S). Aynı panele çizmek okunaksız
  olurdu; özet metrikler ölçekten ve süreden bağımsız karşılaştırılabiliyor.
  Metrikler üst istatistik satırıyla AYNI yardımcıları kullanıyor ki iki
  yerde farklı sayı çıkmasın.
- **Isı haritalarında hover tooltip:** tooltip eskiden sadece çizgi
  grafiklerinde çalışıyordu, çünkü `_on_plot_hover` değeri eksenin
  `get_lines()`'ından okuyordu — ısı haritasında hiç çizgi yok. Artık her
  ısı haritası çizim fonksiyonu, interpolasyon ızgarasını ve satır
  etiketlerini `_current_heatmap`/`_motors_heatmap`'e bırakıyor; hover
  imlecin `ydata`'sından satırı (`imshow` extent'i 0.5'ten başladığı için
  `round(y)-1`), `xdata`'dan da en yakın sütunu buluyor.

  Değer metnini paneller değil, ızgarayı tutan kapanış (`format_cell`)
  üretiyor; bu sayede PWM sapma haritası sapmanın yanına mutlak PWM'i de
  yazabiliyor ("+57.3 µs sapma (1458 µs)") — o görünüm mutlak değeri skalada
  bilerek gizliyor ama tek hücreye bakarken bilgi değerli.

  Aynı fonksiyondaki iki birim hatası da düzeltildi: sıcaklık modunda "24.50V",
  PWM çizgi modunda "1650.00A" yazıyordu.
- **Uçuş süresi hatası düzeltildi:** `duration_s` son zaman damgasını
  döndürüyordu; PX4 logları uçuş kontrolcüsünün açılışından beri geçen süreyi
  damgaladığı için 90.8 saniyelik bir kayıt "4104.7 s" görünüyordu. Artık ilk
  ve son örnek arasındaki fark alınıyor (hem backend `computeDuration` hem
  frontend `_flight_duration_seconds`). Aynı hata kalan-süre tahminine de
  taşınıyordu — orada süreyle çarpıldığı için sonuç ~45 kat şişiyordu.

## Kapsam dışı bırakılan fikirler

- **Yapay zeka / makine öğrenmesi entegrasyonu:** Değerlendirildi, KESİN
  OLARAK YAPILMAYACAK. ML modeli (ör. motor arıza tahmini) için gereken
  etiketlenmiş gerçek uçuş logu verisi yok. LLM tabanlı özet/yorum (Claude/
  GPT API) alternatifi de ücretli API anahtarı ve internet bağlantısı
  gerektirdiği için (offline çalışmaz, proje sahibi ücret ödemek istemiyor)
  elendi. Yerel/açık kaynak bir model çalıştırmak da paketi birkaç GB'a
  şişirip dağıtımı karmaşıklaştıracağından tercih edilmedi — zaten mevcut
  kural tabanlı uyarı sistemi aynı pratik değeri sıfır maliyetle sağlıyor.
  Bu konu kapanmıştır, tekrar gündeme getirilmeyecek.

## Kodlama tercihleri / notlar

- Kullanıcı (proje sahibi) C++ ve Python'da öğrenme aşamasında. Kod üretirken
  kısa açıklamalar eklemek veya neden o şekilde yazıldığını belirtmek faydalı
  olur.
- Küçük, tek amaçlı fonksiyonlar / adımlar tercih edilir; büyük tek seferlik
  kod bloklarından kaçınılır.
- Değişiklikler küçük, test edilebilir adımlar halinde yapılır.
