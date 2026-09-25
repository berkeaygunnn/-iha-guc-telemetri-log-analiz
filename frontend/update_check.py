"""GitHub Releases üzerinden yeni sürüm kontrolü.

Tk'den tamamen bağımsız, saf mantık — `flight_series.py`/`resample.py` ile
aynı desen. `fetch_latest_release` hiçbir zaman exception fırlatmaz: ağ
hatası/timeout/bozuk yanıt gibi tüm başarısızlık durumlarında sessizce
`None` döner, çünkü bu bir arka plan kolaylık özelliği — internet yoksa
uygulamayı hiç rahatsız etmemeli (bkz. main.py'deki çağıran taraf).
"""

import json
import urllib.error
import urllib.request

GITHUB_REPO = "berkeaygunnn/-iha-guc-telemetri-log-analiz"


def parse_version(text: str):
    """"v0.1.0" ya da "0.1.0" -> (0, 1, 0). Parse edilemezse None (hata
    fırlatmaz - bozuk/beklenmedik bir tag adı karşılaştırmayı sessizce
    imkansız kılmalı, uygulamayı çökertmemeli)."""
    if not text:
        return None
    text = text.strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    parts = text.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def is_newer_version(remote: str, local: str) -> bool:
    """remote, local'den büyük mü? İkisinden biri parse edilemiyorsa
    temkinli varsayılan: False (belirsizlikte "güncelleme var" denmez)."""
    remote_v = parse_version(remote)
    local_v = parse_version(local)
    if remote_v is None or local_v is None:
        return False
    return remote_v > local_v


def _select_installer_asset(assets: list):
    """assets: GitHub API'nin release['assets'] listesi. .exe ile biten ve
    adında 'zip' GEÇMEYEN ilk asset'i installer olarak seçer (isme değil bu
    kritere göre - iha_setup.iss'teki OutputBaseFilename değişse bile
    çalışmaya devam eder)."""
    for asset in assets:
        name = asset.get("name", "")
        if name.lower().endswith(".exe") and "zip" not in name.lower():
            return asset
    return None


def fetch_latest_release(repo: str = GITHUB_REPO, timeout: float = 5.0):
    """En son GitHub Release'i döner: {"version", "installer_name",
    "installer_url"} ya da (ağ hatası, bozuk yanıt, uygun asset yokluğu
    dahil HERHANGİ bir başarısızlıkta) None. Asla exception fırlatmaz."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None

    tag_name = payload.get("tag_name")
    if parse_version(tag_name) is None:
        return None

    asset = _select_installer_asset(payload.get("assets", []))
    if asset is None:
        return None

    return {
        "version": tag_name,
        "installer_name": asset.get("name", ""),
        "installer_url": asset.get("browser_download_url", ""),
    }
