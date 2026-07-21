# İHA Güç/Telemetri Log Analiz Aracı

ArduPilot (`.bin`) veya PX4 (`.ulog`) uçuş kontrolcülerinden alınan uçuş sonrası
log dosyalarını okuyup; batarya voltaj düşümünü (voltage sag) ve motor/ESC akım
çekimini grafiklere dönüştüren bir masaüstü uygulaması.

## Özellikler

- **ArduPilot `.bin` desteği** — kendini tanımlayan (FMT mesajlı) ikili format,
  batarya (`BAT`) ve motor (`ESC`) mesajlarını doğrudan çözer.
- **PX4 `.ulog` desteği** — Format/Subscription/Data mesaj yapısını çözer,
  `battery_status` ve iç içe dizili `esc_status`/`esc_report` verisini okur.
- Log formatı dosya içeriğinden (magic byte) otomatik algılanır; kullanıcı
  format seçmek zorunda değildir.
- Koyu temalı arayüz: voltaj, toplam akım ve motor bazlı akım ayrı panellerde
  (ortak zaman eksenini paylaşarak) çizilir.
- Motor paneli, çizgi grafiği ile motor × zaman ısı haritası arasında tek
  tıkla geçiş yapabiliyor.
- Toplam akım (busbar yüklenmesi) paneli de aynı şekilde çizgi grafiği ↔
  batarya × zaman ısı haritası arasında geçiş yapabiliyor; birden fazla
  batarya (yedekli güç hattı) olduğunda hangisinin ne zaman daha yüklü
  olduğunu karşılaştırmak için kullanışlı.
- Birden fazla batarya (ör. ana + yedek güç kaynağı) ayrı ayrı, tutarlı
  renklerle voltaj/akım panellerinde gösterilir.
- Kural tabanlı otomatik uyarılar: aşırı voltaj düşümü (%15+) ve motorlar
  arası akım dengesizliği (%20+) gerçek loglarla kalibre edilmiş eşiklerle
  otomatik tespit edilip arayüzde gösterilir (bkz.
  `shared/power_log_schema.md`'deki `warnings` alanı).

## Mimari

İki dilli (polyglot), dosya tabanlı haberleşme (JSON üzerinden):

| Klasör | Görev |
|---|---|
| `backend/` | C++ (CMake). Log dosyasını parse edip `shared/` şemasına uygun JSON üretir. |
| `frontend/` | Python + CustomTkinter + Matplotlib. Dosya seçimi, backend'i çalıştırma, grafik çizimi. |
| `shared/` | Backend ↔ frontend arasındaki JSON veri sözleşmesi (şema + örnek). |
| `data/` | Test için örnek/sentetik uçuş logları (ArduCopter, PX4 quadrotor/hexarotor/rover/sabit kanat). |

Backend ve frontend bağımsız çalışır; aralarında doğrudan bir çağrı yoktur,
sadece dosya üzerinden JSON alışverişi vardır.

## Kurulum ve çalıştırma

### Backend'i derleme

```
cd backend
cmake -S . -B build -G "Ninja"
cmake --build build
```

> Not: CMake'in varsayılan "MinGW Makefiles" generator'ı, yol adında Türkçe/Unicode
> karakter (ör. `İ`, `ü`) varsa hataya düşebilir. `Ninja` generator'ı bu sorunu yaşamaz.

### Frontend'i çalıştırma

```
cd frontend
pip install -r requirements.txt
python main.py
```

Açılan pencerede **Log Dosyası Yükle** ile `data/` klasöründeki örnek
loglardan birini (ya da kendi `.bin`/`.ulog` dosyanı) seç.

## Dağıtım (paketleme)

Geliştirici olmayan biri Python/CMake kurmadan uygulamayı kullanabilsin diye,
frontend PyInstaller ile tek bir klasöre (`.exe` + gerekli her şey) paketlenebilir:

```
.\scripts\build_release.ps1
```

Bu script backend'i derler, sonra `frontend/power_log_frontend.spec` dosyasına
göre paketler. Sonuç: `dist/iha_guc_telemetri_analiz/` klasörü — bu klasörü
olduğu gibi zip'leyip paylaşabilirsin; kullanıcı sadece içindeki
`iha_guc_telemetri_analiz.exe`'yi çalıştırır (backend'in derlenmiş hâli aynı
klasörde bulunduğu için ayrıca CMake/Ninja kurmasına gerek yoktur).

Not: paket örnek `data/` log dosyalarını içermez — küçük kalması için;
kullanıcı kendi `.bin`/`.ulog` dosyasını yükler.

## Proje durumu

- [x] Python UI iskeleti
- [x] ArduPilot `.bin` parser'ı (batarya + motor/ESC)
- [x] PX4 `.ulog` parser'ı (batarya + motor/ESC)
- [x] Gerçek ve sentetik loglarla uçtan uca test
- [x] Güç dağıtım (busbar) ısı haritası
- [x] Birden fazla batarya desteği
- [x] Kural tabanlı otomatik uyarı sistemi (voltaj düşümü + motor dengesizliği)
- [x] PyInstaller ile paketleme (tek klasörlük dağıtım)

## Lisans

MIT — bkz. [LICENSE](LICENSE).
