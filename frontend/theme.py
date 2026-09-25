"""Arayüz tema sabitleri: renk paletleri, font ölçeği, spacing ölçeği.

Bu dosya SAF VERİ içerir — hiçbir widget kurmaz, hiçbir Tk/CTk çağrısı
yapmaz (CTkFont gibi Tk root'u gerektiren nesneler burada YOK, onlar
App.__init__ içinde bu dosyadaki FONT_SIZES'tan kurulur). main.py bu
modülden import eder; tema geçişi (_on_theme_toggle_click) mantığı
değişmedi, sadece sabitlerin tanım yeri buraya taşındı.
"""

# Koyu ve açık tema renk paletleri (koyu, dataviz rehberinin doğrulanmış
# referans paletinden). Grafik burada kendi renklerini tanımlıyor ki
# CustomTkinter'ın appearance mode'uyla birebir uyumlu olsun; matplotlib'in
# varsayılan beyaz arka planı kullanılmıyor. Giriş ekranının kendi ayrı
# LANDING_* paleti (aşağıda) bilinçli olarak bundan bağımsız kalır — zaten
# analiz ekranının teması ne olursa olsun sabit/"dikkat çekici" olacak
# şekilde tasarlanmıştı.
DARK_PALETTE = {
    "SURFACE": "#1a1a19",
    "TEXT_PRIMARY": "#ffffff",
    "TEXT_SECONDARY": "#c3c2b7",
    "TEXT_MUTED": "#898781",
    "GRIDLINE": "#2c2c2a",
    "AXIS_LINE": "#383835",
    "COLOR_CRITICAL": "#d03b3b",  # durum paleti: kritik/hata (backend hatası)
    "COLOR_WARNING": "#d9a334",   # durum paleti: uyarı (backend'in kural tabanlı yorumları)
    # PWM sapma ısı haritasının ıraksak (diverging) skalası: soğuk uç
    # (ortalamanın altı) → NÖTR GRİ orta nokta (sapma yok) → sıcak uç
    # (ortalamanın üstü). Orta noktanın gri olması şart: sapmasız bölgeler
    # göze çarpmamalı, dikkat sapmanın olduğu yere gitmeli. Durum paletiyle
    # (COLOR_WARNING/CRITICAL) bilerek aynı tonlar seçilmedi — bu bir ölçüm
    # skalası, "uyarı" değil.
    "PWM_DEVIATION_COLORS": ("#5aa9f0", "#2c2c2a", "#eb9b4f"),
    # Seri (batarya/motor/PWM kanalı) renkleri — kategori kimliği. Koyu
    # zeminde parlaklığı yüksek tonlar; açık temada bunlar soluk kaldığı
    # için LIGHT_PALETTE kendi (daha koyu/doygun) sürümünü taşır. Slot
    # sırası iki temada da AYNI varlığa denk gelir (Batarya 1 hep mavi).
    "SERIES_COLORS": (
        "#3987e5", "#008300", "#d55181", "#c98500",
        "#8a5fd1", "#1fada4", "#e0574a", "#b8b83c",
    ),
    # Isı haritası ardışık skalası: koyu yüzeyden markaya ait maviden (seri
    # slot 1 ile aynı ton) açık uca — düşük değer yüzeyde erir, yüksek değer
    # parlar.
    "HEATMAP_COLORS": ("#141a22", "#3987e5", "#cde2fb"),
}
LIGHT_PALETTE = {
    "SURFACE": "#f4f5f2",
    "TEXT_PRIMARY": "#1c1c1a",
    # İkincil/soluk tonlar bilerek koyu temadaki muadilleriyle AYNI kontrast
    # oranına ayarlandı; ilk değerleri açık zeminde soluk kalıyordu. Ölçüm
    # (WCAG, yüzey / istatistik kutucuğu zemini üzerinde):
    #   TEXT_SECONDARY  8.15 -> 9.83  (koyu temada 9.72)
    #   TEXT_MUTED      4.48 -> 6.06  ve kutucukta 3.74 -> 5.05; eskisi normal
    #                                  metin için AA sınırının (4.5) altındaydı
    #   COLOR_WARNING   4.12 -> 5.58  (uyarı satırları için)
    # Tonlar korundu, sadece açıklık düşürüldü.
    "TEXT_SECONDARY": "#3e3e39",
    "TEXT_MUTED": "#615c54",
    "GRIDLINE": "#e1e1db",
    "AXIS_LINE": "#c6c6bf",
    "COLOR_CRITICAL": "#b5231f",
    "COLOR_WARNING": "#8b5616",
    # Koyu temanın tersi yönde: açık zeminde uçların KOYU, orta noktanın açık
    # gri olması gerekiyor ki aynı "sapma parlar, sapmasızlık geri çekilir"
    # okuması korunsun. (Ardışık HEATMAP_COLORS'ın aksine bu skala temaya göre
    # değişiyor; orta noktanın zeminle uyumlu kalması buna bağlı.)
    "PWM_DEVIATION_COLORS": ("#1f6fb8", "#e1e1db", "#c2701a"),
    # Koyu temadaki tonların açık zemin (#f4f5f2) için koyulaştırılmış/
    # doygunlaştırılmış halleri — koyu paletteki değerler açık zeminde soluk
    # kalıp birbirine karışıyordu (kullanıcı geri bildirimi). Slot başına ton
    # kimliği korunur (Batarya 1 iki temada da mavi). Değerler ölçülerek
    # seçildi: her renk zemine karşı yeterli kontrast taşıyor ve ardışık
    # çiftler renk körlüğü simülasyonunda da ayrışıyor; 7. (kırmızı) bilerek
    # daha koyu, 8. (zeytin) bilerek daha açık — naif koyulaştırılmış çift
    # protanopide birbirinin aynısı çıkıyordu.
    "SERIES_COLORS": (
        "#2a78d6", "#006f00", "#c13d6c", "#9c6a00",
        "#6d44b8", "#147d77", "#b03225", "#8f9422",
    ),
    # Açık zeminde yön ters (PWM_DEVIATION_COLORS'daki açık-tema notuyla aynı
    # mantık): düşük uç zemine karışacak kadar açık, yüksek uç koyulaşarak
    # öne çıkar — "düşük değer erir, yüksek değer belirginleşir" okuması
    # iki temada da korunur.
    "HEATMAP_COLORS": ("#dce8f7", "#2a78d6", "#0d366b"),
}

