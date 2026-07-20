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

## Mimari

İki dilli (polyglot), dosya tabanlı haberleşme (JSON üzerinden):

| Klasör | Görev |
|---|---|
| `backend/` | C++ (CMake). Log dosyasını parse edip `shared/` şemasına uygun JSON üretir. |
| `frontend/` | Python + CustomTkinter + Matplotlib. Dosya seçimi, backend'i çalıştırma, grafik çizimi. |
| `shared/` | Backend ↔ frontend arasındaki JSON veri sözleşmesi (şema + örnek). |
| `data/` | Test için örnek/sentetik uçuş logları. |

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

## Proje durumu

- [x] Python UI iskeleti
- [x] ArduPilot `.bin` parser'ı (batarya + motor/ESC)
- [x] PX4 `.ulog` parser'ı (batarya + motor/ESC)
- [x] Gerçek ve sentetik loglarla uçtan uca test
- [ ] Güç dağıtım (busbar) ısı haritası
- [ ] Birden fazla batarya desteği

## Lisans

MIT — bkz. [LICENSE](LICENSE).
