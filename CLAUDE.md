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
