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
