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
- **Gerçek ArduRover logu ile doğrulama:** ArduPilot tarafındaki rover yolu
  yalnızca sentetik bir fixture'la kapsanıyordu; rover davranışına dair her
  ölçüm PX4 logundan geliyordu. `data/Rover-Scripting-00000036.BIN` eklendi
  (ArduPilot autotest arşivinden, mevcut `ArduCopter-*.BIN` örnekleriyle aynı
  kaynak). Firmware satırı "ArduRover V4.8.0-dev"; tip doğru okunuyor.

  **En değerli yanı PX4 rover'ının TERSİ olması:** bu araçta akım sensörü VAR
  (712 örnek 0.0–7.12 A), PX4 rover'ında ise hiç yoktu (452 örneğin hepsi tam
  0.0). Yani "panel dallanması araç tipine göre değil verinin varlığına göre"
  kararı artık tek bir logun tesadüfü değil, aynı tipte iki zıt logla
  kanıtlanıyor. Üstelik akım sensörü olmasına rağmen ESC telemetrisi yok
  (`motors: []`) — üç durum (batarya akımı / motor akımı / PWM) birbirinden
  bağımsız.

  RCOU'nun 14 kanalından sadece ikisi hareketli (gaz + direksiyon), kalan 12'si
  sabit olduğu için eleniyor. Arayüzde akım çekimi ile PWM hareketi birebir
  örtüşüyor (araç ~43. saniyede hareket etmeye başlıyor) — fiziksel tutarlılık
  kontrolü olarak da işe yaradı.

  **Kaynak notu:** autotest logları SITL (simülasyon) koşularından geliyor,
  fiziksel donanımdan değil. Format birebir gerçek (aynı ArduRover firmware'inin
  loglama kodu), ama sayılar simüle. Uyarı eşiklerinin kalibrasyonu bunlara
  değil, PX4'ün flight review veritabanındaki gerçek donanım uçuşlarına
  dayanıyor (bkz. `shared/power_log_schema.md`) — o iddia etkilenmiyor.
- **İstatistik satırı dar ekranda sarıyor:** dokuz kutucuk 1100px altında
  sığmıyordu (1000px'de sonuncusu, 720px'de son üçü kesiliyordu). Kutucuklar
  artık `grid` ile yerleşiyor ve sığan sütun sayısı pencere genişliğinden
  hesaplanıyor.

  Sütun sayısı basit bir bölmeyle bulunamıyor: grid'de sütun genişliği o
  sütundaki en geniş kutucuk kadar olur ve kutucuklar eşit değil ("Süre" 79px,
  "Voltaj Aralığı" 133px). Bu yüzden aday sütun sayıları en genişten başlayarak
  deneniyor. Sarma gerektiğinde sütunlar satırlara **eşit dağıtılıyor**: sığan
  en fazla sütun 8 olduğunda yerleşim 8+1 çıkıyordu ve tek başına kalan
  kutucuk hata gibi duruyordu; aynı iki satırda 5+4 hem dengeli hem daha dar.

  **Testler önce boşa geçiyordu:** pencere haritalanmadan tüm widget
  genişlikleri 1 kalıyor, yani "hiçbir şey taşmıyor" iddiası hiçbir şey ölçmeden
  doğrulanıyordu. Test yardımcısı artık `deiconify` edip gerçek genişliği
  bekliyor ve gelmezse testi düşürüyor. Ayrıca `grid`, yer yetmediğinde
  butonları dışarı İTMİYOR — önce sıkıştırıp sonra grupları üst üste
  bindiriyor; dedektör üç bozulma biçimini de arıyor. İkisi de yerleşim
  kasıtlı bozularak (mutasyon) doğrulandı.
- **Üst toolbar dar ekranda taşmıyor:** ölçüldü — butonlar 1280px istiyordu,
  1100px'lik bir pencerede "Temizle"/"Dışa Aktar"/"Ayarlar"/"Koyu Tema"
  görünmüyordu (tıklanamıyordu da). İki değişiklik:

  (1) Butonlar artık doğrudan toolbar'a değil **sol/sağ grup çerçevelerine**
  konuyor ve toolbar `grid` kullanıyor; yer kalmayınca sağ grup ikinci satıra
  iniyor. Grup çerçevesi şart, çünkü Tk'da bir widget'ın ebeveyni sonradan
  değiştirilemiyor — satır değiştiren şey butonlar değil, grubun ızgaradaki
  yeri. Sağ grup ikinci satırda `columnspan` ile tüm satırı kaplıyor ki sütun
  genişlikleri üstteki butonlarla hizalanmaya zorlanmasın (zorlansaydı 700px'de
  yine 14px taşıyordu).

  Sarma kararı **her zaman tek satır ihtiyacına** göre veriliyor; iki satır
  modunun kendi (daha dar) ihtiyacına bakılsaydı yerleşim iki mod arasında
  salınırdı.

  (2) Uzunluğu içeriğe bağlı tek bileşen dosya adı etiketi (uzun bir ad 409px
  istiyor) — o da kalan boşluğa kısaltılıyor, tam metin fare ipucunda. Tam
  metin ayrıca saklanıyor çünkü **PDF raporu dosya adını oradan okuyor**;
  kısaltılmış ad rapora düşmemeli.

  İki Tk tuzağı yol boyunca çıktı: `<Configure>` işlenirken `winfo_width()`
  hâlâ ESKİ genişliği veriyor (yeni genişlik yalnızca olay nesnesinde), ve
  `pack_forget()` hemen etkili olmuyor (geometri hesabı boşta yapılıyor, bu
  yüzden yerleşim yenilemesi `after_idle` ile).
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