# CustomTkinter widget'ları için (AÇIK, KOYU) renk ÇİFTLERİ. Yukarıdaki tekil
# sabitlerle (SURFACE, ...) FARKI ve NEDEN ikisi de gerekli:
#   - Tekil sabitler matplotlib figürü, giriş ekranı ve ham Tk navigasyon
#     toolbar'ı için kullanılır (bunları ctk yönetmez). Tema değişince
#     _on_theme_toggle_click içinde elle güncellenip grafik yeniden çizilir.
#   - Bir CTk widget'ına renk ÇİFTİ verilirse, ctk.set_appearance_mode()
#     çağrısında widget'ı PENCEREYE DOKUNMADAN, yerinde otomatik yeniden
#     renklendirir. Canlı tema geçişinin (pencereyi gizleyip yeniden kurmadan,
#     titremeden) çalışmasının anahtarı bu — analiz ekranındaki her CTk widget
#     bu UI_* çiftleriyle kurulur. Sıra (açık, koyu) olmalı; ctk "Light"i 0,
#     "Dark"ı 1. indeks olarak okur.
UI_SURFACE = (LIGHT_PALETTE["SURFACE"], DARK_PALETTE["SURFACE"])
UI_TEXT_PRIMARY = (LIGHT_PALETTE["TEXT_PRIMARY"], DARK_PALETTE["TEXT_PRIMARY"])
UI_TEXT_SECONDARY = (LIGHT_PALETTE["TEXT_SECONDARY"], DARK_PALETTE["TEXT_SECONDARY"])
UI_TEXT_MUTED = (LIGHT_PALETTE["TEXT_MUTED"], DARK_PALETTE["TEXT_MUTED"])
UI_GRIDLINE = (LIGHT_PALETTE["GRIDLINE"], DARK_PALETTE["GRIDLINE"])
UI_AXIS_LINE = (LIGHT_PALETTE["AXIS_LINE"], DARK_PALETTE["AXIS_LINE"])
UI_COLOR_CRITICAL = (LIGHT_PALETTE["COLOR_CRITICAL"], DARK_PALETTE["COLOR_CRITICAL"])
UI_COLOR_WARNING = (LIGHT_PALETTE["COLOR_WARNING"], DARK_PALETTE["COLOR_WARNING"])

# Giriş (splash) ekranı için ayrı, "dikkat çekici" bir palet — sadece landing
# ekranında kullanılır, analiz ekranının sakin koyu teması (SURFACE vb.)
# bundan etkilenmez. Ana mavi (SERIES_COLORS[0]) ile aynı aile, ama daha
# doygun/parlak.
LANDING_BG = "#07131f"              # koyu lacivert taban
LANDING_ACCENT = "#3987e5"          # mevcut ana mavi ile tutarlılık
LANDING_ACCENT_BRIGHT = "#5fd4ff"   # parlak camgöbeği, ikon/vurgu için
LANDING_TEXT = "#eaf4ff"
LANDING_CARD = "#152d45"            # geçmiş dosya kartlarının arka planı — sidebar
                                     # zemininden (#0c1e30) belirgin ayrışsın diye
                                     # bilerek epey açık, artık gerçek bir "kutu" gibi okunuyor
