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

- **Genel tarama: hata düzeltmeleri, yeni testler, görsel cila.** Üç paralel
  araştırma (frontend görsel, backend hata avı, test kapsamı) ~34 bulgu
  çıkardı; kullanıcıyla önceliklendirilip en değerli/en düşük riskli alt
  küme uygulandı.

  **Backend'de dört gerçek düzeltme** (`backend/src/main.cpp`):
  (1) `locateField`'e, `locateUlogField`'de zaten var olan sıfır-boyut
  koruması eklendi — bozuk bir FMT'de Volt/Curr'dan önceki tanınmayan bir
  format karakteri artık offset'i kaydırıp yanlış veri okumak yerine örneği
  güvenle atlıyor. (2) `extractUlogEscSamples`'ta `escCount>32` için
  `onlineMask >> i` tanımsız davranışına `i<32` koruması eklendi. (3) Hiçbir
  yerde zaman sıralaması garanti edilmiyordu (`computeDuration` vb. hep
  `front()`/`back()`'in gerçek min/max olduğunu varsayıyordu); `parseLog()`
  artık her diziyi ayrıştırma bitince `sortSamplesByTime` ile sıralıyor.
  (4) Dosya boyutu okumadan önce kontrol ediliyor (üst sınır 1 GB) —
  sınırsız bellek okumasına karşı.

  Üçü (1, 2, 3) yerleşim kasıtlı bozularak (mutasyon) doğrulandı; (2) UB
  olduğu için bu derleyicide gözlemlenebilir bir fark üretmedi (x86'da
  shift-by-32 donanım düzeyinde shift-by-0 gibi davranıyor) — düzeltme
  yine de doğru, sadece bu ortamda mutasyonla kanıtlanamadı.

  **Yeni testler:** Türkçe/ASCII-dışı karakterli dosya yolu (`İHA UYG`
  projenin kendi çalışma dizini olmasına rağmen hiç test edilmemişti — artık
  hem backend hem frontend'de var), 3+ bataryalı log (dengesizlik/ısı
  haritası/karşılaştırma tablosu hep 2 batarya ile test ediliyordu),
  ArduSub→submarine ve PX4 airship araç tipi eşlemeleri (kodda vardı,
  doğrulanmamıştı). Backend 76→88, frontend 109→115 test.

  **`data/ArduPlane-GpsSensorPreArmEAHRS-00000115.BIN` eklendi** — gerçek
  akım sensörü olan bir sabit kanat logu (`Rover-Scripting-00000036.BIN` ile
  aynı autotest.ardupilot.org kaynağı). Bu turdan önce ne ArduPilot ne PX4
  tarafında böyle bir log yoktu (`px4_fixed_wing_flight.ulg`'de esc_status
  hiç yok, `ArduCopter-*.BIN` örnekleri multirotor). Firmware satırı
  "ArduPlane V4.8.0-dev"; akım 0–49.999 A arası gerçekten değişiyor, ESC
  telemetrisi de var (sabit 0.8 A — SITL'de placeholder olduğu biliniyor,
  bkz. rover notu).

  **Frontend cila** (`frontend/main.py`): geçmiş dosya kartları ve format
  rozeti artık yuvarlak köşeli (`_rounded_rect_points`, `_blade_polygon`
  ile aynı yöntem — Canvas'ın yerleşik böyle bir ilkeli yok), karşılaştırma
  tablosuna stat kutucuklarıyla aynı dilde (`UI_GRIDLINE`) satır bandı
  eklendi, landing footer'ın adsız rengi `LANDING_FOOTER_TEXT`'e taşındı,
  `warnings_label`/`status_label` artık toolbar/stats-row'daki gibi
  `<Configure>`'da yeniden hesaplanan bir wraplength kullanıyor (öncesinde
  `warnings_label`'ın hiç wraplength'i yoktu).

  **Ölçülüp uygulanmayan bir madde:** tema butonundaki ☀/🌙 emoji'nin diğer
  ikonlarla (⚙ gibi) tutarsız renkli göründüğü varsayılmıştı; ekran
  görüntüsüyle test edildiğinde bu ortamda (CustomTkinter'ın varsayılan
  fontu) ikisi de zaten aynı monokrom çizgi stilinde render oluyor — kod
  değişikliği yapılmadı.
- **Giriş ekranı minsize (700px) genişlikte metin taşıyordu:** kök neden
  pack sırasıydı — footer, `expand=True` olan orta çerçeveden (`center`)
  *sonra* `pack(side="bottom")` ile ekleniyordu; Tk'nin pack cavity'si çağrı
  sırasına göre bölündüğü için bu, `center`'ın payını 410px yerine 200px'e
  düşürüyordu (ölçüldü). Footer artık `center`'dan önce pack ediliyor;
  ayrıca başlık/açıklama/footer için toolbar/stats-row'daki gibi
  `<Configure>` tabanlı dinamik `wraplength` eklendi. Regresyon testi
  mutasyonla doğrulandı (pack sırası eski haline döndürülüp testin kırmızı
  çıktığı teyit edildi, sonra düzeltme geri getirildi).
- **Ayarlar/Karşılaştırma dialoglarında küçük spacing tutarlılığı +
  gruplama:** `hint_label`/`error_label`/`status` etiketleri kardeşi olan
  satırların hepsinde var olan `padx=20`'yi almıyordu, eklendi. Ayarlar
  dialogunda 3 eşik alanı artık "Hangi araç için?" seçiciden görsel olarak
  ayrı, tonlu bir çerçevede (`GRIDLINE`, stat kutucuklarıyla aynı dil) —
  öncesinde hepsi düz bir yığın gibi durup seçicinin üçünü de etkileyen bir
  kapsam kontrolü olduğu belli olmuyordu.

  **Uygulama geneli bir spacing sabit skalası (4/8/12/16/24 gibi) bilerek
  YAPILMADI:** ölçüldü, `main.py`'de 60+ satırda `padx`/`pady` var, değerler
  0-24 arasında dağınık (3, 6, 20 gibi temiz bir skalaya oturmayan ara
  değerler dahil). Ama tek seferde tüm dosyayı değiştirmek büyük bir diff
  olur ve projenin "küçük, test edilebilir adımlar" ilkesine ters düşer —
  bilinçli olarak kapsam dışı bırakıldı, sadece dokunulan alanlarda (yukarı
  bakınız) düzeltiliyor.
- **Giriş ekranı içeriği tekrar sağa kaymıştı (relx=0.56 -> 0.5):** bir
  önceki maddedeki pack-sırası düzeltmesinin yan etkisi — `relx=0.56`,
  footer'ın `center`'ı daralttığı (yanlış) bir genişliğe göre kalibre
  edilmişti; genişlik doğru hale gelince aynı oran artık görünür şekilde
  daha sağa denk geliyordu. `relx=0.5`'e çekildi (1600px pencerede
  ölçüldü: başlık etiketinin merkezi beklenen merkezle birebir eşit).
