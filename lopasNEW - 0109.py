import time
from datetime import datetime
import os
import random
import warnings
import re
import json
import threading
from queue import Queue, Empty
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import base64
import platform
import tempfile
import shutil
import uuid  # correlation_id-hoz
from collections import deque
import sys

warnings.filterwarnings("ignore", category=ResourceWarning)

import requests
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchWindowException,
    WebDriverException,
)

# --- DEBUG kapcsoló HTTP hívásokhoz ---
DEBUG_HTTP = os.getenv("DEBUG_HTTP", "0") == "1"

IS_MAC = (platform.system() == "Darwin")
KEY_MOD = Keys.COMMAND if IS_MAC else Keys.CONTROL

# --- Driver életjelző ---
DRIVER_DEAD = False

def _is_driver_connection_error(exc: Exception) -> bool:
    """
    Felismeri a klasszikus 'HTTPConnectionPool / WinError 10061 / Max retries exceeded' típusú hibákat,
    amikor a WebDriver HTTP szerver már halott.
    """
    txt = str(exc)
    if "HTTPConnectionPool" in txt and "/window/handles" in txt:
        return True
    if "Failed to establish a new connection" in txt:
        return True
    if "WinError 10061" in txt:
        return True
    if "Max retries exceeded with url: /session/" in txt:
        return True
    return False


def _safe_window_handles(label: str):
    """
    driver.window_handles biztonságos wrapper:
    - DRIVER_DEAD vagy driver is None → üres lista
    - driver/window_handles hiba esetén:
        - ha connection error → DRIVER_DEAD=True
        - logol, és üres listát ad vissza
    """
    global driver, DRIVER_DEAD

    if DRIVER_DEAD or driver is None:
        return []

    try:
        return driver.window_handles
    except WebDriverException as e:
        msg = str(getattr(e, "msg", str(e))).splitlines()[0]
        if _is_driver_connection_error(e):
            DRIVER_DEAD = True
            warn(f"[win_handles] driver leállt (WebDriverException): {msg} (label={label})")
            return []
        warn(f"[win_handles] hiba: {msg} (label={label})")
        return []
    except Exception as e:
        msg = str(e).splitlines()[0]
        if _is_driver_connection_error(e):
            DRIVER_DEAD = True
            warn(f"[win_handles] driver leállt (Exception): {msg} (label={label})")
            return []
        warn(f"[win_handles] váratlan hiba: {msg} (label={label})")
        return []


# ---------- CONFIG ----------
DEFAULT_BASE = "https://en.surebet.com"
LOGIN_URL = "https://surebet.com/users/sign_in"
CHECK_INTERVAL = 1.25
MAIN_URL = "https://en.surebet.com/surebets"


ACCOUNTS = {
    "acc1": {  # első account
        "email": "nosztalgiakonzol@gmail.com",
        "password": "Pankix123!",
        "profile_dir": os.path.abspath("./profile_surebet_acc1"),
    },
    "acc2": {  # második account
        "email": "secretcodeforme@gmail.com",
        "password": "Pankix123!",
        "profile_dir": os.path.abspath("./profile_surebet_acc2"),
    },
}

ACCOUNT_ROTATE_MIN = float(os.getenv("SB_ACCOUNT_ROTATE_MIN", "32"))

# Parancssori argumentum feldolgozás (--acc=acc1 vagy --acc=acc2)
forced_account = None
for arg in sys.argv:
    if arg.startswith("--acc="):
        forced_account = arg.split("=", 1)[1].strip()

# Opcionális: env változóval is válthatsz (SB_ACTIVE_ACCOUNT=acc2)
env_account = os.getenv("SB_ACTIVE_ACCOUNT")

if forced_account in ACCOUNTS:
    ACTIVE_ACCOUNT_KEY = forced_account
elif env_account in ACCOUNTS:
    ACTIVE_ACCOUNT_KEY = env_account
else:
    ACTIVE_ACCOUNT_KEY = "acc1"   # default

ACTIVE_ACCOUNT = ACCOUNTS[ACTIVE_ACCOUNT_KEY]




WAIT_FOR_REDIRECT = 15
SEEN_FILE = "seen_ids.txt"
FOUND_LINKS_FILE = "found_links.txt"
LINK_CACHE_FILE = "link_cache.json"

# Supabase Edge Functions
SUPABASE_URL = "https://sonudgyyvxncdcganppl.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNvbnVkZ3l5dnhuY2RjZ2FucHBsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjAwMzA5NDMsImV4cCI6MjA3NTYwNjk0M30.QhtBEhUYoZU8dukJ2bNcy95bXW7unxln8NPe_13eBQ4"

SAVE_TIP_URL    = f"{SUPABASE_URL}/functions/v1/save-tip"
UPDATE_TIP_URL  = f"{SUPABASE_URL}/functions/v1/update-tip"
DELETE_TIP_URL  = f"{SUPABASE_URL}/functions/v1/delete-tip"
UPDATE_TIPS_BATCH_URL = f"{SUPABASE_URL}/functions/v1/update-tips-batch"
DELETE_TIPS_BATCH_URL = f"{SUPABASE_URL}/functions/v1/delete-tips-batch"

HTTP_HEADERS = {
    "Content-Type": "application/json",
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
}

# Gyors beállítások
RESOLVE_TIMEOUT = 1.5
RESOLVE_STABLE_PERIOD = 0
RESOLVE_POLL_INTERVAL = 0
HANDLE_WAIT_TIMEOUT = 0.5
HEADLESS = False

FIX_URL_WAIT_SEC = 20
NAV_HARD_LIMIT_SEC = 20.0

NAV_DEBUG_INTERVAL = 2.0  # másodpercenkénti NAV debug log (0 = kikapcsolva)

TAB_CLEANUP_INTERVAL = 250.0   # ennyi másodpercenként nézünk rá a nyitott tabokra (3 perc)
TAB_CLEANUP_MIN_AGE = 70.0     # ennél fiatalabb ismeretlen tabot nem zárunk be (biztonsági buffer)

# Egyszerre ennyi tbody-pár / linkpár fusson a NAV workerben
NAV_WORKER_MAX_PAIRS = 11

# Egy párra mennyi ideig várunk maximum (másodpercben)
PAIR_TIMEOUT_SEC = FIX_URL_WAIT_SEC  # most 20 mp, ugyanaz mint a régi FIX_URL_WAIT_SEC

# Milyen gyakran kérdezzük le CDP-vel a Target.getTargets-et (másodperc)
CDP_POLL_INTERVAL = 0.15  # 150 ms körül

# Logoljuk-e, ha egy pár mindkét végső linkje megvan és a pár lezárult
LOG_PAIR_DONE = True


OPEN_WITHIN_PAIR_MS = 75             # két link ugyanazon párban: A 0ms, B +80ms
OPEN_PAIR_STAGGER_MS_BASE = 175  

# NAV-specifikus feloldás
NAV_MIN_WAIT = 0.0         # en.surebet.com/nav-on minimum türelmi idő
RESOLVE_TIMEOUT_NAV = 3   # NAV-on hosszabb plafon
NAV_STABLE_AFTER_EXIT = 0.42 # ha kimentünk NAV-ról, ennyit várunk stabilan

# --- BOOTSTRAP FÁZIS: indulás után X másodpercig csak tabnyitás + ID-gyűjtés ---
RUN_STARTED_AT = 0.0        # induláskor beállítjuk __main__-ben
BOOTSTRAP_SEC = 50.0        # ennyi másodpercig megy a "csak nyitunk mindent" fázis

def in_bootstrap_phase() -> bool:
    """
    True: az első BOOTSTRAP_SEC másodpercben a script indulásától.
    Ezalatt:
      - NINCS SAVE / UPDATE / DELETE Supabase felé
      - NINCS NAV worker
      - csak main/group/next oldalak nyitása + tbody ID gyűjtés történik
    """
    if RUN_STARTED_AT <= 0:
        return True
    return (time.time() - RUN_STARTED_AT) < BOOTSTRAP_SEC

# --- ACTIVE / GONE ---
ACTIVE_FILE = "active_ids.txt"
DISAPPEAR_GRACE_SEC = 4.5

# --- UPDATE CONFIG ---
UPDATE_MIN_INTERVAL = 2.0
UPDATE_DECIMALS = 2

# --- GROUP-LINK KEZELÉS ---
GROUP_EMPTY_CLOSE_TB_THRESHOLD = 1
GROUP_REOPEN_BACKOFF_SEC = 120
GROUP_ERR_BACKOFF_SEC = 90
GROUP_SELECTOR = "tbody.surebet_record"

# --- GROUP RÉSZLEGES REFRESH ---
GROUP_REFRESH_MIN = 35
GROUP_REFRESH_MAX = 55
GROUP_REFRESH_SKIP_ON_NEW_SEC = 10

# --- MAIN OLDAL PLAY/REFRESH ---
MAIN_REFRESH_MIN = 50
MAIN_REFRESH_MAX = 75

# --- MAIN PAGINATE WRAPPER REFRESH ---
MAIN_PAGINATE_REFRESH_MIN = 70
MAIN_PAGINATE_REFRESH_MAX = 90

# --- NEXT PAGE KEZELÉS ---
NEXT_REFRESH_MIN = 28
NEXT_REFRESH_MAX = 42
NEXT_SELECTOR = "tbody.surebet_record"
NEXT_EMPTY_CLOSE_TB_THRESHOLD = 0

# --- LOG kapcsoló ---
LOG_ENABLED = True
# Csendesítők a "már nyitva" spamre:
LOG_GROUP_ALREADY_OPEN_VERBOSE = False  # ha True, ír; ha False, elnémítva
LOG_NEXT_ALREADY_OPEN_VERBOSE  = False  # ha True, ír; ha False, elnémítva

# --- NAV backoff ---
NAV_RETRY_BASE = 20.0   # sec
NAV_RETRY_MAX  = 300.0  # sec

# NAV-specifikus időzítések NAV-only feloldáshoz
NAV_LEAVE_TIMEOUT = 3      # max ennyi ideig várunk, hogy elhagyja a surebet.com-ot
NAV_STABLE_PERIOD = 0.0                      # ha >0, ennyit várunk stabilan a külső URL-en mielőtt elfogadjuk
NAV_LEAVE_POLL_INTERVAL = 0.005

# --- ROUND-ROBIN RESOLVER ---
ROUND_ROBIN_MAX_MS = 7000     # meddig pörgünk összesen egy csomagon (ms)
ROBIN_SPIN_SLEEP = 0.15        # 0.0 – tényleg full-gáz pörgetés
MAX_BODY_SNIFF = 1200         # ennyi karakterig nézünk bele a body-ba "not found"-ot keresni

HMAP_MAX_SEC = 60  # max ennyi másodpercet engedünk hmap + URL olvasásra

# --- NAV CDP DEBUG (URL figyelés tabváltás nélkül) ---
DEBUG_NAV_CDP = True          # ha zavar a log, állítsd False-ra
DEBUG_NAV_CDP_INTERVAL = 2.0  # másodpercenként logoljuk a NAV / külső page targeteket


def _cdp_dump_nav_targets(label: str = ""):
    """
    CDP-ből kiírja az összes 'page' target URL-jét, ami:
      - surebet.com/nav ... VAGY
      - bármilyen külső http(s) host (valid_external)
    """
    if not DEBUG_NAV_CDP:
        return
    try:
        info = _safe_cdp_cmd("Target.getTargets", {}, label=f"NAVCDP dump {label}")
        if not isinstance(info, dict):
            return
        targets = info.get("targetInfos", []) or []
    except Exception as e:
        warn(f"[NAVCDP] Target.getTargets hiba: {e}")
        return

    lines = []
    for t in targets:
        try:
            if t.get("type") != "page":
                continue
            url = (t.get("url") or "").strip()
            if not url:
                continue

            # Csak a NAV és a külső oldalak érdekesek
            if "surebet.com/nav" in url or valid_external(url):
                tid = t.get("targetId")
                lines.append(f"    - {tid} | {url}")
        except Exception:
            continue

    if lines:
        log(f"[NAVCDP] {label} {len(lines)} target:")
        for ln in lines:
            print(ln)


EARLY_ACCEPT_POLL_MS = 200
EARLY_ACCEPT_MAX_SEC = 8

def log(msg):
    if LOG_ENABLED:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def warn(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_seen():
    s = set()
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(" | ", 1)
                if len(parts) == 2:
                    _, tid = parts
                    s.add(tid)
    return s

def save_seen_line(tbody_id):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {tbody_id}\n")

def remove_seen_line(tbody_id):
    if not os.path.exists(SEEN_FILE):
        return
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.readlines() if f" | {tbody_id}" not in ln]
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass

def load_active():
    s = set()
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                tid = line.strip()
                if tid:
                    s.add(tid)
    return s

def save_active_all(active_set: set):
    with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
        for tid in sorted(active_set):
            f.write(tid + "\n")