LANDING_CARD_BORDER = "#24445f"      # kartın ince kenarlığı, dikdörtgen sınırı netleştirir
LANDING_BADGE_ULOG = "#1c3f66"       # .ulog/.ulg format rozeti — LANDING_ACCENT'tan (hover rengi)
                                     # BİLEREK farklı, daha koyu lacivert: aksi halde imleç kartın
                                     # üzerine gelince rozet arka planla aynı renge karışıp kayboluyordu
LANDING_BADGE_BIN = "#c9822f"        # .bin format rozeti (ULG'den ayırt edilsin diye sıcak ton)
LANDING_TEXT_SECONDARY = "#a9c3de"   # geçmiş kartlarındaki "az önce · 1544s" meta yazısı — TEXT_SECONDARY
                                     # KASITLI olarak kullanılmıyor: o tema değişince (koyu/açık) değişen bir
                                     # sabit, LANDING_CARD'ın her zaman koyu lacivert kalan zeminiyle açık
                                     # temada neredeyse hiç kontrastı kalmazdı (koyu gri/koyu lacivert)
LANDING_FOOTER_TEXT = "#3a5a78"      # alt bilgi satırı ("... MIT Lisansı ile açık kaynak ...") — bilerek
                                     # soluk: sayfanın en az önemli metni, dikkat çekmemeli
LANDING_SIDEBAR_WIDTH = 290          # _build_recent_sidebar'daki sabit genişlik; başlık/açıklama
                                     # metinlerinin kalan alana göre ne zaman sarması gerektiğini
                                     # hesaplayabilmek için burada da adlandırılmış halde tutuluyor
ANALYSIS_SIDEBAR_WIDTH = 170         # _build_analysis_sidebar'daki gezinme rayının sabit genişliği
                                     # (LANDING_SIDEBAR_WIDTH'ten dar: sadece birkaç aç/kapat anahtarı
                                     # taşıyor, geçmiş dosya kartları gibi geniş içerik yok). Analiz
                                     # ekranındaki wraplength hesapları (_on_analysis_frame_configure)
                                     # bu payı analysis_frame'in toplam genişliğinden düşmek zorunda.
# "Uçuşları Karşılaştır" penceresi SADECE giriş ekranından açılıyor ve giriş
# ekranı temadan bağımsız hep koyu lacivert — dialog aktif temayı takip
# edince açık temada "koyu zemin üstünde beyaz pencere" gibi kopuk duruyordu
# (kullanıcı geri bildirimi). Dialog bu yüzden LANDING paletiyle çiziliyor.
LANDING_ROW_STRIPE = "#0f2236"       # karşılaştırma tablosunun zebra bandı: LANDING_BG ile
                                     # LANDING_CARD arasında bir ara ton
LANDING_WARNING = DARK_PALETTE["COLOR_WARNING"]    # koyu zeminde okunan uyarı/kritik tonları —
LANDING_CRITICAL = DARK_PALETTE["COLOR_CRITICAL"]  # temalı singles açık temada laciverte karşı
                                                   # okunaksız kalabilirdi, sabitlere bağlandı

# Font ölçeği: mevcut ~22 dağınık ctk.CTkFont(size=...) çağrısındaki
# değerlerin isimlendirilmiş hali (yeni sayı icat edilmedi, var olanlar
# sınıflandırıldı). CTkFont nesnesi Tk root'u gerektirdiği için burada
# SAYI olarak tutulur; gerçek CTkFont nesneleri App.__init__ içinde
# self.fonts = {ad: ctk.CTkFont(size=FONT_SIZES[ad], ...)} ile kurulur.
FONT_SIZES = {
    "display": 32,     # giriş ekranı başlığı
    "heading": 16,      # dialog başlıkları
    "subheading": 14,   # giriş ekranı açıklaması
    "body": 12,         # genel gövde metni, buton yazıları
    "label": 11,        # ikincil etiketler
    "small": 10,        # en soluk/az önemli metin
}
# İstatistik kutucuğu değerleri gibi sayısal/tablo verisi için sabit
# genişlikli bir font — hizalama okunabilirliği artırır. Windows'ta hazır
# geliyor, ek bir font dosyası paketlemeye gerek yok.
MONO_FONT_FAMILY = "Consolas"

# Spacing ölçeği (px). Bilinçli olarak SADECE yeni/dokunulan alanlarda
# kullanılır — main.py'deki mevcut 77 padx=/pady= çağrısını toplu değiştirmek
# CLAUDE.md'de daha önce ölçülüp "büyük/riskli diff" diye ertelenmişti.
SPACING = {
    "xs": 4,
    "sm": 8,
    "md": 16,
    "lg": 24,
}