- **İkinci "genel tarama" turu:** yine üç paralel araştırma ajanı (frontend
  görsel, backend hata avı, test kapsamı) çalıştırıldı, kullanıcı bu kez
  "hepsini birden yap" dedi — ~12 bulgunun tamamı ele alındı.

  **Backend (`backend/src/main.cpp`), dört düzeltme:**
  (1) JSON çıktısında hiçbir yerde `setprecision` yoktu, varsayılan 6
  anlamlı basamak uzun uçuşlarda `time_s`'i yuvarlayıp alt-saniye
  çözünürlüğünü kaybediyordu (`12345.6789` → `"12345.7"`) — artık
  `out << std::setprecision(15)`. Gerçek bir PX4 logunda ölçülüp
  doğrulandı (`1453.497104` gibi değerler artık tam korunuyor).
  (2) `writeJsonString` sadece `\n\r\t` kaçışlıyordu, RFC 8259'un şart
  koştuğu diğer kontrol karakterleri (0x00-0x1F) `\u00XX` ile kaçışlanıyor.
  (3) `std::atoi` kullanılıyordu ama `<cstdlib>` include edilmemişti — eklendi.
  (4) ULog format string'inde negatif dizi uzunluğu (`"tip[-5]"`) artık
  reddediliyor (alan boyutu 0/çözülemez sayılıyor) — negatif değer
  `size_t`'e cast edilince dev bir sayıya sarıp offset hesaplarını
  bozabilirdi.

  (1) gerçek logla, (4) mutasyonla denendi: guard kaldırılıp yeniden
  derlendiğinde test yine de yeşil kaldı, çünkü bu senaryodaki sarma
  (-5 × 4 bayt) zaten var olan `offset + fieldSize > payloadSize` sınır
  kontrolünü her durumda geçiyor (dev sayı her zaman payload'dan büyük) —
  escCount>32 UB durumundaki gibi, guard'ın gerekliliği bu ortamda
  kanıtlanamadı ama yine de doğru (sarmaya bel bağlamak yerine kaynağında
  reddediyor). Backend 88→92 test.

  **Frontend (`frontend/main.py`), beş düzeltme:**
  (1) Karşılaştırma dialoguna `grab_set()` eklendi (Ayarlar'da zaten
  vardı) — eksikken pencere açıkken ana ekran hâlâ etkileşilebiliyordu ve
  tema değiştirilirse dialogdaki renkler (açılış anındaki modül sabitleri,
  UI_* çiftleri değil) donuk kalıyordu.
  (2) Giriş ekranındaki "örnek veri ile dene" linki `TEXT_MUTED`
  kullanıyordu (tema değişince renk değiştiren sabit); açık temada
  `LANDING_BG`'nin (her zaman koyu) üzerinde kontrastı kalmıyordu —
  `LANDING_TEXT_SECONDARY`'ye çevrildi. Tıklama yüksekliği de (20px,
  diğer butonlar 48px) 32px'e büyütüldü.
  (3) PDF raporunda 10+ uyarı varsa liste sayfa dışına taşıp sessizce
  kayboluyordu — artık sığmayan kısım "… ve N uyarı daha" ile özetleniyor,
  o satırın kendisi de sığacak şekilde bir satır payı ayrılarak.
  (4) Ayarlar dialogunun sabit yüksekliği (400px) gerçek içerik
  yüksekliğinden (466px, ölçüldü) azdı — buton satırı kırpılıyordu; 480'e
  çıkarıldı.
  (5) "Hangi araç için?" menüsünün genişlik almadığı (uzun etiket
  kırpılabilir) iddiası ölçülüp YANLIŞ çıktı: `pack(fill="x")` zaten
  menüyü satırın tam genişliğine geriyor, en uzun etiket ("Sadece Sabit
  Kanat") bile kırpılmadan sığıyor — kod değişikliği yapılmadı.

  **Yeni testler:** frontend'in backend-hata UI yolu (`⚠ Hata: ...`,
  `status_label` rengi, `load_button` yeniden etkinleşmesi) hiç test
  edilmiyordu, artık var. Backend'de `SchemaConformanceTests` —
  `shared/power_log_schema.md`'de dokümante edilen tüm alanların (meta/
  batteries/motors/pwm_outputs) gerçek ArduPilot ve PX4 loglarında
  birebir üretildiğini doğrudan kontrol ediyor (öncesinde şemayla kod
  arasındaki bağ sadece yorum satırlarıydı). Frontend 116→117, backend
  88→92 test.

  **İlk arama bulamadı, ikinci arama buldu:** gerçek bir ArduPilot
  QuadPlane/VTOL `.BIN` logu için `autotest.ardupilot.org`'daki "QuadPlane"
  CI işine bakıldı — o iş sürekli FAILED ve sadece `.tlog` yayınlıyor, hiç
  `.BIN` yok. Ama kullanıcı ısrar edince farklı bir mekanizma bulundu:
  ArduPilot'un `FlyEachFrame` testi, Plane firmware'ini `vehicleinfo.json`
  içindeki TÜM çerçeve tiplerinde (aralarında `quadplane`, `quadplane-tri`,
  `quadplane-cl84` gibi onlarca VTOL varyantı) uçuruyor ve HER biri için
  ayrı bir `ArduPlane-FlyEachFrame-NNNNNNNN.BIN` üretiyor — 42 tanesi
  indirilip kendi backend'imizden geçirildi, motor sayısına (ESC
  telemetrisi) göre en zengin aday (`00000182`, 5 motor, 225 saniyelik
  gerçek uçuş) seçildi.

  **Asıl bulgu kod tarafındaydı:** bu logun (ve genel olarak birçok
  QuadPlane SITL testinin) hiç batarya (BAT/CURR) verisi yok — sadece
  gerçek çok motorlu ESC telemetrisi ve PWM verisi var. Backend o ana
  kadar `parsed.batteries.empty()` olan HER logu reddediyordu (motor/PWM
  verisi ne kadar zengin olursa olsun) — yani bu gerçek log daha
  önceki (network-erişimli) aramalarla bulunsaydı bile o zaman hâlâ
  kullanılamazdı. `writePowerLogJson` artık batarya yoksa da motor ya da
  PWM'den en az biri doluysa logu kabul ediyor; `computeDuration`
  (backend'de `computeTimeRange` şablon fonksiyonuna genelleştirildi)
  batarya yoksa süreyi motor, o da yoksa PWM zaman damgalarından
  hesaplıyor. Frontend'de voltaj panelinin bu durumda hiç mesaj
  göstermeden boş kalması da düzeltildi (`NO_BATTERY_MESSAGE`, diğer
  "veri yok" panelleriyle aynı desen).

  Bu davranış değişikliği ESKİ bir testi (`test_ardupilot_esc_only_no_
  battery_fails_with_clear_error`) bilerek kırdı — o test tam da artık
  desteklenen senaryoyu (ESC var, batarya yok) "hata bekleniyor" diye
  test ediyordu; adı ve beklentisi yeni davranışa göre güncellendi. Gate
  değişikliği mutasyonla doğrulandı (eski katı kural geri getirilip yeni
  testlerin kırmızı çıktığı teyit edildi). `data/ArduPlane-FlyEachFrame-
  00000182.BIN` eklendi (28MB — diğer örneklerden ~10 kat büyük, bilinçli
  bir tercih: bu zenginlikte gerçek VTOL geçiş verisi taşıyan başka aday
  yoktu). `shared/power_log_schema.md`'ye "Batarya verisi hiç yoksa"
  bölümü eklendi. Backend 92→97, frontend 117→118 test.

  **Bilinen, kapsam dışı bırakılan sınırlama:** ArduPilot firmware adı
  quadplane çerçeve sınıfını ayırt etmiyor (`Q_ENABLE` bir parametre,
  firmware string'i değil) — bu yüzden bu logun `vehicle_type`'ı `"vtol"`
  değil `"fixed_wing"` kalıyor. PX4 tarafında `is_vtol` bayrağı sayesinde
  bu ayrım zaten var; ArduPilot tarafında düzeltmek FMT/PARM mesajlarından
  `Q_ENABLE` parametresinin değerini okumayı gerektirir — ayrı bir iş,
  bu turun kapsamı dışında.

- **Zaman ekseni artık 0'dan başlıyor (görüntüleme katmanı normalizasyonu):**
  kullanıcı haklı bir tutarsızlık yakaladı — süre kartı 90.0 s derken
  grafik ekseni 1450–1545 gösteriyordu (PX4 damgaları kontrolcünün
  açılışından itibaren sayar; 1543.5 s ise `duration_s` düzeltmesinden
  önceki ESKİ hatalı değerdi, regresyon değil). `_run_backend`, JSON'u
  okur okumaz tüm serileri (batarya/motor/PWM) ortak en erken damgaya
  göre kaydırıyor (`_normalize_time_axis`) — TEK ortak t0 ile, seriler
  arası hizalama bozulmasın diye. Süre/enerji/kapasite hesapları zaman
  FARKLARINA dayandığı için etkilenmiyor; backend JSON'ı ve şema mutlak
  kalıyor (bu sadece frontend'in görüntüleme normalizasyonu). Mutasyonla
  doğrulandı (normalizasyon kapatılınca test 1453.5 != 0.0 ile kırmızı).

- **Kullanıcı geri bildirim turu — açık tema, stat kartları, karşılaştırma,
  tıklanabilir uyarılar:** ekran görüntüleri üzerinden gelen ayrıntılı bir
  geri bildirim listesi 4 pakete bölünüp uygulandı. Keşifte netleşen iki
  şey: PDF raporu zaten vardı (kullanıcı bilmiyordu), dengesizlik bandı da
  zaten çiziliyordu (sadece açıklaması yoktu) — bu ikisi için yeni iş
  açılmadı, sadece bildirildi/etiketlendi.

  **Paket 1 — açık tema seri paleti:** `SERIES_COLORS`/`HEATMAP_COLORS`
  tek (koyu zemine göre seçilmiş) listeydi, açık temada soluk kalıyordu.
  `PWM_DEVIATION_COLORS`'daki desenle birebir: ikisi de artık
  `DARK_PALETTE`/`LIGHT_PALETTE` içinde, tema toggle'ı ikisini de yeniden
  bağlıyor. Renk-varlık eşleşmesi korundu (Batarya 1 iki temada da mavi).

  **Paket 2 — stat kartları cilası:** 9 kutucuğa `STAT_TILE_TOOLTIPS`
  (birim/bağlam açıklaması); Akım Aralığı kutucuğu negatifte "⚠ " öneki
  alıyor (kart ile uyarı arasındaki görsel bağ kullanıcı isteğiydi).
  Tahmini Kalan Süre artık tek sayı yerine aralık (`_remaining_time_range_min`):
  taban tahmine, son 1/3 pencerenin akım oranıyla ölçeklenmiş ikinci bir
  tahmin ekleniyor; ikisi %10 içindeyse tek değere düşüyor.

  **Paket 3 — karşılaştırma dialogu, landing paleti:** dialogdaki "tema
  uyumsuzluğu" aslında dialog kaynaklı değildi — giriş ekranı BİLEREK hep
  koyu (LANDING_* paleti), dialog aktif temayı takip ediyordu ama sadece o
  hep-koyu ekrandan açıldığı için açık temada "koyu zemin üstünde beyaz
  pencere" gibi kopuk duruyordu. Kullanıcıya soruldu: dialog artık HER
  ZAMAN LANDING paletini kullanıyor, temayı hiç takip etmiyor. Ayrıca:
  farklı araç tipi karşılaştırılırken uyarı notu (`_comparison_has_mixed_
  vehicles`), dikkat çekici hücre vurgusu + otomatik özet satırı
  (`_comparison_notable_cells` — bir uçuşun değeri diğerlerinin medyanının
  2 KATIysa işaretlenir; 1.5x gibi sınır durumlar bilerek işaretlenmez),
  uzun dosya adı başlıkta ortadan kısaltma (`_middle_ellipsis`, tam ad
  tooltip'te).

  **Paket 4a — tıklanabilir uyarılar → seri vurgulama:** bir uyarıya
  tıklayınca ("Batarya N: ..." / "Motor N: ...") ilgili seri grafikte tam
  opak kalırken paneldeki diğerleri soluklaşıyor (alpha 0.15); ikinci tık
  kaldırıyor. Backend'in tüm uyarı cümleleri bu önekle başladığı için
  (`WARNING_TARGET_RE`) uyarı metninden hedef seri güvenle çıkarılabiliyor.
  Isı haritası ve PWM görünümleri kapsam dışı: birinde seri çizgisi yok,
  ötekinde kanal numarası motor numarasıyla eşleşmiyor (bkz. PWM notları).

  **Paket 4b — karşılaştırma voltaj overlay'i + eşik görselleştirmeleri:**
  tablo altına, uçuş başına TEK çizgi olacak şekilde voltaj overlay
  grafiği eklendi (`_overlay_battery_for_flight` — ilk voltaj örneği olan
  batarya; 5 uçuş × N batarya çizmek okunmaz olurdu). Zaman ekseni zaten
  t=0 bazlı olduğu için ek hizalama gerekmedi. Farklı batarya sınıfları
  (3S/6S) aynı panelde dürüstçe farklı seviyelerde görünüyor — bu
  KASITLI, "elma-armut" karşılaştırmasının kendisi zaten mixed-vehicle
  notuyla işaretleniyor. Dengesizlik bandına köşe metni ("bant: ortalama
  ±%N dengesizlik eşiği") eklendi; negatif akım eşiği artık akım/motor
  panellerinde kesikli bir çizgi (`_draw_negative_current_threshold`) —
  SADECE gerçekten negatif örnek varsa çiziliyor, her uçuşta sabit bir
  çizgi negatif akım nadir olduğu için gürültü olurdu.

  Dialog geometry 920x720 → 960x860'a çıkarıldı (grafik eklenince eski
  boyut yetersiz kaldı). Bir ekran görüntüsü probu ilk seferde grafiği
  "kayıp" gösterdi — kod hatası değildi, `CTkScrollableFrame`'in görünür
  viewport'u chart'ı sarkıtıyordu; kaydırılınca üç uçuşun (12V/11V/24V)
  çizgileri de doğru görünüyor.

  Ortam notu: Bash tool'un (git-bash) alt süreç ortamında backend .exe'si
  access violation (0xC0000005) ile çöküyor, aynı komut PowerShell'den
  sorunsuz çalışıyor — kod hatası değil, sadece test/derleme komutlarının
  PowerShell üzerinden çalıştırılması gerekiyor (Bash tool'un bilinen PATH
  tuhaflığına ek bir ortam sınırlaması).

  Frontend 118 → 140 test.

- **Platform bağımsızlık (Windows + Linux hazırlığı):** Kod platform-bağımsız
  hale getirildi (Windows + Linux uyumlu). Linux paketleme/test henüz
  yapılmadı, bir Linux ortamı (WSL vb.) gerektiğinde ele alınacak.

- **Güç/motor anomali tespiti:** Voltaj düşüşü, akım sıçraması, pervane
  dengesizliği ve verim düşüşü için zaman-penceresi bazlı bir tespit katmanı
  eklendi — mevcut kural tabanlı `warnings` (uçuş-bazlı, tek sayı) katmanının
  ÜSTÜNE, onu değiştirmeden.

  **Mimari karar:** İstek Python'da pymavlink/pyulog ile ayrı bir ham-log
  ayrıştırıcı kurulmasını öneriyordu, ama bu projenin "tek ayrıştırma kaynağı
  C++ backend" ilkesiyle (bkz. yukarıdaki Mimari bölümü) çelişiyordu — iki
  dilde aynı format için iki ayrı ayrıştırıcı bakım yükü ve tutarsızlık riski
  demek. Bunun yerine backend'e (`extractEscSample`, `extractUlogEscSamples`)
  RPM okuma eklendi (`motors[].rpm` / `has_rpm_data`, `has_current_data`
  ile birebir aynı desen), anomali MATEMATİĞİNİN tamamı (resample, öznitelik,
  spektral, eşik) Python'da SADECE JSON'dan çalışıyor. pymavlink/pyulog
  projeye hiç eklenmedi.

  Titreşim (ArduPilot VIBE / PX4 sensor_accel) verisi bilinçli olarak
  kapsam dışı bırakıldı — backend'de hiç ayrıştırılmıyor, sıfırdan yeni bir
  parser gerektirirdi. Pervane dengesizliği bu yüzden gerçek titreşim
  ölçümü değil, akım/RPM sinyalinin Welch PSD analiziyle DOLAYLI tahmini
  (`frontend/anomaly_spectral.py`).

  **scikit-learn/IsolationForest bilerek eklenmedi** — mevcut "Yapay zeka /
  makine öğrenmesi entegrasyonu KESİN OLARAK YAPILMAYACAK" kararıyla
  (aşağıya bakınız) tutarlı; kodda tek satırlık bir TODO notu dışında hiç
  iz yok.

  Yeni dosyalar (`frontend/`, hepsi Tk'siz/bağımsız test edilebilir):
  `flight_series.py` (JSON→numpy adaptörü), `resample.py` (sabit hıza
  yeniden örnekleme — PX4/ArduPilot'un farklı örnekleme hızlarını
  eşitlemek için), `anomaly_features.py` (RMS/tepe/dV/dt/ripple),
  `anomaly_spectral.py` (scipy.signal.welch tabanlı dengesizlik skoru,
  projeye ilk kez `scipy` bağımlılığı eklendi), `anomaly_detect.py`
  (kayan pencere z-score motoru + `AnomalyEvent` + orkestratör).

  **Gerçek bir hata ölçülüp düzeltildi:** RPM'siz motorlar için tasarlanan
  "PSD tepe/ortalama oranı" yedek skoru, ölçüldüğünde SAF BEYAZ GÜRÜLTÜDE
  bile ~14 gibi sahte-yüksek bir "dengesizlik" skoru üretiyordu — sebep,
  hedef hıza (`target_rate_hz`) sinyalin GERÇEK örnekleme hızından daha
  yükseğe resample etmenin, interpolasyonun alçak-geçiren etkisiyle yüksek
  frekans bantlarını neredeyse sıfıra düşürmesi (bu da PSD'yi yapay olarak
  "tepeli" gösteriyor). Düzeltme: hedef hız artık her zaman
  `min(target_rate_hz, orijinal_hız)` ile sınırlanıyor
  (`anomaly_spectral._resample_and_welch`). Gerçek loglarla (px4_hexarotor_
  flight.ulg, ArduPlane-FlyEachFrame-00000182.BIN) doğrulandı: sağlıklı
  motorlar 0.004-0.005 gibi düşük skorlar alıyor, yanlış alarm yok.

  UI entegrasyonu (`frontend/main.py`): mevcut `warnings_frame`/
  `_on_warning_click`/`WARNING_TARGET_RE` tıkla-vurgula mekanizması aynen
  yeniden kullanıldı (`AnomalyEvent.target` bilerek aynı `("Batarya", N)`/
  `("Motor", N)` sözleşmesinde) — yeni bir kaydırılabilir panel
  (`anomaly_frame`, olay yoksa tamamen gizli) ve panellerdeki mevcut
  zaman serisi çizgilerinin üzerine `axvspan` ile renkli bantlar
  (`_draw_anomaly_overlay`, `_draw_imbalance_band` ile aynı üslupta).
  Hover tooltip desteği overlay bantlarına eklenmedi (bilinçli, küçük adım
  tercihi — `_on_plot_hover` sadece `Line2D` okuyor, `axvspan` bir
  `Polygon` döndürüyor).

  Eşik değerleri (z-score, pencere genişliği, skor eşiği) gerçek anormal
  bir log elde bulunmadığı için KALİBRE EDİLMEMİŞ başlangıç değerleri —
  `warnings` kuralının %15/%20 eşiklerinin aksine gerçek bozuk bir motor
  örneğiyle doğrulanmadı.

  **Kalibrasyon için gerçek arızalı log arandı, bulunamadı (CURR2/CURR3
  ile aynı kategori — kapsam dışı kalan, gerçek örnek şartı karşılanamayan
  bir konu):** ArduPilot ve PX4 forumlarında motor/ESC arızası, pervane
  dengesizliği tartışan başlıklar tarandı. Tek indirilebilir gerçek log
  (ArduPilot, arızalı ESC teşhisi konmuş bir hexacopter,
  `discuss.ardupilot.org/t/oscillation-and-propeller-noise-issue/127804`)
  indirilip backend'den geçirildi — ama bu logda hiç ESC/motor telemetrisi
  yok (`motors: []`, sadece batarya verisi), yani `propeller_imbalance`/
  `efficiency_drop` (ikisi de RPM/motor akımı gerektiriyor) hiç test
  edilemedi. Batarya seviyesinde de `anomaly_detect.detect_all` hiç olay
  üretmedi — pozitif bir kalibrasyon kanıtı değil ama bir negatif-kontrol:
  gerçek, bilinen bir donanım sorunu taşıyan bir uçuşta yanlış alarm
  çıkmadı. RPM/motor-akımı seviyesinde herkese açık, indirilebilir,
  bilinen-arızalı bir log bulunamadı; PX4 taraftaki adaylarda (ESC desync/
  motor arızası, RPM dengesizliği başlıkları) hiç log linki paylaşılmamıştı.

  Backend 97→103, frontend (Tk'siz beş yeni test dosyası + test_smoke.py
  eklentileri) toplamda 36 yeni bağımsız test + 158 smoke testi (155→158).

- **Üç yeni analiz özelliği: iç direnç trendi, motor dengesizliği, PWM
  doygunluğu.** Hepsi anomali tespiti katmanının üzerine, tamamen Python
  tarafında eklendi — backend'e (`backend/src/main.cpp`) HİÇ dokunulmadı,
  mevcut JSON şeması yeterliydi.

  **Batarya iç direnç tahmini iyileştirildi** (`frontend/battery_resistance.py`,
  yeni): eski `_battery_internal_resistance_estimate` tüm uçuşun ham
  örneklerine filtresiz tek regresyon uyguluyordu; yerini alan yeni modül
  önce kenar-düzeltilmiş bir kayan ortalamayla gürültüyü azaltıyor, sonra
  akımın yerel standart sapmasının genele göre düşük kaldığı (durağan/
  hover) pencereleri eleyip sadece "belirgin değişen" örneklerle regresyon
  yapıyor, R² değerini güven göstergesi ("yüksek/orta/düşük") olarak
  döndürüyor. Karşılaştırma dialoguna uçuşlar arası trend grafiği eklendi.

  **Gerçek bir hata ölçülüp düzeltildi:** `np.convolve(mode="same")` kenar
  noktalarında sıfır-dolgu (zero-padding) yüzünden ilk/son birkaç örneği
  gerçek değerinin ~1/5'ine düşürüyordu — bu 1-2 bozuk uç nokta 200 örneklik
  regresyonu domine edip R²'yi 0.999'dan 0.08'e çöktürüyordu. Düzeltme: pay
  ve payda aynı şekilde konvolve edilip bölünüyor (kenarlarda gerçek katkı
  sayısına göre normalize), mutasyonla doğrulandı.

  **Motor dengesizliği** (`anomaly_detect.detect_motor_imbalance_events`):
  backend'in mevcut akım-bazlı "ortalamaların ortalaması ±%20" kuralının
  AYNISI, RPM boyutu eklenmiş hali — ikisinde de sapan motor "critical"
  sayılıyor. **ESC telemetrisi (akım/RPM) hiçbir motorda yoksa** PWM
  kanalları üzerinde aynı mantıkla bir fallback çalışıyor
  (`detect_pwm_channel_imbalance_notes`) — ama bu, motor hedefi taşıyan bir
  `AnomalyEvent` DEĞİL, tıklanamaz, kanal etiketiyle ("Kanal 1"/"MAIN 2")
  raporlanan ayrı bir not listesi. Sebep: PWM kanalının hangi motora ait
  olduğu loglarda hiç yazmıyor (bkz. `shared/power_log_schema.md`
  "Kanal ≠ motor") — kullanıcıya soruldu, kanal-bazlı raporlama (motor
  iddiası taşımayan) seçildi.

  **PWM doygunluğu** (`frontend/pwm_saturation.py`, yeni): her PWM kanalı
  için değerin yapılandırılabilir bir eşiğin (varsayılan 1900µs) üstünde
  kaldığı örnek yüzdesi + kanallar arası ortalama. Sonuç yine kanal
  etiketiyle raporlanıyor, "motor" denmiyor.

  **Gerçek loglarla ilginç bir doğrulama:** `px4_hexarotor_flight.ulg`'de
  gerçek motor kanalları (AUX 1-6) %0 doygun çıktı, ama motor OLMAYAN
  MAIN 2/3 kanalları %97 doygun çıktı — "kanal ≠ motor" kuralının somut
  kanıtı: motor etiketi kullanılsaydı "2 motor sürekli tam gazda" gibi
  yanlış bir sonuç raporlanırdı.

  Ayarlar dialoguna iki yeni, araç tipinden BAĞIMSIZ (Python tarafında
  hesaplandığı için backend'in `--vehicle-thresholds` mekanizmasına hiç
  bağlanmayan) global alan eklendi: "Motor RPM Dengesizliği Eşiği (%)" ve
  "PWM Doygunluk Eşiği (µs)". Akım dengesizlik eşiği mevcut genel ayarı
  paylaşıyor, gereksiz üçüncü bir alan açılmadı.

  Mevcut `synthetic_test_log.BIN` fixture'ının motorlarının BİLEREK
  dengesiz kurulmuş olduğu (0.8x/1.25x çarpanlarla, satırlar ayırt
  edilebilsin diye) ortaya çıktı — yeni motor dengesizliği tespiti bunu
  doğru şekilde yakalayınca eski bir "temiz uçuş" testi kırıldı (hata
  testteydi, tespitte değil); gerçekten dengeli yeni bir fixture ile
  düzeltildi. Ayrıca iki eski PWM-görünümü testi ham `ax.get_lines()`
  sayısına bakıyordu — projenin "eşik/dekoratif çizgiler `_` önekiyle
  filtrelenir" kuralına (`_data_lines`) uydurulmaları gerekti.

  Backend'e dokunulmadı (97 test aynen kaldı). Frontend: 2 yeni Tk'siz test
  dosyası (`test_battery_resistance.py` 9 test, `test_pwm_saturation.py` 7
  test) + `test_anomaly_detect.py`'a 16 yeni test + `test_smoke.py`'a 5 yeni
  test, toplam 163 smoke testi (158→163).

- **Arayüz görsel profesyonelleştirme (tema modülü, Matplotlib stili,
  sidebar, marka bütünlüğü).** Kod incelemesi kullanıcının varsaydığından
  farklı bir durum ortaya çıkardı: renkler zaten merkeziydi (`DARK_PALETTE`/
  `LIGHT_PALETTE`/`UI_*`/`LANDING_*`), sadece fontlar (22 dağınık
  `CTkFont(size=...)` çağrısı) ve spacing (77 dağınık `padx`/`pady`) gerçekten
  dağınıktı; Matplotlib tarafında zoom/pan senkronu (`sharex`), eşik/anomali
  bantları (`axhspan`/`axvspan`) ve hover tooltip zaten VARDI — bunlar için
  yeni kod yazılmadı, sadece 3 ana panelin dışındaki (giriş ekranı, karşılaştırma
  dialogu) tekrarlı eksen stili `_style_landing_axes`'te birleştirildi.

  **Yeni `frontend/theme.py`:** renk paletleri + `UI_*`/`LANDING_*` çiftleri
  davranış değiştirilmeden buraya taşındı; `FONT_SIZES` (mevcut boyutların
  isimlendirilmiş sınıflandırması), `SPACING` (4/8/12/16/24) ve
  `MONO_FONT_FAMILY` eklendi — bilerek SADECE bu turda dokunulan alanlarda
  (yeni sidebar, stat kutucuğu değeri) kullanıldı, dosya genelinde 77 çağrıyı
  toplu değiştirmek önceden ("büyük diff riski" diye) bilerek ertelenmişti.

  **Sidebar (gezinme rayı):** voltaj/akım/motor paneli TEK birleşik Figure
  olarak (senkron zoom) kalmalı diye kullanıcıyla netleştirildi — sidebar
  bunu bölen bir `tkraise` değil, sadece "İstatistikler"/"Anomaliler/Uyarılar"
  ikincil panellerini açıp kapatan iki `CTkSwitch`. İlk denemede sidebar
  toolbar'ın CAVITY'sini de paylaşıyordu ve 800px'de bir buton sıkışıyordu
  (toolbar'ın önceden ince marjlı, `_toolbar_single_row_width` ile ayarlanmış
  dar-ekran davranışını bozdu) — kullanıcıya soruldu, sidebar toolbar'ın
  ALTINA alındı: yeni bir `analysis_content` çerçevesi stats_row/warnings/
  anomaly/status_label/motor-görünüm toggle'ları/grafik alanının gerçek
  ebeveyni oldu, toolbar tam genişlikte kalıp hiç etkilenmedi.

  **İki gerçek hata bulunup düzeltildi** (ikisi de Tk'nin "pack_forget()
  sonrası çıplak pack() widget'ı sıranın SONUNA ekler" davranışından):
  (1) `anomaly_frame` ilk kez `_update_anomaly_panel`'de paketlendiğinde
  (inşa sırasında bilerek paketlenmiyor) sıranın sonuna düşüp canvas'ın
  (`fill="both", expand=True`) ALTINDA neredeyse sıfır yüksekliğe
  sıkışıyordu — `pack_slaves()` ile ölçülüp doğrulandı (`h=1`), `before=
  self.status_label` ile düzeltildi. Bu araştırma sırasında customtkinter'ın
  `CTkScrollableFrame.pack()`/`pack_forget()`'ının `self`'i değil dahili
  `self._parent_frame`'i çağırdığı da ortaya çıktı (ctk_scrollable_frame.py)
  — testlerde `winfo_manager()`/`winfo_y()` bu yüzden `_parent_frame`'e
  bakmalı, `anomaly_frame`'in kendisine değil. (2) `stats_row`'un sabit
  `before=self.warnings_frame` çapası, anomali switch'i KAPALIYKEN (yani
  warnings_frame de pack_forget durumundayken) istatistik switch'i kapatılıp
  açılırsa TclError fırlatıyordu ("... isn't packed") — gerçek loglarla
  uçtan uca denemede yakalandı, çapa artık dinamik (warnings_frame paketliyse
  ona, değilse her zaman paketli olan status_label'a). İkisi de mutasyonla
  doğrulandı.

  **Installer ikon düzeltmesi:** `iha_setup.iss`'teki `SetupIconFile` satırı
  hem yorum satırıydı hem de var olmayan bir dosyayı (`drone_logo.ico`)
  gösteriyordu; artık etkin ve uygulamanın kendi ikonuna (`frontend/assets/
  uygulama.ico`, pencere/görev çubuğunda zaten kullanılan) işaret ediyor —
  gerçek bir `ISCC.exe` derlemesiyle doğrulandı. `WizardImageFile` için uygun
  boyutta mevcut bir görsel bulunamadı, yeni görsel üretilmedi (kapsam dışı
  bırakıldı).

  **Kapsam dışı bırakılanlar (kullanıcıya soruldu, bilerek):** sürükle-bırak
  dosya yükleme (yeni `tkinterdnd2` bağımlılığı gerektirir, boş ekran zaten
  olgun); gerçek yüzdeli ilerteme çubuğu (backend IPC protokolü değişikliği
  gerektirir, backend'e dokunmama kararıyla çelişir); spacing skalasının
  dosya geneline toplu uygulanması.

  Backend'e dokunulmadı (103 test aynen kaldı). Frontend: `test_smoke.py`'a
  6 yeni test (sidebar varlığı/varsayılan durum, toolbar'ın daralmadığı,
  iki switch'in doğru sırayla paket/pack_forget, iki switch'in ETKİLEŞİMİ —
  gerçek hatanın yakalandığı senaryo), toplam 169 smoke testi (163→169).

- **Uygulama içi güncelleme kontrolü.** Kullanıcı, insanların yeni sürümü
  elle indirip yeniden kurmadan haberdar olabilmesini istedi. Tam sessiz/
  kendi-kendini-güncelleyen bir sistem (kod imzalama sertifikası yok, her
  yeni `.exe` SmartScreen tetikliyor; çalışan `.exe` kendini değiştiremez)
  orantısız risk taşıdığı için kullanıcıyla konuşulup orta yol seçildi:
  açılışta arka planda GitHub Releases kontrol edilir, yeni sürüm varsa
  dialog gösterilir, "İndir ve Kur" tıklanınca installer indirilip
  `os.startfile` ile başlatılır ve uygulama kendini kapatır — kurulumun
  gerisi tamamen mevcut Inno Setup sihirbazına bırakılır.

  **Yeni `frontend/update_check.py`** (Tk'siz, `flight_series.py` deseniyle):
  `parse_version`/`is_newer_version` (belirsizlikte temkinli `False`),
  `fetch_latest_release` — stdlib `urllib.request` ile GitHub Releases API'si
  (yeni bağımlılık eklenmedi), ağ hatası/timeout/bozuk yanıt/eşleşen `.exe`
  asset'i yokluğu dahil HER başarısızlıkta sessizce `None` döner, asla
  exception fırlatmaz — bu, arka plan kontrolünün internet yokken kullanıcıyı
  hiç rahatsız etmemesinin garantisi.

  **Mimari:** `_load_file`'ın (backend çağrısı) AYNI queue+thread+after
  deseni hem kontrol hem indirme için yeniden kullanıldı — Tk ana thread'i
  ağ isteği sırasında donmuyor. `sys.frozen` ayrımıyla (mevcut
  `_find_backend_exe` deseni) kontrol SADECE paketlenmiş sürümde çalışır;
  kaynaktan çalıştırılan bir kopyaya "installer indir" önermek anlamsız
  olurdu. Otomatik kontrol günde en fazla bir kez (ayarda kapatılabilir,
  varsayılan açık); elle "Şimdi kontrol et" bu sınırı yok sayar. Kullanıcı
  bir sürümü "atlarsa" o sürüm için otomatik kontrolde bir daha sorulmaz.

  `iha_setup.iss`'e `CloseApplications=force` eklendi — uygulama installer'ı
  başlatıp kendini kapatana kadarki küçük zamanlama penceresinde hâlâ açık
  kalırsa installer'ın devam edebilmesi için güvenlik ağı.

  **Test disiplini:** Hiçbir testte gerçek ağ isteği ya da gerçek installer
  çalıştırma YOK — hepsi `unittest.mock.patch` ile sahtelendi (geliştirme
  makinesinde yanlışlıkla gerçek bir kurulum tetiklenmesin diye). Gerçek
  doğrulama paketlenmiş `.exe`'nin GERÇEK GitHub Releases'e karşı çalıştırılıp
  (mevcut sürüm zaten en güncel olduğu için) sessizce "güncel" sonucunu
  `settings.json`'a yazdığının ölçülmesiyle yapıldı — gerçek bir indirme/kurulum
  hiç tetiklenmedi (deneyecek daha yeni bir sürüm olmadığı için).

  Backend'e dokunulmadı (103 test aynen kaldı), yeni bağımlılık eklenmedi.
  Frontend: yeni `test_update_check.py` (16 test, tamamı mock'lu) +
  `test_smoke.py`'a 13 yeni test (günlük kontrol sınırı, dialog/atla/güncel
  senaryoları, indirme başarı/hata yolu, Ayarlar'daki bölümün sadece
  paketlenmiş sürümde göründüğü), toplam 182 smoke testi (169→182).

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
- **Eski ArduPilot `CURR2`/`CURR3` mesaj adı desteği ve PX4'ün iki geçişli
  format-yeniden-tanımlama kırılganlığı:** ikisi de "Genel tarama" turunda
  gerçek bir logla doğrulanamadığı için ertelenmişti; bu turda web'de
  araştırıldı, ikisi de KAPATILDI.

  `CURR2`/`CURR3` — ArduPilot'un GitHub'daki güncel `AP_BattMonitor/
  LogStructure.h` ve `AP_Logger/LogStructure.h` dosyalarında böyle bir mesaj
  adı yok (sadece `BAT`, `Inst` alanıyla çoklu batarya destekliyor); resmi
  log mesajı dokümantasyonunda da geçmiyor. Backend'in zaten desteklediği
  `"CURR"` adının nereden geldiği belirsiz (çok eski bir sürümden kalma
  olabilir) ama `CURR2`/`CURR3`'ün gerçekten var olduğuna dair hiçbir iz
  bulunamadı — bulunsa bile o kadar eski bir logun indirilebilir bir
  kopyasını bulmak gerçekçi görünmüyor. Kod yazmak için gereken "gerçek log"
  şartı bu maddede muhtemelen hiç karşılanamayacak.

  PX4 format-yeniden-tanımlama — ULog dosya formatı spesifikasyonu bu konuda
  sessiz (ne izin veriyor ne yasaklıyor); ne PX4'ün resmi dokümantasyonunda
  ne `pyulog` deposunda buna karşılık gelen bir hata/issue bulundu. Normal
  tek-uçuşluk loglama akışında bir mesaj tipinin formatı logun başında bir
  kere yazılır, yeniden tanımlanmaz — bu risk gerçek bir log değil, kod
  okurken çıkarılan teorik bir senaryo. Tetikleyecek gerçek bir dosya
  bulunamadığı sürece kapsam dışı kalacak.

## Kodlama tercihleri / notlar

- Kullanıcı (proje sahibi) C++ ve Python'da öğrenme aşamasında. Kod üretirken
  kısa açıklamalar eklemek veya neden o şekilde yazıldığını belirtmek faydalı
  olur.
- Küçük, tek amaçlı fonksiyonlar / adımlar tercih edilir; büyük tek seferlik
  kod bloklarından kaçınılır.
- Değişiklikler küçük, test edilebilir adımlar halinde yapılır.