def load_link_cache():
    if os.path.exists(LINK_CACHE_FILE):
        try:
            with open(LINK_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_link_cache(cache: dict):
    try:
        with open(LINK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    

# ---------- Chrome init (100% friss profil minden indításnál) ----------

PROFILE_DIR = ACTIVE_ACCOUNT["profile_dir"]
os.makedirs(PROFILE_DIR, exist_ok=True)

chrome_options = Options()

if HEADLESS:
    chrome_options.add_argument("--headless=new")

# 🔥 Minden account a saját fix profilkönyvtárát használja
chrome_options.add_argument(f"--user-data-dir={PROFILE_DIR}")

# (Opcionális) ha akarod mellé, maradhat az incognito is, de nem szükséges:
# chrome_options.add_argument("--incognito")

# Gyorsító / tiltó flag-ek
chrome_options.add_argument("--disable-features=OptimizationHints,TranslateUI")
chrome_options.add_argument("--disable-site-isolation-trials")
chrome_options.add_argument("--disable-translate")
chrome_options.add_argument("--disable-infobars")
chrome_options.add_argument("--disable-sync")
chrome_options.add_argument("--disable-client-side-phishing-detection")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--window-size=960,540")
chrome_options.add_argument("--disable-popup-blocking")
chrome_options.add_argument(
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Prefs 1
prefs1 = {
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False,
    "profile.default_content_setting_values.notifications": 2,
    "translate_whitelists": {"lt": "en"},
    "translate": {"enabled": "true"},
}
chrome_options.add_experimental_option("prefs", prefs1)

# Kép / geolocation / camera tiltás
prefs2 = {
    "profile.default_content_setting_values.popups": 1,
    "profile.managed_default_content_settings.images": 2,
    "profile.managed_default_content_settings.geolocation": 2,
    "profile.managed_default_content_settings.notifications": 2,
    "profile.managed_default_content_settings.media_stream": 2,
}
chrome_options.add_experimental_option("prefs", prefs2)

# Logging
try:
    chrome_options.set_capability("pageLoadStrategy", "eager")
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
except Exception:
    pass

# 🔥 Chrome indítása egyszer, tisztán
try:
    driver = uc.Chrome(options=chrome_options, version_main=143)
except Exception as e:
    print(f"First Chrome start attempt failed: {e}")
    try:
        driver = uc.Chrome(options=chrome_options)
    except Exception as e2:
        print(f"❌ Chrome start FAILED: {e2}")
        raise SystemExit(1)

uc.Chrome.__del__ = lambda self: None



# --- CDP gyorsítók / tiltások ---
try:
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setBypassServiceWorker", {"bypass": True})
    driver.execute_cdp_cmd("Network.setBlockedURLs", {
        "urls": [
            "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
            "*.ico", "*favicon*", "*apple-touch-icon*", "*mask-icon*", "*mstile*"
        ]
    })
    try:
        driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-reduced-motion", "value": "reduce"}]
        })
    except Exception:
        pass

    log("🧱 Globális blokkolás aktív (fontok), SW bypass, reduced motion.")

    driver.execute_cdp_cmd("Page.enable", {})

    # 1. injektor: jelöld EXT ablakokat + window.name getter/setter védelem
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": r"""
        (function(){
          try {
            if (location && location.hostname && !/(\.|^)surebet\.com$/i.test(location.hostname)) {
              try { window.name = (window.name || '') + '|EXT'; } catch(e){}
            }
            try {
              Object.defineProperty(window, 'name', {
                configurable: true,
                enumerable: true,
                set: function(v){ try{ this._n=v; }catch(e){} return v; },
                get: function(){ try{ return this._n || ''; }catch(e){} return ''; }
              });
            } catch(e){}
          } catch(e){}
        })();
        """
    })

    # 2. injektor: olcsó flag, hogy külső oldalon vagyunk
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": r"""
        (function(){
          try{
            var host=(location.hostname||"").toLowerCase();
            var isSB=/(\.|^)surebet\.com$/.test(host);
            if(!isSB){ try{ window.__SB_EXT__=1; }catch(e){} }
          }catch(e){}
        })();
        """
    })

except Exception as e:
    warn(f"⚠️ CDP init részben sikertelen: {e}")


try:
    driver.set_script_timeout(30)
except Exception:
    pass
    


# gyenge animációtiltás (CSS) – best-effort injektor
def _inject_disable_animations():
    try:
        driver.execute_script("""
        (function(){
          try{
            var st = document.getElementById('__noanim');
            if (st) return;
            st = document.createElement('style');
            st.id='__noanim';
            st.textContent='*{animation:none!important;transition:none!important;scroll-behavior:auto!important}';
            document.head && document.head.appendChild(st);
          }catch(e){}
        })();
        """)
    except Exception:
        pass
        
def tiny_keepalive_ping():
    """
    Pici scroll fel-le, hogy legyen activity a fő tabon.
    """
    try:
        _safe_execute_script("window.scrollBy(0, 1); window.scrollBy(0, -1);")
        _safe_execute_script("window.scrollBy(0, -1);")
    except Exception:
        pass


def ensure_active_window():
    try:
        h = driver.current_window_handle
        if h in driver.window_handles:
            return True
    except Exception:
        pass
    try:
        handles = driver.window_handles
        if not handles:
            return False
        if 'MAIN_HANDLE' in globals() and MAIN_HANDLE and MAIN_HANDLE in handles:
            driver.switch_to.window(MAIN_HANDLE)
            return True
        driver.switch_to.window(handles[0])
        return True
    except Exception:
        return False


def _safe_execute_script(script, *args):
    """
    driver.execute_script biztonságos wrapper:
    - ha közben bezáródott a window, megpróbálunk visszaváltani egy élőre
    - 2 próbálkozás NoSuchWindowException esetén
    """
    for _ in range(2):
        try:
            if not ensure_active_window():
                raise NoSuchWindowException("No alive window to execute script")
            return driver.execute_script(script, *args)
        except NoSuchWindowException:
            time.sleep(0.05)
            continue
    # ha eddig sem sikerült, még egy utolsó próbálkozás
    if not ensure_active_window():
        raise NoSuchWindowException("No alive window after retries")
    return driver.execute_script(script, *args)


def _safe_execute_async_script(script, *args):
    for _ in range(2):
        try:
            if not ensure_active_window():
                raise NoSuchWindowException("No alive window to execute async script")
            return driver.execute_async_script(script, *args)
        except NoSuchWindowException:
            time.sleep(0.05)
            continue
    if not ensure_active_window():
        raise NoSuchWindowException("No alive window after retries (async)")
    return driver.execute_async_script(script, *args)
    
def _safe_cdp_cmd(method: str, params: dict | None = None, *, label: str = ""):
    """
    CDP hívásokhoz védőréteg.
    - Ha DRIVER_DEAD=True → azonnal skip
    - Ha nincs driver, vagy nincsenek window handle-ök → visszaad None-t.
    - Ha 'no such window' / 'web view not found' / stb. hibát kapunk → log + None.
    - Ha driver connection error (HTTPConnectionPool / WinError 10061...), akkor DRIVER_DEAD=True,
      és innentől minden cdp hívás skip-el.
    """
    global driver, DRIVER_DEAD

    if params is None:
        params = {}

    # ha már tudjuk, hogy halott
    if DRIVER_DEAD:
        warn(f"[CDP] {method} skip – DRIVER_DEAD=True (label={label})")
        return None

    # driver már None? (pl. shutdown / restart közben)
    if driver is None:
        warn(f"[CDP] {method} skip – driver is None (label={label})")
        return None

    # van-e élő window? (safe wrapperrel)
    handles = _safe_window_handles(label=f"{method} pre")
    if DRIVER_DEAD:
        warn(f"[CDP] {method} skip – DRIVER_DEAD=True window_handles után (label={label})")
        return None
    if not handles:
        warn(f"[CDP] {method} skip – nincs window (label={label})")
        return None

    try:
        return driver.execute_cdp_cmd(method, params)
    except Exception as e:
        msg = str(e).lower()

        # ha ez is driver connection error → beállítjuk a flaget
        if _is_driver_connection_error(e):
            DRIVER_DEAD = True
            warn(f"[CDP] {method} driver-connection hiba, DRIVER_DEAD=True (label={label}): {e}")
            return None

        # tipikus „ablak megszűnt” hibák
        if (
            "no such window" in msg
            or "web view not found" in msg
            or "disconnected: not connected to devtools" in msg
            or "chrome not reachable" in msg
        ):
            warn(f"[CDP] {method} skip – window already closed/devtools detached (label={label}): {e}")
            return None

        # egyéb CDP hiba – logoljuk, de nem ölünk meg semmit
        warn(f"[CDP] {method} hiba (label={label}): {e}")
        return None


# ---------- URL utilok ----------
def is_http_url(u: str | None) -> bool:
    if not u: return False
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def is_surebet_url(u: str | None) -> bool:
    if not u: return False
    try:
        host = urlparse(u).netloc.lower()
        return host.endswith("surebet.com")
    except Exception:
        return False

def is_nav_url(u: str | None) -> bool:
    if not u: return False
    try:
        p = urlparse(u)
        return p.netloc.lower().endswith("surebet.com") and p.path.startswith("/nav")
    except Exception:
        return False

def valid_external(u: str | None) -> bool:
    return is_http_url(u) and not is_surebet_url(u)

def _maybe_b64_decode(s: str) -> str | None:
    s2 = (s or "").strip()
    if not re.match(r'^[A-Za-z0-9+/=_-]{8,}$', s2):
        return None
    try:
        pad = '=' * (-len(s2) % 4)
        for variant in (s2, s2.replace('-', '+').replace('_', '/')):
            try:
                return base64.b64decode(variant + pad).decode('utf-8', errors='ignore')
            except Exception:
                continue
        return None
    except Exception:
        return None

def extract_target_from_nav(nav_url: str) -> str | None:
    try:
        p = urlparse(nav_url)
        q = parse_qs(p.query)
        keys = ["to","url","u","target","redirect","dest","link","r","q"]
        for k in keys:
            if k in q and q[k]:
                raw = q[k][0]
                cand = unquote(raw)
                if cand and cand.startswith(("http://","https://")):
                    return cand
                b = _maybe_b64_decode(raw)
                if b and b.startswith(("http://","https://")):
                    return b
                m = re.search(r'(https?://[^\s"\'<>]+)', cand)
                if m:
                    return m.group(1)
        return None
    except Exception:
        return None
        
# --- "Page not found" detektor + gyors tab állapot olvasó ---

NOT_FOUND_PATTERNS = [
    r"\bpage not found\b",
    r"\b404\b",
    r"\bnot found\b",
    r"\bseite nicht gefunden\b",
    r"\bстраница не найдена\b",
    r"\bpagina non trovata\b",
    r"\bpágina no encontrada\b",
    r"\bno encontrado\b",
    r"\bhittades inte\b",
]

def _looks_not_found_text(txt: str) -> bool:
    if not txt:
        return False
    lo = txt.lower()
    for pat in NOT_FOUND_PATTERNS:
        if re.search(pat, lo):
            return True
    return False
    
def _surebet_h1_not_found() -> bool:
    """
    Hipergyors 404-check: csak az <h1 class="title"> szöveget olvassa.
    True: ha pontosan 'Page not found' (case-insensitive/trim).
    """
    try:
        return bool(_safe_execute_script(r"""
            try {
              var h = document.querySelector('h1.title');
              if (!h) return false;
              var t = (h.textContent || '').trim().toLowerCase();
              return t === 'page not found';
            } catch(e) { return false; }
        """))
    except Exception:
        return False


def _superfast_external_url_or_none():
    """
    VILLÁM: 1x execute_script – ha location.href már http(s) és NEM surebet.com → visszaadjuk.
    Ha az injektor beállította a window.__SB_EXT__-et, az is jó jel; ilyenkor
    egy driver.current_url fallback olvasást még megpróbálunk.
    """
    try:
        href, extflag = _safe_execute_script("return [location.href||'', !!window.__SB_EXT__];")
        href = (href or '').strip()
    except Exception:
        href, extflag = "", False

    if href.startswith(("http://","https://")) and not is_surebet_url(href):
        return _sanitize_url(href)

    if extflag:
        try:
            cur = driver.current_url
            if cur.startswith(("http://","https://")) and not is_surebet_url(cur):
                return _sanitize_url(cur)
        except Exception:
            pass

    return None


def _looks_not_found(title_l: str, body_l: str) -> bool:
    # minimál: elég ha bármelyikben felismerjük
    return _looks_not_found_text(title_l) or _looks_not_found_text(body_l)

def _left_surebet(cur: str | None) -> bool:
    return is_http_url(cur) and not is_surebet_url(cur)

def _read_tab_state_quick() -> tuple[str, str, str]:
    """
    Gyors állapotolvasás az aktuális tabról:
    - current_url
    - document.title (lowercased)
    - body innerText (lowercased, MAX_BODY_SNIFF-ig vágva)
    """
    try:
        cur = driver.current_url
    except Exception:
        cur = "about:blank"

    try:
        title = _safe_execute_script("return document.title || ''") or ""
    except Exception:
        title = ""
    try:
        body_text = _safe_execute_script(
            "return (document.body && document.body.innerText) || ''"
        ) or ""
    except Exception:
        body_text = ""

    title_l = title.lower()
    body_l = body_text.lower()[:MAX_BODY_SNIFF]
    return cur, title_l, body_l


def _get_main_frame_id() -> str | None:
    """Visszaadja az aktuális tab main frame-jének frameId-ját (CDP Page.getFrameTree)."""
    try:
        ft = driver.execute_cdp_cmd("Page.getFrameTree", {})
        return ft.get("frameTree", {}).get("frame", {}).get("id")
    except Exception:
        return None


def _drain_perf_for_redirects(target_frame_ids: set[str],
                              reqid_to_frame: dict[str, str]) -> dict[str, str]:
    """
    Kiolvassa az azóta érkezett CDP performance logokat, és visszaadja:
      { frameId -> external_location_url }
    Csak a target_frame_ids-ben lévő frame-ekre figyel.
    """
    redirects = {}
    try:
        logs = driver.get_log("performance")
    except Exception:
        logs = []

    for e in logs:
        try:
            msg = json.loads(e.get("message", "")).get("message", {})
        except Exception:
            continue

        m = msg.get("method")
        p = msg.get("params", {}) or {}

        if m == "Network.requestWillBeSent":
            rid = p.get("requestId")
            fid = p.get("frameId")
            if rid and fid:
                reqid_to_frame[rid] = fid

        elif m == "Network.responseReceived":
            rid = p.get("requestId")
            resp = p.get("response", {}) or {}
            status = int(resp.get("status", 0) or 0)
            if status in (301, 302, 303, 307, 308):
                fid = reqid_to_frame.get(rid)
                if fid in target_frame_ids:
                    hdrs = resp.get("headers", {}) or {}
                    loc = hdrs.get("Location") or hdrs.get("location") or hdrs.get("LOCATION")
                    if loc and valid_external(loc):
                        redirects[fid] = _sanitize_url(loc)

        elif m == "Network.responseReceivedExtraInfo":
            rid = p.get("requestId")
            fid = reqid_to_frame.get(rid)
            if fid in target_frame_ids:
                hdrs = p.get("headers", {}) or {}
                loc = hdrs.get("Location") or hdrs.get("location") or hdrs.get("LOCATION")
                if loc and valid_external(loc):
                    redirects[fid] = _sanitize_url(loc)

    return redirects


# ---------- FAST FINAL szabályok (gyors elfogadás) ----------
def _sanitize_url(u: str | None) -> str | None:
    if not u:
        return None
    s = str(u).strip()
    s = re.sub(r'[,\.;\)\s]+$', '', s)
    return s

def _cdp_debug_log_nav_targets(label: str = ""):
    if NAV_DEBUG_INTERVAL <= 0:
        return

    global driver
    if driver is None:
        return

    try:
        info = _safe_cdp_cmd("Target.getTargets", {}, label=f"NAVDBG {label}")
        if not isinstance(info, dict):
            return
    except Exception as e:
        warn(f"[NAVDBG] Target.getTargets hiba: {e}")
        return

    targets = info.get("targetInfos") or []

    rows = []
    for t in targets:
        if t.get("type") != "page":
            continue

        url = t.get("url") or ""
        if not url.startswith("http"):
            continue

        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            host = ""

        # A fő en.surebet.com oldalt NE listázzuk, csak a bookmaker / külső tabokat
        if "surebet.com" in host:
            continue

        rows.append((t.get("targetId"), host, url))

    if not rows:
        log(f"[NAVDBG] {label} – nincs külső 'page' target")
    else:
        log(f"[NAVDBG] {label} – {len(rows)} külső 'page' target:")
        for tid, host, url in rows:
            short = url if len(url) <= 160 else (url[:157] + "...")
            log(f"    - {tid} [{host}] {short}")

def _host(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""

def _hash(u: str) -> str:
    try:
        return urlparse(u).fragment or ""
    except Exception:
        return ""

def _query_params(u: str) -> dict:
    try:
        return parse_qs(urlparse(u).query)
    except Exception:
        return {}

def _blaze_btpath_ok(u: str) -> bool:
    try:
        q = _query_params(u)
        p = q.get('bt-path', [])
        if not p:
            return False
        raw = p[0]
        dec = unquote(raw)
        if 'undefined' in dec.lower():
            return False
        if re.search(r'\d{10,}', dec):
            return True
        if re.search(r'-\d{10,}$', dec):
            return True
        return False
    except Exception:
        return False


# ---------- kisegítő függvények ----------
def human_type(element, text: str):
    try:
        _safe_execute_script("arguments[0].focus();", element)
    except Exception:
        pass
    try:
        element.click()
    except Exception:
        pass
    try:
        element.send_keys(KEY_MOD, "a")
        element.send_keys(Keys.DELETE)
    except Exception:
        try:
            _safe_execute_script("arguments[0].value='';", element)
        except Exception:
            pass

    ok = False
    try:
        driver.execute_cdp_cmd("Input.insertText", {"text": text})
        ok = True
    except Exception:
        ok = False

    if not ok:
        try:
            element.send_keys(text)
            ok = True
        except Exception:
            ok = False

    try:
        cur = _safe_execute_script("return arguments[0].value;", element)
    except Exception:
        cur = None

    if cur != text:
        try:
            _safe_execute_script("""
                const el = arguments[0], val = arguments[1];
                const proto = Object.getPrototypeOf(el) || HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value')
                           || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                if (desc && desc.set) desc.set.call(el, val);
                else el.value = val;
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            """, element, text)
        except Exception:
            pass

    time.sleep(0.05)

def get_bet_name(td):
    try:
        abbr = td.find_element(By.TAG_NAME, "abbr")
        val = abbr.get_attribute("data-bs-original-title") or abbr.get_attribute("title") or abbr.get_attribute("aria-label")
        if val:
            return val.strip()
    except:
        pass
    try:
        return td.text.strip() or "Ismeretlen szelvény"
    except:
        return "Ismeretlen szelvény"

def robust_event_text(tbody, attempts=3, sleep=0.06):
    for _ in range(attempts):
        try:
            els = tbody.find_elements(By.CSS_SELECTOR, "td[class^='event event-']")
            texts = []
            for e in els:
                try:
                    t = (e.text or "").strip()
                except StaleElementReferenceException:
                    t = ""
                except Exception:
                    t = ""
                if t:
                    texts.append(t)
            if texts:
                return max(texts, key=lambda t: len(t.strip())).strip()
            return ""
        except StaleElementReferenceException:
            time.sleep(sleep)
        except Exception:
            break
    return ""

def get_first_minor_text(tbody) -> str:
    try:
        minors = tbody.find_elements(By.CSS_SELECTOR, "span.minor")
        for el in minors:
            txt = (el.text or "").strip()
            if txt:
                return txt
    except Exception:
        pass
    return ""

def parse_float(text):
    if text is None:
        return None
    t = str(text).strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    try:
        return float(m.group(0)) if m else None
    except:
        return None

def to_float_or_none(val):
    v = parse_float(val)
    return v if v is not None else None

def canonical_bookmaker(name: str) -> str:
    if not name:
        return name
    # zárójeles kiegészítések levágása
    base = re.sub(r"\s*\([^)]*\)\s*", "", name).strip()
    base_norm = re.sub(r"\s+", " ", base).strip()

    alias = {
        "Vegas.hu": "Vegas",
        "Vegas": "Vegas",
        "BetInAsia (Black)": "BetInAsia",
        "BetInAsia Black": "BetInAsia",
        "BetInAsia": "BetInAsia",
        "Tippmix Pro": "Tippmixpro",
        "Tippmixpro": "Tippmixpro",
        "Boabet": "Boabet",
        "BetWinner": "Betwinner",
        "Betwinner": "Betwinner",
        "Rockyspin": "RockySpin",
        "RockySpin": "RockySpin",

        # KÉRT MÓDOSÍTÁS: Parimatch → Betmatch
        "Parimatch": "Betmatch",
        "PariMatch": "Betmatch",
        "Pari Match": "Betmatch",
        "PARIMATCH": "Betmatch",
    }
    return alias.get(base_norm, base_norm)

def normalize_match_start(s: str) -> str:
    if not s:
        return s
    s = s.replace(".", "/").strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if m:
        d, mo, hh, mm = map(int, m.groups())
        year = datetime.now().year
        try:
            dt = datetime(year, mo, d, hh, mm)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return s

def compute_profit_percent(odds1, odds2) -> str:
    try:
        o1 = float(odds1); o2 = float(odds2)
        s = 1.0/o1 + 1.0/o2
        val = max(0.0, (1.0 - s) * 100.0)
        return f"{val:.2f}%"
    except:
        return "0.00%"

def find_profit_percent(tbody):
    selectors = [
        "td.profit", "td[class*='profit']", "td.gain", "td.percent", "td.max_profit",
        ".profit", ".gain", ".percent"
    ]
    for sel in selectors:
        try:
            el = tbody.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            m = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", txt)
            if m:
                return m.group(0).replace(",", ".")
        except:
            pass
    try:
        txt = tbody.text
        m = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", txt)
        if m:
            return m.group(0).replace(",", ".")
    except:
        pass
    return None

def norm_odds(val):
    if val is None:
        return None
    try:
        return f"{float(val):.{UPDATE_DECIMALS}f}"
    except:
        v = parse_float(str(val))
        return f"{v:.{UPDATE_DECIMALS}f}" if v is not None else None

def norm_profit_str(s):
    if not s:
        return f"{0.0:.{UPDATE_DECIMALS}f}%"
    try:
        t = str(s).replace(",", ".")
        m = re.search(r"-?\d+(?:\.\d+)?", t)
        v = float(m.group(0)) if m else 0.0
        return f"{v:.{UPDATE_DECIMALS}f}%"
    except:
        return f"{0.0:.{UPDATE_DECIMALS}f}%"

def percent_to_float(s: str | None):
    if not s:
        return None
    try:
        m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", "."))
        return float(m.group(0)) if m else None
    except:
        return None

def iso_or_none(s: str | None):
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        return s
    return None

def log_found_link(name, href, bet, odd):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(FOUND_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {name} -> {href} | {bet} | {odd}\n")

# --- ÚJ: Cím-tisztító csak match/league mezőkre ---
def _clean_title(s: str | None) -> str | None:
    if s is None:
        return None
    t = str(s)
    t = re.sub(r"\[\d+\]", "", t)     # [123456] kidob
    t = t.replace(".", "")            # pontok törlése
    t = re.sub(r"\s+", " ", t).strip(" -—–\u2013\u2014").strip()
    return t or None

# --- HTTP helpers ---
def http_post(url: str, payload: dict, timeout=12) -> tuple[int, dict]:
    """
    Részletes JSON visszaadása + saját X-Correlation-Id header.
    A válaszban: {"message", "issues", "correlation_id", "__status__", ...}
    """
    try:
        corr_id = str(uuid.uuid4())
        headers = dict(HTTP_HEADERS)
        headers["X-Correlation-Id"] = corr_id

        r = requests.post(url, headers=headers, json=payload, timeout=timeout)

        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}

        data.setdefault("correlation_id",
                        r.headers.get("x-correlation-id") or data.get("correlation_id") or corr_id)
        data["__status__"] = r.status_code

        if DEBUG_HTTP:
            print(f"HTTP {r.status_code} {url} cid={data.get('correlation_id')}")
            try:
                print(json.dumps(data, ensure_ascii=False)[:1200])
            except Exception:
                print(str(data)[:1200])

        return r.status_code, data
    except Exception as e:
        return 0, {"error": "request_exception", "message": str(e)}

# ===================== ASZINKRON DISZPÉCSER + BATCH =====================
class AsyncHttpDispatcher:
    def __init__(self):
        self.q_save   = Queue(maxsize=10000)
        self.q_update = Queue(maxsize=10000)
        self.q_delete = Queue(maxsize=10000)
        self.result_q = Queue(maxsize=10000)

        self.UPDATE_BATCH_MAX = 50
        self.UPDATE_BATCH_FLUSH_SEC = 1.2

        self.DELETE_BATCH_MAX = 50
        self.DELETE_BATCH_FLUSH_SEC = 1.5

        self.HTTP_TIMEOUT = 12

        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()

    def stop(self):
        self._stop.set()
        self._thr.join(timeout=5)

    def get_results(self, max_items=200):
        items = []
        for _ in range(max_items):
            try:
                items.append(self.result_q.get_nowait())
            except Empty:
                break
        return items

    def enqueue_save(self, item: dict):
        try:
            self.q_save.put_nowait(item)
        except Exception:
            warn("⚠️ SAVE queue full, item dropped")

    def enqueue_update(self, payload: dict):
        try:
            self.q_update.put_nowait(payload)
        except Exception:
            warn("⚠️ UPDATE queue full, item dropped")

    def enqueue_delete(self, tip_id: str):
        try:
            self.q_delete.put_nowait(tip_id)
        except Exception:
            warn("⚠️ DELETE queue full, item dropped")

    def _run(self):
        upd_bucket = []
        upd_first_ts = None

        del_bucket = []
        del_first_ts = None

        while not self._stop.is_set():
            did_anything = False

            try:
                save_item = self.q_save.get_nowait()
                did_anything = True
                self._process_save_item(save_item)
            except Empty:
                pass

            now = time.time()
            try:
                while True:
                    upd_item = self.q_update.get_nowait()
                    upd_bucket.append(upd_item)
                    if upd_first_ts is None:
                        upd_first_ts = now
                    if len(upd_bucket) >= self.UPDATE_BATCH_MAX:
                        break
            except Empty:
                pass

            if upd_bucket:
                if (time.time() - (upd_first_ts or time.time())) >= self.UPDATE_BATCH_FLUSH_SEC or len(upd_bucket) >= self.UPDATE_BATCH_MAX:
                    self._flush_update_batch(upd_bucket)
                    upd_bucket = []
                    upd_first_ts = None
                    did_anything = True

            now = time.time()
            try:
                while True:
                    del_item = self.q_delete.get_nowait()
                    del_bucket.append(del_item)
                    if del_first_ts is None:
                        del_first_ts = now
                    if len(del_bucket) >= self.DELETE_BATCH_MAX:
                        break
            except Empty:
                pass

            if del_bucket:
                if (time.time() - (del_first_ts or time.time())) >= self.DELETE_BATCH_FLUSH_SEC or len(del_bucket) >= self.DELETE_BATCH_MAX:
                    self._flush_delete_batch(del_bucket)
                    del_bucket = []
                    del_first_ts = None
                    did_anything = True

            if not did_anything:
                time.sleep(0.02)

        if upd_bucket:
            self._flush_update_batch(upd_bucket)
        if del_bucket:
            self._flush_delete_batch(del_bucket)

        try:
            while True:
                save_item = self.q_save.get_nowait()
                self._process_save_item(save_item)
        except Empty:
            pass

    def _process_save_item(self, it: dict):
        tip_payload = it.get("tip_payload", {})
        status, data = http_post(SAVE_TIP_URL, tip_payload, timeout=self.HTTP_TIMEOUT)

        # siker csak akkor, ha 2xx ÉS ok:true
        ok = (200 <= status < 300) and isinstance(data, dict) and (data.get("ok") is True)

        if ok:
            self.result_q.put({
                "type": "save_ok",
                "id": tip_payload.get("id"),
                "state_info": it.get("state_info"),
                "finals": it.get("finals"),
                "resp": data,
            })
            return

        # duplikáció kezelése
        low = json.dumps(data, ensure_ascii=False).lower() if isinstance(data, dict) else str(data).lower()
        if status == 409 or any(k in low for k in ["duplicate", "unique", "already exists", "conflict"]):
            upd_payload = it.get("update_payload")
            if upd_payload:
                s2, d2 = http_post(UPDATE_TIP_URL, upd_payload, timeout=self.HTTP_TIMEOUT)
                if (200 <= s2 < 300) and isinstance(d2, dict) and d2.get("ok") is True:
                    self.result_q.put({
                        "type": "save_dup_updated",
                        "id": tip_payload.get("id"),
                        "state_info": it.get("state_info"),
                        "finals": it.get("finals"),
                        "update_payload": upd_payload,
                        "resp": d2,
                    })
                    return
                else:
                    self.result_q.put({
                        "type": "save_dup_update_fail",
                        "id": tip_payload.get("id"),
                        "status": s2,
                        "error": d2,
                    })
                    return
            else:
                self.result_q.put({
                    "type": "save_duplicate",
                    "id": tip_payload.get("id"),
                    "state_info": it.get("state_info"),
                    "finals": it.get("finals"),
                    "resp": data,
                })
                return

        # minden más hiba
        self.result_q.put({
            "type": "save_error",
            "id": tip_payload.get("id"),
            "status": status,
            "error": data
        })

    def _flush_update_batch(self, items: list[dict]):
        try:
            payload = {"items": items}
            status, data = http_post(UPDATE_TIPS_BATCH_URL, payload, timeout=self.HTTP_TIMEOUT)
            if (200 <= status < 300) and isinstance(data, dict) and data.get("ok") is True:
                for it in items:
                    self.result_q.put({"type": "update_ok", "id": it.get("id"), "payload": it, "resp": data})
                return
        except Exception:
            pass
        for it in items:
            s, d = http_post(UPDATE_TIP_URL, it, timeout=self.HTTP_TIMEOUT)
            if (200 <= s < 300) and isinstance(d, dict) and d.get("ok") is True:
                self.result_q.put({"type": "update_ok", "id": it.get("id"), "payload": it, "resp": d})
            else:
                self.result_q.put({"type": "update_error", "id": it.get("id"), "status": s, "error": d, "payload": it})

    def _flush_delete_batch(self, ids: list[str]):
        uniq_ids = list(dict.fromkeys(ids))
        try:
            payload = {"ids": uniq_ids}
            status, data = http_post(DELETE_TIPS_BATCH_URL, payload, timeout=self.HTTP_TIMEOUT)
            if (200 <= status < 300) and isinstance(data, dict) and data.get("ok") is True:
                for tid in uniq_ids:
                    self.result_q.put({"type": "delete_ok", "id": tid, "resp": data})
                return
        except Exception:
            pass
        for tid in uniq_ids:
            s, d = http_post(DELETE_TIP_URL, {"type": "gone", "id": tid}, timeout=self.HTTP_TIMEOUT)
            if (200 <= s < 300) and isinstance(d, dict) and d.get("ok") is True:
                self.result_q.put({"type": "delete_ok", "id": tid, "resp": d})
            else:
                self.result_q.put({"type": "delete_error", "id": tid, "status": s, "error": d})

dispatcher = AsyncHttpDispatcher()

# ====== GLOBÁLIS NYITÁSI VÁRÓLISTA (lookahead a 3-as csomagokhoz) ======
OPEN_TASKS = deque()
OPEN_TASKS_MAX = 5000

def enqueue_open_task(task: dict):
    """Feladat (tbody-id) nyitásának előkészítése lookahead-dal.
       Csak akkor tesszük be, ha még nincs link-final megoldva azonnal."""
    try:
        if len(OPEN_TASKS) < OPEN_TASKS_MAX:
            OPEN_TASKS.append(task)
        else:
            warn("⚠️ OPEN_TASKS megtelt, dobom a legrégebbit")
            OPEN_TASKS.popleft()
            OPEN_TASKS.append(task)
    except Exception as e:
        warn(f"⚠️ enqueue_open_task hiba: {e}")

# ---------- stale-biztos DOM snapshot ----------
def dom_snapshot_by_id(tbody_id: str, attempts=4, sleep=0.08):
    js = r"""
    const id = arguments[0];
    function snap(id){
      const sel1 = 'tbody.surebet_record[data-id="'+id+'"]';
      const sel2 = 'tbody.surebet_record[dataid="'+id+'"]';
      const row = document.querySelector(sel1) || document.querySelector(sel2);
      if (!row) return null;

      const getTxt = el => el ? (el.textContent || '').trim() : '';
      const q = (r, s) => r ? r.querySelector(s) : null;
      const qa = (r, s) => r ? Array.from(r.querySelectorAll(s)) : [];

      const values = qa(row, "td.value[class*='odd_record_']");
      const coeffs = qa(row, "td.coeff");

      const a1 = values[0] ? q(values[0], 'a') : null;
      const a2 = values[1] ? q(values[1], 'a') : null;

      const href1 = a1 ? a1.href : null;
      const href2 = a2 ? a2.href : null;

      const odds1_text = getTxt(values[0] || null);
      const odds2_text = getTxt(values[1] || null);

      const getBet = (cell) => {
        const ab = cell ? (cell.querySelector('abbr,[data-bs-original-title],[title],[aria-label]')) : null;
        const cands = [
          ab ? (ab.getAttribute('data-bs-original-title')||'') : '',
          ab ? (ab.getAttribute('title')||'') : '',
          ab ? (ab.getAttribute('aria-label')||'') : '',
          getTxt(cell || null)
        ].map(s => (s||'').trim()).filter(Boolean);
        return cands[0] || 'Ismeretlen szelvény';
      };

      const bet1 = getBet(coeffs[0] || null);
      const bet2 = getBet(coeffs[1] || null);

      // Bookmaker nevek (max 2)
      const bookers = qa(row, "td.booker a").map(a => getTxt(a)).filter(Boolean).slice(0,2);

      // EVENT cellák
      const evTds = qa(row, "td[class^='event event-']");
      const evAnchors = evTds
        .map(td => q(td, "a[target='_blank']"))
        .filter(Boolean)
        .map(a => getTxt(a))
        .filter(Boolean);

      // Két anchor is lehet – a rövidebbik kell
      let event_anchor_text = "";
      if (evAnchors.length === 1) {
        event_anchor_text = evAnchors[0];
      } else if (evAnchors.length >= 2) {
        event_anchor_text = evAnchors.sort((a,b) => a.length - b.length)[0];
      }

      // League a td.event... alatti span.minor-ból, ha kettő van -> rövidebbik kell
      const evMinors = evTds
        .map(td => getTxt(q(td, 'span.minor')))
        .filter(Boolean);

      let league_minor = "";
      if (evMinors.length === 1) {
        league_minor = evMinors[0];
      } else if (evMinors.length >= 2) {
        league_minor = evMinors.sort((a,b) => a.length - b.length)[0];
      }

      // sport_minor: meghagyjuk, ha kell később
      const minorsAll = qa(row, "span.minor").map(el => getTxt(el)).filter(Boolean);
      const sport_minor = minorsAll.length ? minorsAll[0] : "";

      // Profit szöveg
      const sels = ['td.profit','td[class*="profit"]','td.gain','td.percent','td.max_profit','.profit','.gain','.percent'];
      let profit = '';
      for (const s of sels) {
         const el = q(row, s);
         const t = getTxt(el);
         if (t) { profit = t; break; }
      }

      // Kezdési idő HTML (abbr)
      const timeabbr = q(row, "td.time abbr");
      const time_html = timeabbr ? (timeabbr.innerHTML || "") : "";

      return {
        href1, href2,
        odds1_text, odds2_text,
        bet1, bet2,
        bookers,
        league_minor,
        sport_minor,
        time_html,
        profit_text: profit,
        event_anchor_text
      };
    }
    return snap(arguments[0]);
    """
    for _ in range(attempts):
        try:
            data = driver.execute_script(js, tbody_id)
            if data:
                return data
        except StaleElementReferenceException:
            pass
        except Exception:
            pass
        time.sleep(sleep)
    return None

# ---------- stale-biztos SAVE előkészítés + batch ----------
def prepare_new_task_for_id(tbody_id):
    snap = dom_snapshot_by_id(tbody_id)
    if not snap:
        return None

    href1 = snap.get("href1")
    href2 = snap.get("href2")

    # match_name az anchor rövidebbik változata
    match_name_raw = (snap.get("event_anchor_text") or "").strip()
    match_name = _clean_title(match_name_raw) or "Ismeretlen meccs"

    # league_name a td.event alatti rövidebbik span.minor
    league_minor = (snap.get("league_minor") or "").strip()
    league_name = _clean_title(league_minor) or ""

    sport_name = (snap.get("sport_minor") or "").strip()

    names_raw = (snap.get("bookers") or [])[:2]
    if len(names_raw) < 2:
        return None
    names = [canonical_bookmaker(n) for n in names_raw]

    odds1_text = (snap.get("odds1_text") or "").strip()
    odds2_text = (snap.get("odds2_text") or "").strip()
    odds1 = to_float_or_none(odds1_text)
    odds2 = to_float_or_none(odds2_text)

    bet1 = (snap.get("bet1") or "").strip() or "Ismeretlen szelvény"
    bet2 = (snap.get("bet2") or "").strip() or "Ismeretlen szelvény"

    profit_dom = (snap.get("profit_text") or "").strip()
    if profit_dom:
        profit_percent = profit_dom
    else:
        if odds1 is not None and odds2 is not None:
            profit_percent = compute_profit_percent(odds1, odds2)
        else:
            profit_percent = "0.00%"

    time_html = snap.get("time_html") or ""
    parts = [p.strip() for p in time_html.replace("<br>", "\n").split("\n") if p.strip()]
    if len(parts) >= 2:
        match_start_raw = f"{parts[0]} {parts[1]}"
    else:
        match_start_raw = (time_html.strip() or "Ismeretlen időpont")
    match_start = normalize_match_start(match_start_raw)
    profit_text = norm_profit_str(profit_percent)

    task = {
        "id": tbody_id,
        "names": names,
        "bets": (bet1, bet2),
        "odds": (odds1 if odds1 is not None else to_float_or_none(odds1_text),
                 odds2 if odds2 is not None else to_float_or_none(odds2_text)),
        "profit_text": profit_text,
        "match_name": match_name,
        "league_name": league_name,
        "sport_name": sport_name,
        "match_start_iso": iso_or_none(match_start),
        "hrefs": (href1, href2),
        "finals": None,
    }

    if tbody_id in link_cache:
        l1 = link_cache[tbody_id].get("link1")
        l2 = link_cache[tbody_id].get("link2")
        if valid_external(l1) and valid_external(l2):
            task["finals"] = (l1, l2)

    return task

def _build_tip_payload_from_task(task):
    tbody_id = task["id"]
    names = task["names"]
    bet1, bet2 = task["bets"]
    odds1, odds2 = task["odds"]
    profit_text = task["profit_text"]

    # Tisztítás csak a cím mezőknél
    match_name = _clean_title(task["match_name"])
    league_name = _clean_title(task["league_name"])

    sport_name = task["sport_name"]
    match_start_iso = task["match_start_iso"]
    final_href1, final_href2 = task.get("finals") or (None, None)

    tip_payload = {
        "id": tbody_id,
        "bookmaker1": names[0],
        "bookmaker2": names[1],
        "profit_percent": profit_text,
        "profit_percent_num": percent_to_float(profit_text),
        "match_name": match_name,
        "league_name": league_name,
        "option1": bet1,
        "option2": bet2,
        "match_start": match_start_iso,
        "link1": final_href1,
        "link2": final_href2,
        "odds1": odds1,
        "odds2": odds2,
        "sport": sport_name,
    }
    return tip_payload

def _build_update_payload_from_task(task):
    tbody_id = task["id"]
    odds1, odds2 = task["odds"]
    profit_text = task["profit_text"]
    return {
        "type": "update",
        "id": tbody_id,
        "odds1": norm_odds(odds1),
        "odds2": norm_odds(odds2),
        "profit_percent": norm_profit_str(profit_text),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# ---------- NAV backoff ----------
nav_retry_attempts = {}   # id -> int
nav_retry_until    = {}   # id -> epoch

nav_backoff_consecutive = 0

def force_main_refresh(reason: str = ""):
    """MAIN tab kemény frissítés + autoupdate biztosítása."""
    global main_refresh_enabled, main_last_refresh, main_next_refresh
    try:
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)
        driver.refresh()
        _inject_disable_animations()
        _wait_main_container(timeout=12)
        ensure_main_autoupdate()
        log(f"🔁 MAIN forced refresh {f'({reason})' if reason else ''}")
    except Exception as e:
        warn(f"⚠️ MAIN forced refresh failed: {e}")

def _schedule_nav_backoff(tid: str):
    global nav_backoff_consecutive
    att = nav_retry_attempts.get(tid, 0) + 1
    nav_retry_attempts[tid] = att
    delay = min(NAV_RETRY_BASE * (2 ** (att - 1)), NAV_RETRY_MAX)
    nav_retry_until[tid] = time.time() + delay

    nav_backoff_consecutive += 1
    warn(f"⏳ NAV backoff id={tid} {int(delay)}s (attempt={att})")

    if nav_backoff_consecutive >= 10:
        force_main_refresh("10 consecutive NAV backoffs")
        nav_backoff_consecutive = 0

def _clear_nav_backoff(tid: str):
    global nav_backoff_consecutive
    nav_retry_attempts.pop(tid, None)
    nav_retry_until.pop(tid, None)
    nav_backoff_consecutive = 0

def _open_window_tagged(url: str, tag: str, delay_ms: int):
    js = f"""
        (function(){{
            var go = function(u,t,delay){{
                var w = window.open('about:blank','_blank');
                if (!w) return;
                try {{ w.name = t; }} catch(e) {{}}
                setTimeout(function() {{
                    try {{ w.location.href = u; }} catch(e) {{}}
                }}, delay);
            }};
            go({json.dumps(url)}, {json.dumps(tag)}, {int(delay_ms)});
        }})();
    """
    _safe_execute_script(js)


def _finalize_url_for_handle_fast(handle, opened_at_ts):
    """
    EGYSZERŰ LOGIKA:

    - Ha a tab már nem él: (None, "timeout")
    - Beolvassuk a current_url-t
    - Ha http(s) ÉS NEM surebet.com host → elfogadjuk: ("ok")
    - Minden más (surebet.com, /nav, 404-es surebet oldal, stb.) → (None, "timeout")
      → ezzel NAV backoff lesz, ahogy szeretnéd.
    """
    try:
        if handle not in driver.window_handles:
            return (None, "timeout")
        driver.switch_to.window(handle)
        cur = driver.current_url or ""
    except Exception:
        return (None, "timeout")

    # Külső host? Akkor jó.
    if valid_external(cur):
        return (_sanitize_url(cur), "ok")

    # Minden surebet.com (beleértve /nav + 404) → timeout
    return (None, "timeout")


def _finalize_url_for_handle(handle, opened_at_ts) -> str | None:
    final, _state = _finalize_url_for_handle_fast(handle, opened_at_ts)
    return final

def resolve_pairs_round_robin(pairs) -> tuple[list[tuple[str | None, str | None]], list[tuple[str, str]]]:
    """
    Streaming CDP-s feloldás, most már extra védelemmel.
    """
    global driver, DRIVER_DEAD

    if DRIVER_DEAD:
        warn("[RR] DRIVER_DEAD=True, round-robin resolver skip – minden pár timeout.")
        num_pairs = len(pairs)
        return ([(None, None) for _ in range(num_pairs)],
                [("timeout", "timeout") for _ in range(num_pairs)])

    t0 = time.time()
    num_pairs = len(pairs)
    if num_pairs == 0:
        return [], []


    # normalizált lista: vagy None, vagy (href1, href2)
    norm: list[tuple[str, str] | None] = []
    for p in pairs:
        if p and p[0] and p[1]:
            norm.append((p[0], p[1]))
        else:
            norm.append(None)

    finals_by_pair: list[tuple[str | None, str | None]] = [(None, None) for _ in range(num_pairs)]
    states_by_pair: list[tuple[str, str]] = [("timeout", "timeout") for _ in range(num_pairs)]
    done_pairs = [False] * num_pairs

    # Ha nincs driver vagy nincs window, akkor itt FEJELÜNK KI szépen
    if driver is None:
        warn("[RR] driver is None, minden pár timeout → NAV backoff.")
        return finals_by_pair, states_by_pair

    try:
        if not driver.window_handles:
            warn("[RR] nincs élő Chrome window, round-robin skip → NAV backoff.")
            return finals_by_pair, states_by_pair
    except Exception as e:
        warn(f"[RR] window_handles hiba (round-robin start): {e} → NAV backoff.")
        return finals_by_pair, states_by_pair

    # targetId → {pair_index, pos(1/2)}
    tracking: dict[str, dict] = {}
    num_pairs_to_open = 0

    # 1) Targetek létrehozása (CDP-safe)
    for idx, p in enumerate(norm):
        if p is None:
            continue
        href1, href2 = p
        created_any = False

        # első oldal
        res1 = _safe_cdp_cmd(
            "Target.createTarget",
            {"url": href1, "background": True},
            label=f"RR href1 idx={idx}",
        )
        tid1 = res1.get("targetId") if isinstance(res1, dict) else None
        if tid1:
            tracking[tid1] = {"pair": idx, "pos": 1}
            created_any = True
        else:
            warn(f"[RR] Target.createTarget sikertelen (href1) idx={idx}")

        # második oldal
        res2 = _safe_cdp_cmd(
            "Target.createTarget",
            {"url": href2, "background": True},
            label=f"RR href2 idx={idx}",
        )
        tid2 = res2.get("targetId") if isinstance(res2, dict) else None
        if tid2:
            tracking[tid2] = {"pair": idx, "pos": 2}
            created_any = True
        else:
            warn(f"[RR] Target.createTarget sikertelen (href2) idx={idx}")

        if created_any:
            num_pairs_to_open += 1

    if not tracking:
        log("resolve_pairs_round_robin: nincs nyitható target (tracking üres / CDP skip)")
        return finals_by_pair, states_by_pair

    open_elapsed = time.time() - t0
    deadline = time.time() + (PAIR_TIMEOUT_SEC or 0.0)
    last_dbg = 0.0

    # 2) Polling CDP-vel (biztonságosan)
    while tracking and time.time() < deadline:
        info = _safe_cdp_cmd("Target.getTargets", {}, label="RR getTargets")
        if not isinstance(info, dict):
            # tipikusan akkor jön ide, ha idő közben bezárult a window / devtools
            warn("[RR] Target.getTargets → None (valószínűleg bezárult a window) → kilépés a round-robinből.")
            break

        targets = info.get("targetInfos", []) or []

        for t in targets:
            try:
                tid = t.get("targetId")
            except Exception:
                continue
            if tid not in tracking:
                continue

            url = (t.get("url") or "").strip()
            if not url:
                continue

            # csak akkor tekintjük késznek, ha már elhagyta a surebet.com-ot
            if not valid_external(url):
                continue

            entry = tracking.get(tid)
            if not entry:
                continue

            pair_idx = entry["pair"]
            pos = entry["pos"]

            if done_pairs[pair_idx]:
                # ezt a párt már lezártuk; a maradék targetet is bezárhatjuk
                _safe_cdp_cmd("Target.closeTarget", {"targetId": tid}, label="RR closeTarget (pair already done)")
                tracking.pop(tid, None)
                continue

            clean = _sanitize_url(url)
            f1, f2 = finals_by_pair[pair_idx]
            if pos == 1:
                f1 = clean
            else:
                f2 = clean
            finals_by_pair[pair_idx] = (f1, f2)

            # ha mindkét oldal megvan → pár kész, targetek bezárása
            if f1 and f2:
                states_by_pair[pair_idx] = ("ok", "ok")
                done_pairs[pair_idx] = True

                # csukjuk be a párhoz tartozó összes targetet
                to_close = [tid2 for tid2, info2 in tracking.items() if info2["pair"] == pair_idx]
                for tid2 in to_close:
                    _safe_cdp_cmd("Target.closeTarget", {"targetId": tid2}, label="RR closeTarget (pair done)")
                    tracking.pop(tid2, None)

                if LOG_PAIR_DONE:
                    log(f"[RR] ✓ Pár kész (idx={pair_idx}) f1={f1} f2={f2}")

        # debug log 2 mp-enként (ha engedélyezve)
        if NAV_DEBUG_INTERVAL > 0 and (time.time() - last_dbg) >= NAV_DEBUG_INTERVAL:
            _cdp_debug_log_nav_targets("RR streaming poll")
            last_dbg = time.time()

        time.sleep(CDP_POLL_INTERVAL)

    # 3) Timeout után: minden maradék target bezárása (safe CDP)
    for tid in list(tracking.keys()):
        _safe_cdp_cmd("Target.closeTarget", {"targetId": tid}, label="RR closeTarget (timeout)")
        tracking.pop(tid, None)

    total = time.time() - t0
    log(
        f"resolve_pairs_round_robin(streaming): {num_pairs_to_open} pár, "
        f"open={open_elapsed:.3f}s, total={total:.3f}s, timeout={PAIR_TIMEOUT_SEC:.1f}s"
    )

    return finals_by_pair, states_by_pair


def resolve_two_final_urls_rr(href1, href2):
    """Helper: egy darab pár feloldása az új CDP-s round-robin resolverrel."""
    finals, states = resolve_pairs_round_robin([(href1, href2)])
    return finals[0], states[0]



def resolve_pairs_staggered(pairs, timeout=RESOLVE_TIMEOUT, stable_period=RESOLVE_STABLE_PERIOD, poll_interval=RESOLVE_POLL_INTERVAL):
    """
    NAV-only: nincs előzetes 'fast' ellenőrzés, nincs regex.
    Egyszerűen megnyitjuk a párokat, és mindkét tabnál azt figyeljük,
    mikor hagyja el a surebet.com-ot — akkor elfogadjuk az aktuális URL-t.
    """
    # Normalizáljuk: csak (href1, href2) tuple vagy None
    norm = []
    for p in pairs:
        if p and p[0] and p[1]:
            norm.append((p[0], p[1]))
        else:
            norm.append(None)

    def _guid():
        return f"{int(time.time()*1000)}{random.randint(100,999)}"

    need_open = []
    taginfo = []
    for i, p in enumerate(norm):
        if p is None:
            need_open.append(False)
            taginfo.append({"pair_index": i, "tag1": None, "tag2": None})
        else:
            need_open.append(True)
            tag1 = f"SB|{_guid()}|1|{i}"
            tag2 = f"SB|{_guid()}|2|{i}"
            taginfo.append({"pair_index": i, "tag1": tag1, "tag2": tag2})

    created = []
    prev = set()
    try:
        ensure_active_window()
        prev = set(driver.window_handles)
        now_ts = time.time()

        # 1. pár
        if len(norm) >= 1 and need_open[0]:
            base = 0
            _open_window_tagged(norm[0][0], taginfo[0]["tag1"], base + 0)
            _open_window_tagged(norm[0][1], taginfo[0]["tag2"], base + OPEN_WITHIN_PAIR_MS)

        # 2. pár
        if len(norm) >= 2 and need_open[1]:
            base = OPEN_PAIR_STAGGER_MS_BASE * 1  # 200ms
            _open_window_tagged(norm[1][0], taginfo[1]["tag1"], base + 0)
            _open_window_tagged(norm[1][1], taginfo[1]["tag2"], base + OPEN_WITHIN_PAIR_MS)

        # 3. pár
        if len(norm) >= 3 and need_open[2]:
            base = OPEN_PAIR_STAGGER_MS_BASE * 2  # 400ms
            _open_window_tagged(norm[2][0], taginfo[2]["tag1"], base + 0)
            _open_window_tagged(norm[2][1], taginfo[2]["tag2"], base + OPEN_WITHIN_PAIR_MS)

        target_count = 0
        target_count += 2 if (len(norm) >= 1 and need_open[0]) else 0
        target_count += 2 if (len(norm) >= 2 and need_open[1]) else 0
        target_count += 2 if (len(norm) >= 3 and need_open[2]) else 0

        deadline = time.time() + HANDLE_WAIT_TIMEOUT
        created = []
        while time.time() < deadline:
            try:
                now_handles = driver.window_handles
            except Exception:
                now_handles = []
            newh = list(set(now_handles) - prev)
            if newh:
                created = newh  # ami megvan, AZONNAL dolgozzuk fel
                break
# nincs sleep


        # tag -> handle mapping
        handle_tag = {}
        for h in created:
            try:
                if h not in driver.window_handles:
                    continue
                driver.switch_to.window(h)
                name = _safe_execute_script("return window.name || ''") or ""
            except Exception:
                name = ""
            handle_tag[h] = name

        finals_by_pair = {i: [None, None] for i in range(len(norm))}

        # NAV-only feloldás
        for h in created:
            tag = handle_tag.get(h, "")
            final = _finalize_url_for_handle(h, now_ts)
            m = re.match(r'^SB\|.+\|(1|2)\|(\d+)$', tag)
            if m:
                pos = int(m.group(1))  # 1 vagy 2
                pidx = int(m.group(2))
                if 0 <= pidx < len(norm):
                    finals_by_pair[pidx][pos-1] = final

        out = []
        for i in range(len(norm)):
            out.append(tuple(finals_by_pair[i]))
        return out

    finally:
        try:
            cur = set(driver.window_handles)
            created_list = list(cur - prev) if prev else []
        except Exception:
            created_list = []
        for h in created_list:
            try:
                if h in driver.window_handles:
                    driver.switch_to.window(h)
                    driver.close()
            except Exception:
                pass
        try:
            if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
                driver.switch_to.window(MAIN_HANDLE)
        except Exception:
            pass


def resolve_two_final_urls(href1, href2,
                           timeout=RESOLVE_TIMEOUT,
                           stable_period=RESOLVE_STABLE_PERIOD,
                           poll_interval=RESOLVE_POLL_INTERVAL):
    """
    NAV-only, 2 ablakos: nincs fast/regex; amint elhagyja a surebet-et, elfogadjuk az URL-t.
    """
    if not (href1 and href2):
        return (None, None)

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    tag1 = f"SB|{int(time.time()*1000)}{random.randint(100,999)}|1|0"
    tag2 = f"SB|{int(time.time()*1000)}{random.randint(100,999)}|2|0"

    prev = set()
    created = []
    try:
        ensure_active_window()
        prev = set(driver.window_handles)
        now_ts = time.time()

        _open_window_tagged(href1, tag1, 0)
        _open_window_tagged(href2, tag2, OPEN_WITHIN_PAIR_MS)

        target_count = 2
        deadline = time.time() + HANDLE_WAIT_TIMEOUT
        created = []
        while time.time() < deadline:
            try:
                now_handles = driver.window_handles
            except Exception:
                now_handles = []
            newh = list(set(now_handles) - prev)
            if newh:
                created = newh
                break
# nincs sleep


        final1 = None
        final2 = None
        for h in created:
            try:
                if h not in driver.window_handles:
                    continue
                driver.switch_to.window(h)
                name = _safe_execute_script("return window.name || ''") or ""
            except Exception:
                name = ""
            final = _finalize_url_for_handle(h, now_ts)
            if name.startswith("SB") and "|1|" in name:
                final1 = final
            elif name.startswith("SB") and "|2|" in name:
                final2 = final

        return (final1, final2)

    finally:
        try:
            cur = set(driver.window_handles)
            created_list = list(cur - prev) if prev else []
        except Exception:
            created_list = []
        for h in created_list:
            try:
                if h in driver.window_handles:
                    driver.switch_to.window(h)
                    driver.close()
            except Exception:
                pass
        try:
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
        except Exception:
            pass


def background_nav_worker():
    """
    NAV-only: OPEN_TASKS folyamatos feldolgozása háttérben.
    """
    global link_cache, DRIVER_DEAD

    while True:
        # ha a driver halott, itt is lépjünk ki
        if DRIVER_DEAD:
            warn("💀 NAV worker leáll – DRIVER_DEAD=True.")
            break

        try:
            # nincs feladat → pici alvás, hogy ne pörögjön szét a CPU
            if not OPEN_TASKS:
                time.sleep(0.05)
                continue


            # 1) vegyünk ki max NAV_WORKER_MAX_PAIRS feladatot
            todo = []
            while OPEN_TASKS and len(todo) < NAV_WORKER_MAX_PAIRS:
                todo.append(OPEN_TASKS.popleft())

            if not todo:
                continue

            # 2) Pár-lista a resolverhez
            pairs = []
            for t in todo:
                h1, h2 = t.get("hrefs") or (None, None)
                pairs.append((h1, h2) if (h1 and h2) else None)

            any_to_open = any(p is not None for p in pairs)
            if any_to_open:
                finals, states = resolve_pairs_round_robin(pairs)
            else:
                finals = [(None, None)] * len(pairs)
                states = [("timeout", "timeout")] * len(pairs)

            # 3) Eredmények feldolgozása
            for idx, task in enumerate(todo):
                tbody_id = task["id"]

                if pairs[idx] is None:
                    # fallback: ha a pár None volt, de a taskban van két href, próbáljuk külön
                    h1, h2 = task.get("hrefs") or (None, None)
                    if h1 and h2:
                        (f1, f2), (s1, s2) = resolve_two_final_urls_rr(h1, h2)
                    else:
                        f1, f2 = h1, h2
                        s1, s2 = ("timeout", "timeout")
                else:
                    (f1, f2) = finals[idx]
                    (s1, s2) = states[idx]

                task["finals"] = (f1, f2)

                ok = valid_external(f1) and valid_external(f2)
                if ok:
                    tip_payload = _build_tip_payload_from_task(task)
                    update_payload = _build_update_payload_from_task(task)
                    dispatcher.enqueue_save({
                        "id": tbody_id,
                        "tip_payload": tip_payload,
                        "update_payload": update_payload,
                        "state_info": {
                            "odds1": tip_payload["odds1"],
                            "odds2": tip_payload["odds2"],
                            "profit_percent": tip_payload["profit_percent"],
                        },
                        "finals": task["finals"],
                    })
                    link_cache[tbody_id] = {
                        "link1": f1,
                        "link2": f2,
                        "saved_at": datetime.now().isoformat()
                    }
                    _clear_nav_backoff(tbody_id)
                else:
                    # minden nem 'ok' (beleértve a timeout-ot) → NAV backoff
                    if 'not_found' in (s1, s2):
                        warn(f"🔎 Page not found → NAV backoff: {tbody_id} (s1={s1}, s2={s2})")
                    _schedule_nav_backoff(tbody_id)

            save_link_cache(link_cache)

        except Exception as e:
            warn(f"[NAV-WORKER] Hiba a háttér workerben: {e}")
            time.sleep(1.0)


def batch_save_new_ids(new_ids: list, higher_ids: set | None = None):
    """
    NAV-only:
    - Ha a cache-ben már megvan mindkét külső végső link (task['finals']),
      azonnal SAVE.
    - Különben betesszük a globális OPEN_TASKS várólistába, és
      a fő while-loop végén hívott process_open_tasks() nyitja meg /nav-on át.
    """
    # 🔒 BOOTSTRAP alatt (első 50 mp) nem indítunk új SAVE/NAV feloldást,
    # csak gyűjtjük az ID-ket és nyitjuk a tabokat.
    if in_bootstrap_phase():
        return

    if not new_ids:
        return

    now = time.time()
    tasks = []
    for tid in new_ids:
        # ha láttuk már (file/os), nem új
        if tid in seen:
            continue
        # ha például NEXT/GROUP-ban magasabb prioritású halmazban van, ugorjuk
        if higher_ids and tid in higher_ids:
            continue
        # NAV-backoff: ha várunk még, most ne próbálkozzunk vele
        until = nav_retry_until.get(tid, 0)
        if until and now < until:
            continue

        t = prepare_new_task_for_id(tid)
        if t:
            tasks.append(t)

    if not tasks:
        return

    # 1) Azonnal menthetőek (ha cache-ből már megvan mindkét külső link)
    for t in tasks:
        finals = t.get("finals") or (None, None)
        f1, f2 = finals
        if valid_external(f1) and valid_external(f2):
            tip_payload = _build_tip_payload_from_task(t)
            update_payload = _build_update_payload_from_task(t)
            dispatcher.enqueue_save({
                "id": t["id"],
                "tip_payload": tip_payload,
                "update_payload": update_payload,
                "state_info": {
                    "odds1": tip_payload["odds1"],
                    "odds2": tip_payload["odds2"],
                    "profit_percent": tip_payload["profit_percent"],
                },
                "finals": finals,
            })
            _clear_nav_backoff(t["id"])
        else:
            # 2) Feloldásra várók → globális várólista
            enqueue_open_task(t)

    # FONTOS: itt már NEM hívunk process_open_tasks()-t,
    # hogy egy while-loop iterációban csak EGYSZER fusson NAV-feloldás
    # (a fő ciklus végén: process_open_tasks(max_pairs=6)).

# ---------- UPDATE PATH ----------
def snapshot_update_values_by_id(tbody_id: str):
    js = r"""
    const id = arguments[0];
    const sel1 = 'tbody.surebet_record[data-id="'+id+'"]';
    const sel2 = 'tbody.surebet_record[dataid="'+id+'"]';
    const row = document.querySelector(sel1) || document.querySelector(sel2);
    if (!row) return null;
    const getTxt = (el) => el ? (el.textContent || '').trim() : '';
    const oddsCells = Array.from(row.querySelectorAll('td.value[class*="odd_record_"]'));
    const odds1 = getTxt(oddsCells[0]);
    const odds2 = getTxt(oddsCells[1]);
    const sels = ['td.profit','td[class*="profit"]','td.gain','td.percent','td.max_profit','.profit','.gain','.percent'];
    let profit = '';
    for (const s of sels) {
      const el = row.querySelector(s);
      const t = getTxt(el);
      if (t) { profit = t; break; }
    }
    return {odds1, odds2, profit};
    """
    try:
        return driver.execute_script(js, tbody_id)
    except Exception:
        return None

def handle_update_for_id(tbody_id):
    # 🔒 BOOTSTRAP alatt nem küldünk UPDATE-et – csak figyeljük az ID-ket
    if in_bootstrap_phase():
        return

    try:
        snap = snapshot_update_values_by_id(tbody_id)
        if not snap:
            return
        odds1_text = (snap.get("odds1") or "").strip()
        odds2_text = (snap.get("odds2") or "").strip()
        o1 = parse_float(odds1_text)
        o2 = parse_float(odds2_text)
        profit_dom = (snap.get("profit") or "").strip()
        if profit_dom:
            profit_now = norm_profit_str(profit_dom)
        else:
            if o1 is not None and o2 is not None:
                profit_now = norm_profit_str(compute_profit_percent(o1, o2))
            else:
                profit_now = norm_profit_str("0%")
        o1n = norm_odds(o1 if o1 is not None else odds1_text)
        o2n = norm_odds(o2 if o2 is not None else odds2_text)

        if tbody_id not in last_sent_state:
            last_sent_state[tbody_id] = {"odds1": o1n, "odds2": o2n, "profit_percent": profit_now}
            return

        prev = last_sent_state[tbody_id]
        changed = (o1n != prev.get("odds1")) or (o2n != prev.get("odds2")) or (profit_now != prev.get("profit_percent"))
        can_send = (time.time() - last_update_attempt_ts.get(tbody_id, 0)) >= UPDATE_MIN_INTERVAL

        if changed and can_send:
            payload = {
                "type": "update",
                "id": tbody_id,
                "odds1": o1n,
                "odds2": o2n,
                "profit_percent": profit_now,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            _pending_update_buffer.append(payload)
            last_update_attempt_ts[tbody_id] = time.time()
            # ha már sok UPDATE/DELETE gyűlt, azonnal flush
            maybe_flush_immediate()
    except Exception:
        return

# ---------- TAB REGISZTEREK ----------
group_tabs = {}
group_blocked_until = {}
next_tabs  = {}

id_source = {}
last_seen_ts = {}

handle_birth = {}

# ---------- GROUP helpers ----------
def is_group_blocked(url, now_ts):
    return now_ts < group_blocked_until.get(url, 0)

def block_group_url(url, seconds, reason=""):
    group_blocked_until[url] = time.time() + seconds
    log(f"⛔ GROUP tiltólista {seconds}s: {url} ({reason})")

def close_group_tab(url):
    info = group_tabs.get(url)
    if not info:
        return
    try:
        handle = info["handle"]
        if handle in driver.window_handles:
            driver.switch_to.window(handle)
            driver.close()
    except Exception:
        pass
    finally:
        group_tabs.pop(url, None)
        try:
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass

def find_group_link_in_tbody(tbody):
    try:
        a = tbody.find_element(By.CSS_SELECTOR, "a.group-link")
        href = a.get_attribute("href")
        if not href:
            return None
        cur = urlparse(driver.current_url)
        base = f"{cur.scheme}://{cur.netloc}"
        return urljoin(base, href)
    except Exception:
        return None

def _rand_group_refresh_interval():
    return random.uniform(GROUP_REFRESH_MIN, GROUP_REFRESH_MAX)

def open_group_tab_if_needed(group_url):
    now_ts = time.time()
    if group_url in group_tabs:
        if LOG_GROUP_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ Group már nyitva, nem nyitjuk újra: {group_url}")
        return
    if is_group_blocked(group_url, now_ts):
        log(f"⏳ Group URL tiltva még: {group_url}")
        return

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    try:
        driver.switch_to.new_window('tab')
        driver.get(group_url)
        _inject_disable_animations()
        handle = driver.current_window_handle

        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tb_count = len(driver.find_elements(By.CSS_SELECTOR, GROUP_SELECTOR))
        if tb_count <= GROUP_EMPTY_CLOSE_TB_THRESHOLD:
            try:
                driver.close()
            except Exception:
                pass
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
            block_group_url(group_url, GROUP_REOPEN_BACKOFF_SEC, "empty-at-open")
            return

        now = time.time()
        group_tabs[group_url] = {
            "handle": handle,
            "active_ids": set(),
            "created_at": now,
            "last_refresh": now,
            "next_refresh": now + _rand_group_refresh_interval(),
            "needs_scan": True,
        }
        if original and original in driver.window_handles:
            driver.switch_to.window(original)
        log(f"🆕 Group tab nyitva: {group_url}")
        return
    except Exception as e:
        warn(f"⚠️ Group nyitás hiba: {e}")
        block_group_url(group_url, GROUP_ERR_BACKOFF_SEC, "open-fail")
        try:
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
        except Exception:
            pass

def maybe_refresh_group_tab(url: str, info: dict) -> bool:
    now = time.time()
    if now - info.get("created_at", now) < GROUP_REFRESH_SKIP_ON_NEW_SEC:
        info["next_refresh"] = now + _rand_group_refresh_interval()
        return False
    if now < info.get("next_refresh", 0):
        return False

    ok = False
    try:
        result = _safe_execute_async_script(r"""
            var callback = arguments[0];
            try {
                var sc = document.querySelector('div.table-container.product-table-container');
                if (!sc) { callback({ok:false, err:'container-not-found'}); return; }
                fetch(window.location.href, {cache:'no-store'})
                  .then(r => { if (!r.ok) throw new Error('http-'+r.status); return r.text(); })
                  .then(html => {
                      var parser = new DOMParser();
                      var doc = parser.parseFromString(html, 'text/html');
                      var newSc = doc.querySelector('div.table-container.product-table-container');
                      if (!newSc) { callback({ok:false, err:'new-container-not-found'}); return; }
                      var y = window.scrollY;
                      sc.innerHTML = newSc.innerHTML;
                      window.scrollTo(0, y);
                      callback({ok:true});
                  })
                  .catch(e => callback({ok:false, err:String(e)}));
            } catch(e) { callback({ok:false, err:String(e)}); }
        """)
        ok = bool(result and result.get("ok"))
    except Exception as e:
        warn(f"⚠️ Group részleges refresh hiba: {e}")
        ok = False

    if not ok:
        try:
            driver.refresh()
            _inject_disable_animations()
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
            )
            ok = True
        except Exception as e:
            warn(f"⚠️ Group teljes reload hiba: {e}")
            ok = False

    info["last_refresh"] = now
    info["next_refresh"] = now + _rand_group_refresh_interval()
    if ok:
        info["needs_scan"] = True
    return ok

# ---------- NEXT helpers ----------
def _rand_next_refresh_interval():
    return random.uniform(NEXT_REFRESH_MIN, NEXT_REFRESH_MAX)

def find_next_page_link():
    try:
        a = driver.find_element(By.CSS_SELECTOR, "a.next_page")
        href = a.get_attribute("href")
        if not href:
            return None
        cur = urlparse(driver.current_url)
        base = f"{cur.scheme}://{cur.netloc}"
        return urljoin(base, href)
    except Exception:
        return None

def open_next_tab_if_needed(next_url):
    if next_url in next_tabs:
        if LOG_NEXT_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ NEXT már nyitva: {next_url}")
        return

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    try:
        driver.switch_to.new_window('tab')
        driver.get(next_url)
        _inject_disable_animations()
        handle = driver.current_window_handle

        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tb_count = len(driver.find_elements(By.CSS_SELECTOR, NEXT_SELECTOR))
        if tb_count <= NEXT_EMPTY_CLOSE_TB_THRESHOLD:
            try:
                driver.close()
            except Exception:
                pass
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
            log(f"🔒 NEXT zárva üres miatt: {next_url}")
            return

        now = time.time()
        next_tabs[next_url] = {
            "handle": handle,
            "active_ids": set(),
            "created_at": now,
            "last_refresh": now,
            "next_refresh": now + _rand_next_refresh_interval(),
            "needs_scan": True,
        }

        if original and original in driver.window_handles:
            driver.switch_to.window(original)
        log(f"🆕 NEXT tab nyitva: {next_url}")
        return
    except Exception as e:
        warn(f"⚠️ NEXT nyitás hiba: {e}")
        try:
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
        except Exception:
            pass
            
            
def _scan_current_page_ids_and_groups():
    """
    Az AKTUÁLIS oldalon:
      - összes tbody.surebet_record → ID-k
      - minden tbody-ből group-link (ha van)
    Visszatérés: (ids_set, group_urls_set)
    """
    ids = set()
    group_urls = set()
    now_ts = time.time()

    try:
        tbodys = driver.find_elements(By.CSS_SELECTOR, "tbody.surebet_record")
    except Exception:
        return ids, group_urls

    for tbody in tbodys:
        try:
            tid = tbody.get_attribute("data-id") or tbody.get_attribute("dataid")
        except Exception:
            tid = None
        if not tid:
            continue

        ids.add(tid)
        # induláskori last_seen/id_source is legyen rendben
        last_seen_ts[tid] = now_ts
        id_source[tid] = "initial_scan"

        try:
            g = find_group_link_in_tbody(tbody)
            if g:
                group_urls.add(g)
        except Exception:
            pass

    return ids, group_urls


def maybe_refresh_next_tab(url: str, info: dict) -> bool:
    now = time.time()
    if now < info.get("next_refresh", 0):
        return False

    ok = False
    try:
        result = _safe_execute_async_script(r"""
            var callback = arguments[0];
            try {
                var sc = document.querySelector('div.table-container.product-table-container');
                if (!sc) { callback({ok:false, err:'container-not-found'}); return; }
                fetch(window.location.href, {cache:'no-store'})
                  .then(r => { if (!r.ok) throw new Error('http-'+r.status); return r.text(); })
                  .then(html => {
                      var parser = new DOMParser();
                      var doc = parser.parseFromString(html, 'text/html');
                      var newSc = doc.querySelector('div.table-container.product-table-container');
                      if (!newSc) { callback({ok:false, err:'new-container-not-found'}); return; }
                      var y = window.scrollY;
                      sc.innerHTML = newSc.innerHTML;
                      window.scrollTo(0, y);
                      callback({ok:true});
                  })
                  .catch(e => callback({ok:false, err:String(e)}));
            } catch(e) { callback({ok:false, err:String(e)}); }
        """)
        ok = bool(result and result.get("ok"))
    except Exception as e:
        warn(f"⚠️ NEXT részleges refresh hiba: {e}")
        ok = False

    info["last_refresh"] = now
    info["next_refresh"] = now + _rand_next_refresh_interval()
    if ok:
        info["needs_scan"] = True
    return ok
    
    
# === ÚJ: háttér GROUP/NEXT tab-megnyitó + időszakos TAB cleanup ===

GROUP_NEXT_OPEN_QUEUE = Queue(maxsize=2000)
group_open_pending = set()
next_open_pending = set()


def _open_group_tab_sync(group_url: str):
    """
    Régi open_group_tab_if_needed logika, de külön függvényben.
    Ezt a háttér worker hívja, a fő ciklus csak queue-ba teszi a kérést.
    """
    now_ts = time.time()
    if group_url in group_tabs:
        if LOG_GROUP_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ Group már nyitva (sync): {group_url}")
        return
    if is_group_blocked(group_url, now_ts):
        log(f"⏳ Group URL tiltva (sync): {group_url}")
        return

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    try:
        driver.switch_to.new_window('tab')
        driver.get(group_url)
        _inject_disable_animations()
        handle = driver.current_window_handle
        handle_birth[handle] = time.time()

        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tb_count = len(driver.find_elements(By.CSS_SELECTOR, GROUP_SELECTOR))
        if tb_count <= GROUP_EMPTY_CLOSE_TB_THRESHOLD:
            try:
                driver.close()
            except Exception:
                pass
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
            block_group_url(group_url, GROUP_REOPEN_BACKOFF_SEC, "empty-at-open")
            return

        now = time.time()
        group_tabs[group_url] = {
            "handle": handle,
            "active_ids": set(),
            "created_at": now,
            "last_refresh": now,
            "next_refresh": now + _rand_group_refresh_interval(),
            "needs_scan": True,
        }
        if original and original in driver.window_handles:
            driver.switch_to.window(original)
        log(f"🆕 Group tab nyitva (sync): {group_url}")
        return
    except Exception as e:
        warn(f"⚠️ Group nyitás hiba (sync): {e}")
        block_group_url(group_url, GROUP_ERR_BACKOFF_SEC, "open-fail")
        try:
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
        except Exception:
            pass


def open_group_tab_if_needed(group_url: str):
    """
    ASZINKRON GROUP TAB NYITÁS:
    - itt már NEM hívunk driver.get-et
    - csak betesszük a kérést a queue-ba
    - a háttér worker (_open_group_tab_sync) intézi a lassú munkát
    """
    now_ts = time.time()
    if group_url in group_tabs or group_url in group_open_pending:
        if LOG_GROUP_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ Group már nyitva vagy épp nyílik: {group_url}")
        return
    if is_group_blocked(group_url, now_ts):
        log(f"⏳ Group URL tiltva (async wrapper): {group_url}")
        return

    group_open_pending.add(group_url)
    try:
        GROUP_NEXT_OPEN_QUEUE.put_nowait({"type": "group", "url": group_url})
    except Exception:
        group_open_pending.discard(group_url)
        warn("⚠️ GROUP_NEXT_OPEN_QUEUE tele, group nyitás kihagyva")


def _open_next_tab_sync(next_url: str):
    """
    Régi open_next_tab_if_needed logika, de külön függvényben.
    Háttér worker használja.
    """
    if next_url in next_tabs:
        if LOG_NEXT_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ NEXT már nyitva (sync): {next_url}")
        return

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    try:
        driver.switch_to.new_window('tab')
        driver.get(next_url)
        _inject_disable_animations()
        handle = driver.current_window_handle
        handle_birth[handle] = time.time()

        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tb_count = len(driver.find_elements(By.CSS_SELECTOR, NEXT_SELECTOR))
        if tb_count <= NEXT_EMPTY_CLOSE_TB_THRESHOLD:
            try:
                driver.close()
            except Exception:
                pass
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
            log(f"🔒 NEXT zárva üres miatt (sync): {next_url}")
            return

        now = time.time()
        next_tabs[next_url] = {
            "handle": handle,
            "active_ids": set(),
            "created_at": now,
            "last_refresh": now,
            "next_refresh": now + _rand_next_refresh_interval(),
            "needs_scan": True,
        }

        if original and original in driver.window_handles:
            driver.switch_to.window(original)
        log(f"🆕 NEXT tab nyitva (sync): {next_url}")
        return
    except Exception as e:
        warn(f"⚠️ NEXT nyitás hiba (sync): {e}")
        try:
            if original and original in driver.window_handles:
                driver.switch_to.window(original)
        except Exception:
            pass


def open_next_tab_if_needed(next_url: str):
    """
    ASZINKRON NEXT TAB NYITÁS:
    - nem blokkoljuk a fő while ciklust
    - csak sorba tesszük a nyitási kérést
    """
    if next_url in next_tabs or next_url in next_open_pending:
        if LOG_NEXT_ALREADY_OPEN_VERBOSE:
            log(f"ℹ️ NEXT már nyitva vagy épp nyílik: {next_url}")
        return

    next_open_pending.add(next_url)
    try:
        GROUP_NEXT_OPEN_QUEUE.put_nowait({"type": "next", "url": next_url})
    except Exception:
        next_open_pending.discard(next_url)
        warn("⚠️ GROUP_NEXT_OPEN_QUEUE tele, NEXT nyitás kihagyva")


def group_next_opener_worker():
    """
    Háttér worker:
    - GROUP_NEXT_OPEN_QUEUE-ből veszi ki a 'group' / 'next' nyitási feladatokat
    """
    global MAIN_HANDLE, DRIVER_DEAD
    while True:
        if DRIVER_DEAD:
            warn("💀 GROUP/NEXT opener worker leáll – DRIVER_DEAD=True.")
            break

        try:
            task = GROUP_NEXT_OPEN_QUEUE.get(timeout=1.0)

        except Empty:
            continue

        if not isinstance(task, dict):
            GROUP_NEXT_OPEN_QUEUE.task_done()
            continue

        ttype = task.get("type")
        url = task.get("url")
        if not url:
            GROUP_NEXT_OPEN_QUEUE.task_done()
            continue

        try:
            if ttype == "group":
                _open_group_tab_sync(url)
            elif ttype == "next":
                _open_next_tab_sync(url)
        except Exception as e:
            warn(f"[GROUP/NEXT-OPENER] Hiba ({ttype}): {e}")
        finally:
            if ttype == "group":
                group_open_pending.discard(url)
            elif ttype == "next":
                next_open_pending.discard(url)
            GROUP_NEXT_OPEN_QUEUE.task_done()


def cleanup_stray_tabs():
    """
    Időszakos TAB takarítás:

    - Összes window handle-t lekérdezzük
    - MAIN_HANDLE, group_tabs, next_tabs handle-jei VÉDETTEK
    - Minden más:
        - ha külső (valid_external) VAGY surebet NAV (is_nav_url),
        - és TAB_CLEANUP_MIN_AGE-nél régebbi,
      akkor bezárjuk.
    """
    global MAIN_HANDLE

    try:
        handles = list(driver.window_handles)
    except Exception:
        return

    if not handles:
        return

    protected = set()
    try:
        if MAIN_HANDLE and MAIN_HANDLE in handles:
            protected.add(MAIN_HANDLE)
    except Exception:
        pass

    # group tabok védése + halottak kisöprése a dict-ből
    for url, info in list(group_tabs.items()):
        h = info.get("handle")
        if not h or h not in handles:
            group_tabs.pop(url, None)
            continue
        protected.add(h)

    # next tabok védése + halottak kisöprése
    for url, info in list(next_tabs.items()):
        h = info.get("handle")
        if not h or h not in handles:
            next_tabs.pop(url, None)
            continue
        protected.add(h)

    now = time.time()
    closed = 0

    for h in handles:
        if h in protected:
            continue

        birth = handle_birth.get(h)
        age = (now - birth) if birth is not None else (TAB_CLEANUP_MIN_AGE + 1)

        if age < TAB_CLEANUP_MIN_AGE:
            # frissen nyílt ismeretlen tab – még nem nyúlunk hozzá
            continue

        try:
            driver.switch_to.window(h)
            try:
                cur = driver.current_url or ""
            except Exception:
                cur = ""
        except Exception:
            continue

        # Csak külső vagy NAV tabokat csukjunk
        try:
            if valid_external(cur) or is_nav_url(cur):
                try:
                    driver.close()
                    closed += 1
                    handle_birth.pop(h, None)
                except Exception:
                    pass
        except Exception:
            continue

    try:
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)
        elif driver.window_handles:
            driver.switch_to.window(driver.window_handles[0])
    except Exception:
        pass

    if closed:
        log(f"🧹 TAB cleanup: {closed} stray tab bezárva.")


def tab_cleanup_worker():
    """
    Háttér worker – X másodpercenként lefut a cleanup_stray_tabs().
    """
    global DRIVER_DEAD
    while True:
        if DRIVER_DEAD:
            warn("💀 TAB cleanup worker leáll – DRIVER_DEAD=True.")
            break

        try:
            time.sleep(TAB_CLEANUP_INTERVAL)
            cleanup_stray_tabs()

        except Exception as e:
            warn(f"[TAB-CLEANUP] Hiba: {e}")
            time.sleep(5)


# ---------- PLAY/PAUSE → SHIFT+P AUTUPDATE KEZELÉS ----------
# --- Shift+P autoupdate detektálás (ÚJ) ---
SHIFT_P_MAX_TRIES_FIRST_MIN = 6
AUTUPDATE_BANNER_TEXT = "Auto updates — Shift+P to pause them"
LOGIN_TS = None
_autoupdate_attempts = 0

def _autoupdate_banner_present():
    """
    True/False/None — ellenőrzi, hogy látszik-e a "Auto updates — Shift+P to pause them" szöveg.
    A hosszú kötőjeleket normalizáljuk, hogy a vizsgálat stabil legyen.
    """
    try:
        return bool(_safe_execute_script(r"""
            try {
              var target = (arguments[0] || "").toLowerCase();
              var txt = (document.body ? document.body.innerText : (document.documentElement.innerText || "")) || "";
              txt = txt.toLowerCase();
              txt = txt.replace(/\u2014|\u2013/g, '-');   // hosszú kötőjelek -> '-'
              target = target.replace(/\u2014|\u2013/g, '-');
              return txt.indexOf(target) !== -1;
            } catch(e){ return null; }
        """, AUTUPDATE_BANNER_TEXT))
    except Exception:
        return None




def _dismiss_cookie_like_overlays():
    try:
        _safe_execute_script(r"""
        (function(){
          var cands = [
            '#onetrust-banner-sdk', '#CybotCookiebotDialog', '.cc-window',
            '.cookie', '.cookies', '[data-cookie]', '[aria-label*="cookie" i]'
          ];
          cands.forEach(function(sel){
            var el = document.querySelector(sel);
            if (!el) return;
            var st = window.getComputedStyle(el);
            if (st && st.position === 'fixed') {
              el.style.display='none';
              el.style.visibility='hidden';
              el.style.pointerEvents='none';
            }
          });
        })();
        """)
    except Exception:
        pass

def _get_autoupdate_state():
    try:
        txt = _safe_execute_script(r"""
        var w = document.querySelector('div.paginate-and.mb-3');
        return w ? (w.textContent || '').toLowerCase() : '';
        """) or ""
        if not txt:
            return None
        if "auto updates" in txt:
            if "pause them" in txt:
                return "running"
            if "start them" in txt:
                return "stopped"
        return None
    except Exception:
        return None

def _send_shift_p():
    try:
        _safe_execute_script("window.focus(); try{document.activeElement.blur();}catch(e){}")
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "key": "P",
            "code": "KeyP",
            "windowsVirtualKeyCode": 80,
            "nativeVirtualKeyCode": 80,
            "modifiers": 8
        })
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "key": "P",
            "code": "KeyP",
            "windowsVirtualKeyCode": 80,
            "nativeVirtualKeyCode": 80,
            "modifiers": 8
        })
        return True
    except Exception:
        pass
    try:
        actions = ActionChains(driver)
        actions.key_down(Keys.SHIFT).send_keys('p').key_up(Keys.SHIFT).perform()
        return True
    except Exception:
        pass
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.SHIFT, 'p')
        return True
    except Exception:
        return False

MAIN_HANDLE = None
main_refresh_enabled = False
main_last_refresh = 0.0
main_next_refresh = 0.0

last_keepalive_ping_ts = 0.0

paginate_refresh_enabled = False
paginate_last_refresh = 0.0
paginate_next_refresh = 0.0
has_any_next_tab_opened_ever = False

def _rand_main_refresh_interval():
    return random.uniform(MAIN_REFRESH_MIN, MAIN_REFRESH_MAX)

def _rand_paginate_refresh_interval():
    return random.uniform(MAIN_PAGINATE_REFRESH_MIN, MAIN_PAGINATE_REFRESH_MAX)

def _wait_main_container(timeout=8):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
    )

def ensure_main_autoupdate():
    """
    Login utáni első 60 mp: Shift+P max 6×, amíg nem látszik a
    '(Auto updates — Shift+P to pause them)' szöveg.
    Később: ha eltűnik, Shift+P max 3×. Ha így sem látszik, timed-refresh fallback.
    """
    global main_refresh_enabled, main_last_refresh, main_next_refresh, _autoupdate_attempts, LOGIN_TS

    present = _autoupdate_banner_present()

    if present:
        main_refresh_enabled = False
        main_next_refresh = 0.0
        _autoupdate_attempts = 0
        return

    first_minute = (time.time() - (LOGIN_TS or 0)) <= 60
    max_tries = SHIFT_P_MAX_TRIES_FIRST_MIN if first_minute else 3

    tries = 0
    while present is False and _autoupdate_attempts < max_tries and tries < max_tries:
        if _send_shift_p():
            time.sleep(0.35)
        tries += 1
        _autoupdate_attempts += 1
        present = _autoupdate_banner_present()

    if present:
        main_refresh_enabled = False
        main_next_refresh = 0.0
        _autoupdate_attempts = 0
    else:
        main_refresh_enabled = True
        main_last_refresh = time.time()
        main_next_refresh = main_last_refresh + _rand_main_refresh_interval()

def maybe_refresh_main_page():
    global main_refresh_enabled, main_last_refresh, main_next_refresh
    if not main_refresh_enabled:
        return
    now = time.time()
    if now < main_next_refresh:
        return
    try:
        current = driver.current_window_handle
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)

        driver.refresh()
        _inject_disable_animations()
        _wait_main_container(timeout=10)
        main_last_refresh = now
        main_next_refresh = now + _rand_main_refresh_interval()
        ensure_main_autoupdate()
    except Exception as e:
        warn(f"⚠️ Főoldal reload hiba: {e}")
        main_last_refresh = now
        main_next_refresh = now + _rand_main_refresh_interval()
    finally:
        try:
            if current and current in driver.window_handles:
                driver.switch_to.window(current)
        except Exception:
            pass

def maybe_refresh_main_paginate_and_try_open_next(len_tbodys_main: int):
    global paginate_refresh_enabled, paginate_last_refresh, paginate_next_refresh, has_any_next_tab_opened_ever

    try:
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)
    except Exception:
        return

    next_link = find_next_page_link()
    if next_link:
        open_next_tab_if_needed(next_link)
        has_any_next_tab_opened_ever = True
        paginate_refresh_enabled = False
        return

    if len_tbodys_main == 49:
        if not has_any_next_tab_opened_ever:
            if not paginate_refresh_enabled:
                paginate_refresh_enabled = True
                paginate_last_refresh = time.time()
                paginate_next_refresh = paginate_last_refresh + _rand_paginate_refresh_interval()
        else:
            if not next_tabs:
                if not paginate_refresh_enabled:
                    paginate_refresh_enabled = True
                    paginate_last_refresh = time.time()
                    paginate_next_refresh = paginate_last_refresh + _rand_paginate_refresh_interval()
    else:
        paginate_refresh_enabled = False

    if paginate_refresh_enabled and time.time() >= paginate_next_refresh:
        try:
            _safe_execute_async_script(r"""
                var callback = arguments[0];
                try {
                    var wrap = document.querySelector('div.paginate-and.mb-3');
                    if (!wrap) { callback({ok:false,err:'paginate-wrapper-not-found'}); return; }
                    fetch(window.location.href, {cache:'no-store'})
                      .then(r => { if (!r.ok) throw new Error('http-'+r.status); return r.text(); })
                      .then(html => {
                          var parser = new DOMParser();
                          var doc = parser.parseFromString(html, 'text/html');
                          var newWrap = doc.querySelector('div.paginate-and.mb-3');
                          if (!newWrap) { callback({ok:false,err:'new-wrapper-not-found'}); return; }
                          wrap.innerHTML = newWrap.innerHTML;
                          callback({ok:true});
                      })
                      .catch(e => callback({ok:false,err:String(e)}));
                } catch(e) { callback({ok:false,err:String(e)}); }
            """)
        except Exception:
            pass
        paginate_last_refresh = time.time()
        paginate_next_refresh = paginate_last_refresh + _rand_paginate_refresh_interval()

        try:
            link2 = find_next_page_link()
            if link2:
                open_next_tab_if_needed(link2)
                has_any_next_tab_opened_ever = True
                paginate_refresh_enabled = False
        except Exception:
            pass

# ---------- LOGIN ----------
def _submit_login_form_robust(timeout_after=12):
    _dismiss_cookie_like_overlays()

    BTN_SEL = "#sign-in-form-submit-button, input[type='submit'][name='commit']"
    PW_SEL  = "input[autocomplete='password']"

    try:
        pw = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, PW_SEL)))
        pw.send_keys(Keys.ENTER)
        WebDriverWait(driver, timeout_after).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        return True
    except Exception:
        pass

    try:
        btn = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, BTN_SEL)))
        _safe_execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
        _safe_execute_script("arguments[0].click();", btn)
        WebDriverWait(driver, timeout_after).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        return True
    except Exception:
        pass

    try:
        ok = _safe_execute_script(r"""
        (function(){
          var btn = document.querySelector(arguments[0]);
          if(!btn) return false;
          var f = btn.form || btn.closest('form');
          if(!f) return false;
          if (typeof f.requestSubmit === 'function') { f.requestSubmit(btn); }
          else { f.submit(); }
          return true;
        })();
        """, BTN_SEL)
        if ok:
            WebDriverWait(driver, timeout_after).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
            )
            return True
    except Exception:
        pass

    return False

def login():
    global LOGIN_TS, _autoupdate_attempts
    try:
        driver.get(LOGIN_URL)
        _inject_disable_animations()
        time.sleep(0.8)

        # 1) Gyors check: lehet, hogy a login URL már egyből a fő oldalt adja vissza
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
            )
            log("ℹ️ Már be vagy jelentkezve (table container már látszik), login kihagyva.")
            LOGIN_TS = time.time()
            _autoupdate_attempts = 0
            return
        except Exception:
            pass

        # 2) "You are already signed in." üzenet detektálása
        try:
            body_txt = _safe_execute_script(
                "return ((document.body && document.body.innerText) || "
                "(document.documentElement && document.documentElement.innerText) || '').toLowerCase();"
            ) or ""
        except Exception:
            body_txt = ""

        if "you are already signed in" in body_txt:
            log("ℹ️ 'You are already signed in.' – login lépés skip, ugrás a fő oldalra.")
            driver.get(DEFAULT_BASE)
            _inject_disable_animations()
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
            )
            LOGIN_TS = time.time()
            _autoupdate_attempts = 0
            return

        # 3) Normál login folyamat (form kitöltés)
        email_field = WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='email']"))
        )
        password_field = WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='password']"))
        )

        username = os.getenv("SB_USER") or ACTIVE_ACCOUNT["email"]
        password = os.getenv("SB_PASS") or ACTIVE_ACCOUNT["password"]

        human_type(email_field, username)
        human_type(password_field, password)

        if not _submit_login_form_robust(timeout_after=15):
            raise RuntimeError("Nem sikerült elküldeni a bejelentkezési űrlapot.")

        _inject_disable_animations()
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )

        log("✅ Sikeres bejelentkezés.")
        LOGIN_TS = time.time()
        _autoupdate_attempts = 0

    except Exception as e:
        print(f"❌ Bejelentkezés sikertelen: {e}")
        try:
            # Fallback: még egyszer megnézzük a login oldalt, de itt is kezeljük az "already signed in"-t
            driver.get(LOGIN_URL)
            _inject_disable_animations()
            time.sleep(0.8)

            try:
                body_txt = _safe_execute_script(
                    "return ((document.body && document.body.innerText) || "
                    "(document.documentElement && document.documentElement.innerText) || '').toLowerCase();"
                ) or ""
            except Exception:
                body_txt = ""

            if "you are already signed in" in body_txt:
                log("ℹ️ 'You are already signed in.' (fallback ág) – ugrás a fő oldalra.")
                driver.get(DEFAULT_BASE)
                _inject_disable_animations()
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
                )
                LOGIN_TS = time.time()
                _autoupdate_attempts = 0
                return

            # ha mégis login form van, próbáljuk ENTER-rel elküldeni
            try:
                pw = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='password']"))
                )
                pw.send_keys(Keys.ENTER)
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
                )
                log("✅ Sikeres bejelentkezés (fallback ENTER).")
                LOGIN_TS = time.time()
                _autoupdate_attempts = 0
                return
            except Exception:
                raise
        except Exception:
            try:
                driver.quit()
            except:
                pass
            raise SystemExit(1)


# ---------- SCAN függvények GROUP/NEXT ----------
def group_scan_tab(url: str, info: dict, higher_ids: set):
    pending_deletes = []
    curr_ids_tab = set()
    should_close = False
    new_ids_for_save = []

    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tbodys = driver.find_elements(By.CSS_SELECTOR, GROUP_SELECTOR)

        if len(tbodys) <= GROUP_EMPTY_CLOSE_TB_THRESHOLD:
            should_close = True

        for tbody in tbodys:
            tid = None
            try:
                tid = tbody.get_attribute("data-id") or tbody.get_attribute("dataid")
            except Exception:
                pass
            if not tid:
                continue

            curr_ids_tab.add(tid)
            last_seen_ts[tid] = time.time()
            id_source[tid] = url

            if tid in higher_ids:
                continue

            if tid in seen:
                handle_update_for_id(tid)
            else:
                new_ids_for_save.append(tid)

        batch_save_new_ids(new_ids_for_save, higher_ids=higher_ids)

        gone_here = info.get("active_ids", set()) - curr_ids_tab
        for gid in gone_here:
            pending_deletes.append((url, gid))

        info["active_ids"] = curr_ids_tab
        info["needs_scan"] = False

    except Exception as e:
        warn(f"⚠️ Group szkennelés hiba: {e}")
        should_close = True

    return curr_ids_tab, pending_deletes, should_close

def next_scan_tab(url: str, info: dict, curr_ids_main: set):
    pending_deletes = []
    curr_ids_tab = set()
    should_close = False
    found_next_link = None
    new_ids_for_save = []

    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-container.product-table-container"))
        )
        tbodys = driver.find_elements(By.CSS_SELECTOR, NEXT_SELECTOR)

        if len(tbodys) <= NEXT_EMPTY_CLOSE_TB_THRESHOLD:
            should_close = True

        if len(tbodys) >= 50:
            found_next_link = find_next_page_link()

        for tbody in tbodys:
            tid = None
            try:
                tid = tbody.get_attribute("data-id") or tbody.get_attribute("dataid")
            except Exception:
                pass
            if not tid:
                continue

            curr_ids_tab.add(tid)
            last_seen_ts[tid] = time.time()
            id_source[tid] = url

            if tid in curr_ids_main:
                continue

            if tid in seen:
                handle_update_for_id(tid)
            else:
                new_ids_for_save.append(tid)

        batch_save_new_ids(new_ids_for_save, higher_ids=curr_ids_main)

        gone_here = info.get("active_ids", set()) - curr_ids_tab
        for gid in gone_here:
            pending_deletes.append((url, gid))

        info["active_ids"] = curr_ids_tab
        info["needs_scan"] = False

    except Exception as e:
        warn(f"⚠️ NEXT szkennelés hiba: {e}")
        should_close = True

    return curr_ids_tab, pending_deletes, should_close, found_next_link

# ---------- dispatcher eredmények feldolgozása ----------
pending_delete_ids = set()

# --- UPDATE/DELETE threshold flush beállítások ---
UPDATE_IMMEDIATE_FLUSH_THRESHOLD = 6   # ha ennyi UPDATE+DELETE összejön, azonnal küldjük
DELETE_IMMEDIATE_FLUSH_THRESHOLD = 6

# Bufferek – ide gyűjtjük, amit még NEM küldtünk el a dispatchernek
_pending_update_buffer = []  # UPDATE payloadok
_pending_delete_buffer = []  # DELETE ID-k

def process_dispatcher_results(max_items=300):
    global active_ids, seen
    results = dispatcher.get_results(max_items=max_items)
    for res in results:
        rtype = res.get("type")
        tid = res.get("id")

        if rtype in ("save_ok", "save_dup_updated"):
            st = res.get("state_info", {})
            resp = res.get("resp", {})
            cid = resp.get("correlation_id")
            if tid not in seen:
                seen.add(tid)
                save_seen_line(tid)
            last_sent_state[tid] = {
                "odds1": norm_odds(st.get("odds1")),
                "odds2": norm_odds(st.get("odds2")),
                "profit_percent": norm_profit_str(st.get("profit_percent")),
            }
            last_update_ts[tid] = time.time()
            if tid not in active_ids:
                active_ids.add(tid); save_active_all(active_ids)
            log(f"💾 SAVE kész: {tid} ({'dup→update' if rtype=='save_dup_updated' else 'ok'}) cid={cid}")

        elif rtype == "save_duplicate":
            resp = res.get("resp", {})
            cid = resp.get("correlation_id")
            log(f"ℹ️ SAVE duplicate (külön UPDATE nem futott automatikusan): {tid} cid={cid}")

        elif rtype == "save_dup_update_fail":
            warn(f"⚠️ SAVE duplicate → UPDATE FAIL id={tid} status={res.get('status')} err={res.get('error')}")

        elif rtype == "save_error":
            err = res.get("error")
            cid = (err or {}).get("correlation_id") if isinstance(err, dict) else None
            warn(f"⚠️ SAVE hiba id={tid} status={res.get('status')} err={err} cid={cid}")

        elif rtype == "update_ok":
            p = res.get("payload", {})
            resp = res.get("resp", {})
            cid = resp.get("correlation_id")
            if tid:
                last_sent_state[tid] = {
                    "odds1": p.get("odds1"),
                    "odds2": p.get("odds2"),
                    "profit_percent": p.get("profit_percent"),
                }
                last_update_ts[tid] = time.time()
            log(f"🔄 UPDATE kész: {tid} cid={cid}")

        elif rtype == "update_error":
            status = res.get("status")
            err = res.get("error")
            cid = (err or {}).get("correlation_id") if isinstance(err, dict) else None
            if status == 404 and tid:
                t = prepare_new_task_for_id(tid)
                if t and t.get("finals") and valid_external(t["finals"][0]) and valid_external(t["finals"][1]):
                    tip_payload = _build_tip_payload_from_task(t)
                    update_payload = _build_update_payload_from_task(t)
                    dispatcher.enqueue_save({
                        "id": t["id"],
                        "tip_payload": tip_payload,
                        "update_payload": update_payload,
                        "state_info": {
                            "odds1": tip_payload["odds1"],
                            "odds2": tip_payload["odds2"],
                            "profit_percent": tip_payload["profit_percent"],
                        },
                        "finals": t.get("finals"),
                    })
                    log(f"↩️ UPDATE 404 → újra SAVE sorba téve: {tid}")
            else:
                warn(f"⚠️ UPDATE hiba id={tid} status={status} err={err} cid={cid}")

        elif rtype == "delete_ok":
            if tid in active_ids:
                active_ids.remove(tid); save_active_all(active_ids)
            last_sent_state.pop(tid, None)
            last_update_ts.pop(tid, None)
            last_update_attempt_ts.pop(tid, None)
            last_seen_ts.pop(tid, None)
            if tid in seen:
                seen.remove(tid); remove_seen_line(tid)
            pending_delete_ids.discard(tid)
            resp = res.get("resp", {})
            cid = resp.get("correlation_id")
            log(f"❌ DELETE kész: {tid} cid={cid}")

        elif rtype == "delete_error":
            err = res.get("error")
            cid = (err or {}).get("correlation_id") if isinstance(err, dict) else None
            warn(f"⚠️ DELETE hiba id={tid} status={res.get('status')} err={err} cid={cid}")
            pending_delete_ids.discard(tid)


def flush_pending_updates():
    """Elküldi az elbufferelt UPDATE payloadokat a dispatchernek."""
    global _pending_update_buffer
    if not _pending_update_buffer:
        return
    for payload in _pending_update_buffer:
        try:
            dispatcher.enqueue_update(payload)
        except Exception as e:
            warn(f"⚠️ UPDATE enqueue hiba (flush): {e}")
    _pending_update_buffer = []

def flush_pending_deletes():
    """Elküldi az elbufferelt DELETE ID-kat a dispatchernek."""
    global _pending_delete_buffer
    if not _pending_delete_buffer:
        return
    for gid in _pending_delete_buffer:
        try:
            dispatcher.enqueue_delete(gid)
        except Exception as e:
            warn(f"⚠️ DELETE enqueue hiba (flush): {e}")
    _pending_delete_buffer = []

def maybe_flush_immediate():
    """
    Ha összesen legalább 10 UPDATE+DELETE összegyűlt,
    azonnal flush-oljuk (nem várunk a ciklus végéig).
    """
    total = len(_pending_update_buffer) + len(_pending_delete_buffer)
    threshold = min(UPDATE_IMMEDIATE_FLUSH_THRESHOLD, DELETE_IMMEDIATE_FLUSH_THRESHOLD)
    if threshold > 0 and total >= threshold:
        flush_pending_updates()
        flush_pending_deletes()

def schedule_delete(gid: str):
    """
    DELETE-ek gyűjtése:
    - pending_delete_ids: jelzi, hogy már jelöltük törlésre
    - _pending_delete_buffer: amik még nem mentek el a dispatcherhez
    """
    # 🔒 BOOTSTRAP alatt nem törlünk Supabase-ben – előbb épüljön fel
    # az összes main/group/next oldal és a "valós" tbody lista.
    if in_bootstrap_phase():
        return

    if gid in pending_delete_ids:
        return
    pending_delete_ids.add(gid)
    _pending_delete_buffer.append(gid)
    maybe_flush_immediate()



# --- TAB-ALAPÚ RESYNC (ÚJ LOGIKA) -----------------------------------------

def collect_live_ids_from_open_tabs() -> set[str]:
    """
    Összegyűjti az összes élő tbody ID-t a JELENLEG NYITOTT tabokból:

      - MAIN_HANDLE (főoldal)
      - group_tabs
      - next_tabs

    Közben frissíti:
      - last_seen_ts[tid]
      - id_source[tid]
    """
    live_ids = set()
    now_ts = time.time()

    def _scan_current_window(source_label: str):
        nonlocal live_ids, now_ts
        try:
            tbodys = driver.find_elements(By.CSS_SELECTOR, "tbody.surebet_record")
        except Exception:
            return
        for tbody in tbodys:
            try:
                tid = tbody.get_attribute("data-id") or tbody.get_attribute("dataid")
            except Exception:
                tid = None
            if not tid:
                continue
            live_ids.add(tid)
            last_seen_ts[tid] = now_ts
            # ha még nem volt forrás beállítva, ne írjuk felül agresszíven
            if tid not in id_source:
                id_source[tid] = source_label

    # 1) MAIN
    try:
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)
            _scan_current_window("main")
    except Exception as e:
        warn(f"collect_live_ids_from_open_tabs: MAIN_HANDLE hiba: {e}")

    # 2) GROUP tabok
    for url, info in list(group_tabs.items()):
        handle = info.get("handle")
        if not handle or handle not in driver.window_handles:
            continue
        try:
            driver.switch_to.window(handle)
            _scan_current_window("group")
        except Exception as e:
            warn(f"collect_live_ids_from_open_tabs: group tab hiba {url}: {e}")

    # 3) NEXT tabok
    for url, info in list(next_tabs.items()):
        handle = info.get("handle")
        if not handle or handle not in driver.window_handles:
            continue
        try:
            driver.switch_to.window(handle)
            _scan_current_window("next")
        except Exception as e:
            warn(f"collect_live_ids_from_open_tabs: next tab hiba {url}: {e}")

    # próbáljunk visszamenni a MAIN-re
    try:
        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
            driver.switch_to.window(MAIN_HANDLE)
    except Exception:
        pass

    log(f"collect_live_ids_from_open_tabs: {len(live_ids)} élő tbody ID a nyitott tabokból")
    return live_ids


def full_resync_and_cleanup(max_groups=None):
    """
    ÚJ: TAB-ALAPÚ RESYNC

    - NEM mászkál driver.get-tel oldalról oldalra
    - CSAK a már nyitott tabokat nézi végig (MAIN + GROUP + NEXT)
    - Ami active_ids-ben van, de sehol nem látszik → DELETE (Supabase + TXT)
    """
    global active_ids

    log("🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…")

    try:
        live_ids = collect_live_ids_from_open_tabs()
    except Exception as e:
        warn(f"TAB-RESYNC: hiba az élő ID-k gyűjtésekor: {e}")
        live_ids = set()

    if not live_ids:
        log("ℹ️ TAB-RESYNC: nincs élő tbody ID a nyitott tabok alapján (friss indulásnál ez normális lehet).")

    stale = [tid for tid in list(active_ids) if tid not in live_ids]

    if stale:
        log(f"🗑️ TAB-RESYNC: {len(stale)} ID már nem él → törlés Supabase + txt")
        for tid in stale:
            schedule_delete(tid)

        # ami itt összegyűlt, azonnal küldjük is ki
        flush_pending_updates()
        flush_pending_deletes()
        process_dispatcher_results(max_items=2000)
    else:
        log("✨ TAB-RESYNC: nincs törlendő ID – minden élő a NYITOTT tabok szerint.")

    log("🔁 TAB-RESYNC kész.")


# ---------- ACCOUNT ROTATION / RESTART ----------

def get_next_account_key(current: str) -> str:
    """
    Következő account kulcs:
    - acc1 -> acc2
    - acc2 -> acc1
    - minden más -> acc1
    """
    if current == "acc1":
        return "acc2"
    if current == "acc2":
        return "acc1"
    return "acc1"


def restart_with_account(next_key: str):
    warn(f"♻️ Account váltás: {ACTIVE_ACCOUNT_KEY} → {next_key} – Chrome + script újraindítás...")

    # Itt MOST NEM hívunk TAB-RESYNC-et.
    # A folyamatos futás alatt a DISAPPEAR_GRACE_SEC alapú törlés már szépen
    # karbantartotta az active_ids-t, nem akarunk egy utolsó, részleges nézeten alapuló
    # „globális takarítást” rárúgni.

    # 1) Minden pending mentés/törlés flush-olása
    try:
        flush_pending_updates()
        flush_pending_deletes()
        process_dispatcher_results(max_items=2000)
        dispatcher.stop()
    except Exception:
        pass

    # 2) Chrome lezárása
    try:
        driver.quit()
    except Exception:
        pass

    # 4) Script újraindítása új accounttal
    os.execv(
        sys.executable,
        [sys.executable, sys.argv[0], f"--acc={next_key}"]
    )


# ---------- fő program ----------
seen = load_seen()
active_ids = load_active()
last_sent_state = {}
last_update_ts = {}
last_update_attempt_ts = {}
link_cache = load_link_cache()

# NOTE: A futtatáskor a login() hívás indít. Ha csak importálod, ne fusson automatikusan.
if __name__ == "__main__":
    RUN_STARTED_AT = time.time()
    login()

    log(f"🚀 BOOTSTRAP fázis indul: az első {int(BOOTSTRAP_SEC)} mp-ben "
        f"csak main/group/next tab nyitás + tbody ID gyűjtés, "
        f"nincs SAVE/UPDATE/DELETE/NAV.")

    try:
        MAIN_HANDLE = driver.current_window_handle
    except Exception:
        MAIN_HANDLE = None

    # NAV worker: csak BOOTSTRAP UTÁN indul
    nav_thread = None
    nav_started = False

    # GROUP/NEXT tab-nyitó háttér worker
    groupnext_thread = threading.Thread(target=group_next_opener_worker, daemon=True)
    groupnext_thread.start()
    log("🚀 Group/NEXT opener worker elindítva")

    # Időszakos TAB cleanup worker
    tab_cleanup_thread = threading.Thread(target=tab_cleanup_worker, daemon=True)
    tab_cleanup_thread.start()
    log("🧹 TAB cleanup worker elindítva")

    # Autoupdate indítása Shift+P-vel, ha kell
    ensure_main_autoupdate()
    prev_ids_main = set()

    def scan_next_tabs_evented(curr_ids_main: set):
        next_all_curr_ids = set()
        pending_deletes = []
        to_close = []
        open_requests = []

        items = list(next_tabs.items())
        for url, info in items:
            handle = info["handle"]
            try:
                if handle not in driver.window_handles:
                    next_tabs.pop(url, None)
                    continue
                driver.switch_to.window(handle)
            except Exception:
                to_close.append(url)
                continue

            try:
                if maybe_refresh_next_tab(url, info):
                    pass
            except Exception:
                pass

            if info.get("needs_scan", False):
                curr_ids_tab, pend_del, should_close, found_next = next_scan_tab(url, info, curr_ids_main)
                next_all_curr_ids.update(curr_ids_tab)
                pending_deletes.extend(pend_del)
                if found_next:
                    open_requests.append(found_next)
                if should_close:
                    to_close.append(url)

            try:
                if driver.window_handles:
                    driver.switch_to.window(MAIN_HANDLE or driver.window_handles[0])
            except Exception:
                pass

        return next_all_curr_ids, pending_deletes, to_close, open_requests

    def scan_group_tabs_evented(curr_ids_main: set, higher_ids: set):
        group_all_curr_ids = set()
        pending_deletes = []
        to_close = []

        items = list(group_tabs.items())
        for url, info in items:
            handle = info["handle"]
            try:
                if handle not in driver.window_handles:
                    group_tabs.pop(url, None)
                    continue
                driver.switch_to.window(handle)
            except Exception:
                to_close.append(url)
                continue

            try:
                if maybe_refresh_group_tab(url, info):
                    pass
            except Exception:
                pass

            if info.get("needs_scan", False):
                curr_ids_tab, pend_del, should_close = group_scan_tab(url, info, higher_ids)
                group_all_curr_ids.update(curr_ids_tab)
                pending_deletes.extend(pend_del)
                if should_close:
                    to_close.append(url)

            try:
                if driver.window_handles:
                    driver.switch_to.window(MAIN_HANDLE or driver.window_handles[0])
            except Exception:
                pass

        return group_all_curr_ids, pending_deletes, to_close

    try:
        while True:
            # 💀 Ha a WebDriver meghalt, ne kínlódjunk tovább – lépjünk ki a fő loopból
            if DRIVER_DEAD:
                warn("💀 WebDriver kapcsolat meghalt (DRIVER_DEAD=True) – kilépek a fő ciklusból.")
                break

            bootstrap = in_bootstrap_phase()

            # --- SUPABASE dispatcher eredmények ---
            if not bootstrap:
                process_dispatcher_results(max_items=400)

            now_ts = time.time()
            maybe_refresh_main_page()

            # --- MAIN tab életben tartása + újranyitása, ha kell ---
            try:
                # Ha nincs MAIN_HANDLE, vagy a handle már nincs a window_handles-ben → újranyitjuk
                if not MAIN_HANDLE or MAIN_HANDLE not in driver.window_handles:
                    log("⚠️ MAIN_HANDLE eltűnt, új főoldalt nyitok...")

                    # új tab + MAIN_URL betöltése
                    driver.switch_to.new_window("tab")
                    driver.get(MAIN_URL)
                    MAIN_HANDLE = driver.current_window_handle
                    handle_birth[MAIN_HANDLE] = time.time()

                    _inject_disable_animations()
                    _wait_main_container(timeout=12)
                    ensure_main_autoupdate()
                    time.sleep(3)

                # biztosan MAIN-en vagyunk
                driver.switch_to.window(MAIN_HANDLE)

                # időnként pici keepalive mozgás, hogy ne haljon el a tab
                if now_ts - last_keepalive_ping_ts >= 90:
                    tiny_keepalive_ping()
                    last_keepalive_ping_ts = now_ts

                tbodys_main = driver.find_elements(By.CSS_SELECTOR, "tbody.surebet_record")

            except Exception as e:
                warn(f"Főoldal scan hiba: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            ensure_main_autoupdate()


            curr_ids_main = set()
            new_ids_main = []

            for tbody in tbodys_main:
                try:
                    tbody_id = tbody.get_attribute("data-id") or tbody.get_attribute("dataid")
                except Exception:
                    tbody_id = None
                if not tbody_id:
                    continue

                curr_ids_main.add(tbody_id)
                last_seen_ts[tbody_id] = now_ts
                id_source[tbody_id] = 'main'

                # GROUP linkek folyamatos keresése + tabnyitás (BOOTSTRAP alatt is)
                try:
                    group_url = find_group_link_in_tbody(tbody)
                    if group_url:
                        open_group_tab_if_needed(group_url)
                except Exception:
                    pass

                # BOOTSTRAP alatt is megkülönböztetjük, mi seen, mi új,
                # de a SAVE/UPDATE úgyis no-op lesz a gating miatt.
                if tbody_id in seen:
                    handle_update_for_id(tbody_id)
                else:
                    new_ids_main.append(tbody_id)

            # Új ID-k NAV-queue-be (BOOTSTRAP alatt csak "előkészül", de nem küldünk)
            batch_save_new_ids(new_ids_main)

            # NEXT paginálás + első NEXT tab nyitása
            try:
                maybe_refresh_main_paginate_and_try_open_next(len_tbodys_main=len(tbodys_main))
            except Exception:
                pass

            # --- NEXT tabok scan ---
            next_all_curr_ids, next_pending_deletes, next_to_close, next_open_requests = scan_next_tabs_evented(curr_ids_main)

            # új NEXT URL-ek nyitása (BOOTSTRAP alatt is)
            for nurl in next_open_requests:
                try:
                    open_next_tab_if_needed(nurl)
                except Exception:
                    pass

            # --- GROUP tabok scan ---
            higher_ids = curr_ids_main | next_all_curr_ids
            group_all_curr_ids, group_pending_deletes, group_to_close = scan_group_tabs_evented(curr_ids_main, higher_ids)

            curr_ids_all_now = curr_ids_main | next_all_curr_ids | group_all_curr_ids
            now2 = time.time()

            # Eltűnt ID-k jelölése – a valódi DELETE a schedule_delete-ben BOOTSTRAP alatt még no-op
            # NEXT oldalon eltűnt ID-k
            for url, gid in next_pending_deletes:
                if gid in curr_ids_all_now:
                    continue
                last_ts = last_seen_ts.get(gid, 0.0)
                if (now2 - last_ts) >= DISAPPEAR_GRACE_SEC:
                    schedule_delete(gid)

            # GROUP oldalon eltűnt ID-k
            for url, gid in group_pending_deletes:
                if gid in curr_ids_all_now:
                    continue
                last_ts = last_seen_ts.get(gid, 0.0)
                if (now2 - last_ts) >= DISAPPEAR_GRACE_SEC:
                    schedule_delete(gid)

            # MAIN-en eltűnt ID-k
            maybe_gone_main = [aid for aid in list(active_ids) if id_source.get(aid) == 'main' and aid not in curr_ids_main]
            now_ts2 = time.time()
            for gid in maybe_gone_main:
                last_ts = last_seen_ts.get(gid, 0.0)
                if (now_ts2 - last_ts) >= DISAPPEAR_GRACE_SEC:
                    schedule_delete(gid)

            # TABOK BEZÁRÁSA
            for url in next_to_close:
                info = next_tabs.get(url)
                try:
                    if info and info["handle"] in driver.window_handles:
                        driver.switch_to.window(info["handle"])
                        driver.close()
                except Exception:
                    pass
                finally:
                    next_tabs.pop(url, None)
                    try:
                        if MAIN_HANDLE and MAIN_HANDLE in driver.window_handles:
                            driver.switch_to.window(MAIN_HANDLE)
                    except Exception:
                        pass

            for url in group_to_close:
                close_group_tab(url)
                block_group_url(url, GROUP_REOPEN_BACKOFF_SEC, "empty(<=1)-close")

            # Ciklus végén mindig flush-oljuk, ami a threshold alatt maradt
            # (BOOTSTRAP alatt ezek üresek, mert UPDATE/DELETE gatingel)
            flush_pending_updates()
            flush_pending_deletes()

            # ✅ ACCOUNT ROTÁCIÓ: ha letelt X perc, váltunk acc1 <-> acc2
            if ACCOUNT_ROTATE_MIN > 0:
                elapsed_min = (time.time() - RUN_STARTED_AT) / 60.0
                if elapsed_min >= ACCOUNT_ROTATE_MIN:
                    next_key = get_next_account_key(ACTIVE_ACCOUNT_KEY)
                    log(f"♻️ {ACCOUNT_ROTATE_MIN:.1f} perc letelt, váltás {ACTIVE_ACCOUNT_KEY} → {next_key}")
                    restart_with_account(next_key)

            # 🔴 NAV worker indítása – CSAK BOOTSTRAP UTÁN
            if not nav_started and not bootstrap:
                nav_thread = threading.Thread(target=background_nav_worker, daemon=True)
                nav_thread.start()
                log("🚀 NAV háttér worker elindítva (BOOTSTRAP után)")
                nav_started = True

            prev_ids_main = curr_ids_main
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        warn("🛑 Leállítva.")
    finally:
        try:
            flush_pending_updates()
            flush_pending_deletes()
            process_dispatcher_results(max_items=1000)
            dispatcher.stop()
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass

        driver = None

