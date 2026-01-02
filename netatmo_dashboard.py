import os
import sys
import json
import time
import subprocess
import logging
from datetime import datetime, timedelta, timezone, time as dtime
from collections import deque

# -------------------- DASHBOARD_BOOTSTRAP --------------------
# Wir stellen sicher, dass wir im richtigen Verzeichnis arbeiten
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    os.chdir(BASE_DIR)
except Exception:
    pass

# Logging SOFORT einrichten (für Diagnose bei Autostart-Problemen)
# Mit Rotation: max 2MB, 3 Backups
from logging.handlers import RotatingFileHandler
import threading

LOG_FILE = os.path.join(BASE_DIR, "dashboard.log")
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# File Handler mit Rotation
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=2*1024*1024,  # 2 MB
    backupCount=3,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Root Logger konfigurieren
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Duplikate verhindern (wichtig bei Reimport oder mehrfachem Start)
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)

root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

logging.info("=" * 50)
logging.info("Dashboard-Start initiiert")
logging.info(f"Python: {sys.version}")
logging.info(f"Arbeitsverzeichnis: {BASE_DIR}")

# -------------------------------------------------------------
# AUTOSTART-FIX: Warte auf Display bevor Tkinter importiert wird
# -------------------------------------------------------------
def wait_for_display(max_wait=60):
    """Wartet bis X11 Display verfügbar ist."""
    display = os.environ.get("DISPLAY", ":0")
    os.environ["DISPLAY"] = display
    logging.info(f"Warte auf Display {display}...")
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            result = subprocess.run(
                ["xdpyinfo"],
                env={**os.environ, "DISPLAY": display},
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                logging.info(f"Display verfügbar nach {time.time()-start_time:.1f}s")
                return True
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            # xdpyinfo nicht installiert - versuche trotzdem
            logging.warning("xdpyinfo nicht gefunden - versuche Tkinter direkt")
            return True
        except Exception as e:
            logging.debug(f"Display-Check: {e}")
        time.sleep(1)
    
    logging.error(f"Display nicht verfügbar nach {max_wait}s")
    return False

def wait_for_network(max_wait=30):
    """Wartet bis Netzwerk verfügbar ist. Prüft mehrere Ziele für Robustheit."""
    import socket
    import ipaddress
    logging.info("Warte auf Netzwerk...")
    
    # Mehrere Ziele für Robustheit (manche Netze blocken 8.8.8.8)
    NETWORK_TARGETS = [
        ("api.met.no", 443),       # Hauptziel: met.no API
        ("api.netatmo.com", 443),  # Netatmo API
        ("8.8.8.8", 53),           # Google DNS (Fallback)
        ("1.1.1.1", 53),           # Cloudflare DNS (Fallback)
    ]
    
    def is_ip_address(host):
        """Prüft ob host eine IP-Adresse ist."""
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        for host, port in NETWORK_TARGETS:
            try:
                # Erst DNS auflösen (außer bei IPs)
                if not is_ip_address(host):
                    socket.gethostbyname(host)
                
                # Context Manager für sauberes Socket-Handling
                with socket.create_connection((host, port), timeout=2):
                    logging.info(f"Netzwerk verfügbar nach {time.time()-start_time:.1f}s (via {host})")
                    return True
            except OSError:
                continue
        time.sleep(1)
    
    logging.warning("Netzwerk nicht verfügbar - starte trotzdem (Cache wird verwendet)")
    return False

# Warte auf Voraussetzungen beim Autostart
if not wait_for_display(60):
    logging.error("Kein Display verfügbar - Abbruch")
    sys.exit(1)

wait_for_network(30)

# -------------------------------------------------------------
# JETZT erst Tkinter und andere Module importieren
# -------------------------------------------------------------
import tkinter as tk
import requests
import math
from dotenv import load_dotenv

logging.info("Alle Imports erfolgreich")

# Skyfield
from skyfield.api import load as skyfield_load

# Automatische Sommerzeit (Python >= 3.9)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Fallback (dann +1h ohne DST)

# .env laden (zwingend aus dem BASE_DIR)
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

# Prüfung beim Start
if not os.getenv("SOLAREDGE_API_KEY"):
    logging.warning(f"ACHTUNG: Keine SolarEdge Keys in {ENV_PATH} gefunden!")

# --- Standort-Koordinaten (aus .env) ---
LOCATION_LAT = float(os.getenv("LOCATION_LAT", "47.3769"))  # Default: Zürich
LOCATION_LON = float(os.getenv("LOCATION_LON", "8.5417"))

# --- Netatmo-Konfiguration ---
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
TOKEN_URL = "https://api.netatmo.com/oauth2/token"
AUTHORIZE_URL = "https://api.netatmo.com/oauth2/authorize"
DATA_URL = "https://api.netatmo.com/api/getstationsdata"

# --- PV-Konfiguration ---
SITE_ID = os.getenv("SOLAREDGE_SITE_ID")
PV_API_KEY = os.getenv("SOLAREDGE_API_KEY")

# --- MET Norway Sunrise 3.0 User-Agent ---
# Kontakt-E-Mail aus .env laden (met.no empfiehlt identifizierbare UAs)
_contact_email = os.getenv("CONTACT_EMAIL", "")
if _contact_email:
    METNO_USER_AGENT = f"SmartHomeDashboard/6.0 (Raspberry Pi; Linux; contact: {_contact_email}; github.com/smarthome-dashboard)"
else:
    METNO_USER_AGENT = "SmartHomeDashboard/6.0 (Raspberry Pi; Linux; github.com/smarthome-dashboard)"

# Zentralisierte Timer-Intervalle (in Millisekunden)
INTERVALS = {
    'netatmo': 300_000,           # 5 min
    'astronomy': 900_000,         # 15 min
    'sun_position': 60_000,       # 1 min
    'moon_position': 60_000,      # 1 min
    'battery_blink': 500,         # 0.5s
    'pv_flush': 300_000,          # 5 min
    'health_check': 60_000,       # 1 min
    'redraw_debounce': 100,       # 0.1s
}

# Maximale Messungen pro Tag (Schutz vor Memory-Overflow)
MAX_PV_MEASUREMENTS_PER_DAY = 500  # ~1 pro Minute für 8h

# Dateien (werden im BASE_DIR erwartet)
TOKEN_FILE = os.path.join(BASE_DIR, "access_token.json")
PRESSURE_HISTORY_FILE = os.path.join(BASE_DIR, "pressure_history_7inch.json")
CACHE_FILE = os.path.join(BASE_DIR, "dashboard_cache.json")
SKYFIELD_EPH_PATH = os.path.join(BASE_DIR, "de421.bsp")
PV_DAILY_FILE = os.path.join(BASE_DIR, "pv_daily_data.json")

# Lokales Archiv für Messwerte (Sync zu OneDrive via Cronjob)
LOCAL_ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

NORMAL_PRESSURE = 1013.25  # hPa

# Farben
COLORS = {
    'bg': '#000000',
    'card_bg': '#0a0a0a',
    'text_primary': '#ffffff',
    'text_secondary': '#cccccc',
    'text_dim': '#808080',
    'accent_blue': '#2196F3',
    'accent_green': '#4CAF50',
    'accent_orange': '#FF9800',
    'accent_red': '#F44336',
    'border': '#333333',
    'moon': '#FFE4B5',
    'moon_dark': '#1a1a1a',      # Dunkle Seite des Mondes
    'moon_light': '#F5E6B3',     # Beleuchtete Seite (gelblicher)
    'sun': '#FFD700',
    'pressure_high': '#00FFFF',  
    'pressure_low' : '#8E7DBE',
    
    # --- BATTERIE ZUSTANDSFARBEN ---
    'battery_good': '#00FF00',      # Grün (> 40%)
    'battery_medium': '#FFD700',    # Gelb (20% - 40%)
    'battery_low': '#FF8C00',       # Orange (10% - 20%)
    'battery_critical': '#FF0000',  # Rot (<= 10%)
    
    # --- 7-SEGMENT LED FARBEN (70er Jahre Stil) ---
    'led_on': '#00FF66',            # Leuchtendes Grün
    'led_off': '#0A3020',           # Sichtbares dunkles Grün (70er Stil)
}

# ---------------------------------------------------
# Skyfield Globals
# ---------------------------------------------------
TS = None
EPH = None
SUN = None
MOON = None
EARTH = None
OBSERVER = None  # Gecachter Beobachterstandort
SKYFIELD_AVAILABLE = None  # None = uninitialisiert, True/False = Ergebnis


def init_skyfield():
    """
    Initialisiert Skyfield einmalig (lokale de421.bsp).
    Setzt SKYFIELD_AVAILABLE auf True bei Erfolg, False bei Fehler.
    Returns: True wenn verfügbar, False wenn nicht.
    
    Hinweis: Falls Ephemeride später hinzugefügt wird, wird erneut versucht.
    """
    global TS, EPH, SUN, MOON, EARTH, OBSERVER, SKYFIELD_AVAILABLE
    
    # Bereits erfolgreich initialisiert?
    if SKYFIELD_AVAILABLE is True:
        return True
    
    # Ephemeride prüfen
    if not os.path.exists(SKYFIELD_EPH_PATH):
        # Nur einmal loggen (wenn noch nie versucht)
        if SKYFIELD_AVAILABLE is None:
            logging.error(f"Skyfield Ephemeride fehlt: {SKYFIELD_EPH_PATH}")
            logging.error("Astronomie-Funktionen werden eingeschränkt sein.")
            SKYFIELD_AVAILABLE = False
        return False

    # Datei existiert - wenn vorher False war, erneut versuchen
    try:
        from skyfield import api as skyfield_api
        
        TS = skyfield_load.timescale()
        EPH = skyfield_load(SKYFIELD_EPH_PATH)
        SUN = EPH["sun"]
        MOON = EPH["moon"]
        EARTH = EPH["earth"]
        
        # Observer einmalig cachen (lat/lon sind konstant)
        OBSERVER = EARTH + skyfield_api.wgs84.latlon(LOCATION_LAT, LOCATION_LON)
        
        SKYFIELD_AVAILABLE = True
        logging.info("Skyfield erfolgreich initialisiert")
        return True
    except Exception as e:
        logging.exception(f"Fehler bei Skyfield-Initialisierung: {e}")
        SKYFIELD_AVAILABLE = False
        return False


def moon_illumination_percent_skyfield(t):
    """Illumination in % (0..100) ephemeridenbasiert."""
    if not init_skyfield():
        return 0
    e = EARTH.at(t)
    s = e.observe(SUN).apparent()
    m = e.observe(MOON).apparent()
    phase_angle = m.separation_from(s).radians  # 0..pi
    illum = (1 - math.cos(phase_angle)) / 2
    return int(round(illum * 100))


def classify_moon_phase(illumination_percent, trend):
    """
    Klassifiziert die Mondphase basierend auf Illumination und Trend.
    Gibt nur den Phasennamen zurück (Emoji nicht mehr benötigt).
    """
    if illumination_percent <= 2:
        return "Neumond"
    if illumination_percent >= 98:
        return "Vollmond"

    if illumination_percent < 48:
        if trend == "↑":
            return "Zunehmende Sichel"
        return "Abnehmende Sichel"

    if illumination_percent <= 52:
        if trend == "↑":
            return "Erstes Viertel"
        return "Letztes Viertel"

    if trend == "↑":
        return "Zunehmender Mond"
    return "Abnehmender Mond"


def get_sun_elevation_skyfield(t):
    """Berechnet die Sonnenhöhe (Elevation) in Grad für einen Zeitpunkt."""
    if not init_skyfield() or OBSERVER is None:
        return 0  # Fallback: Horizont
    
    try:
        astrometric = OBSERVER.at(t).observe(SUN)
        alt, az, distance = astrometric.apparent().altaz()
        return alt.degrees
    except Exception as e:
        logging.debug(f"Fehler bei Sonnenberechnung: {e}")
        return 0


def get_moon_elevation_skyfield(t):
    """Berechnet die Mondhöhe (Elevation) in Grad für einen Zeitpunkt."""
    if not init_skyfield() or OBSERVER is None:
        return 0  # Fallback: Horizont
    
    try:
        astrometric = OBSERVER.at(t).observe(MOON)
        alt, az, distance = astrometric.apparent().altaz()
        return alt.degrees
    except Exception as e:
        logging.debug(f"Fehler bei Mondberechnung: {e}")
        return 0


def find_sun_crossing_time(target_elevation, start_t, end_t, rising=True):
    """
    Findet den Zeitpunkt, an dem die Sonne eine bestimmte Elevation kreuzt.
    Binary search zwischen start_t und end_t.
    rising=True: Sucht aufsteigende Kreuzung, False: absteigende
    """
    if not init_skyfield():
        return None
    
    # Binary search mit 30 Iterationen (< 1 Sekunde Genauigkeit)
    for _ in range(30):
        mid_t = TS.tt_jd((start_t.tt + end_t.tt) / 2)
        mid_elev = get_sun_elevation_skyfield(mid_t)
        
        if rising:
            if mid_elev < target_elevation:
                start_t = mid_t
            else:
                end_t = mid_t
        else:
            if mid_elev > target_elevation:
                start_t = mid_t
            else:
                end_t = mid_t
    
    return TS.tt_jd((start_t.tt + end_t.tt) / 2)


def get_civil_twilight_skyfield(date=None):
    """
    Berechnet die zivile Dämmerung mit Skyfield.
    Zivile Dämmerung = Sonne bei -6° unter dem Horizont.
    
    Returns:
        (dawn_dt, dusk_dt): datetime Objekte in lokaler Zeit
        oder (None, None) bei Fehler oder fehlendem Skyfield
    """
    if not init_skyfield():
        return None, None
    
    try:
        local_tz = get_local_tz()
        
        if date is None:
            date_obj = datetime.now(local_tz).date()
        else:
            date_obj = date
        
        # Zeitfenster für die Suche: 00:00 bis 23:59 des Tages
        start_of_day = datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0, tzinfo=local_tz)
        end_of_day = datetime(date_obj.year, date_obj.month, date_obj.day, 23, 59, 59, tzinfo=local_tz)
        
        t_start = TS.from_datetime(start_of_day)
        t_end = TS.from_datetime(end_of_day)
        t_noon = TS.from_datetime(datetime(date_obj.year, date_obj.month, date_obj.day, 12, 0, 0, tzinfo=local_tz))
        
        CIVIL_TWILIGHT_ANGLE = -6.0  # Grad unter Horizont
        
        # Morgendämmerung: Suche zwischen Mitternacht und Mittag (aufsteigend)
        dawn_t = find_sun_crossing_time(CIVIL_TWILIGHT_ANGLE, t_start, t_noon, rising=True)
        if dawn_t is None:
            return None, None
        dawn_dt = dawn_t.utc_datetime().replace(tzinfo=timezone.utc).astimezone(local_tz)
        
        # Abenddämmerung: Suche zwischen Mittag und Mitternacht (absteigend)
        dusk_t = find_sun_crossing_time(CIVIL_TWILIGHT_ANGLE, t_noon, t_end, rising=False)
        if dusk_t is None:
            return None, None
        dusk_dt = dusk_t.utc_datetime().replace(tzinfo=timezone.utc).astimezone(local_tz)
        
        logging.debug(f"Zivile Dämmerung berechnet: {dawn_dt.strftime('%H:%M')} - {dusk_dt.strftime('%H:%M')}")
        return dawn_dt, dusk_dt
        
    except Exception as e:
        logging.exception(f"Fehler bei Berechnung der zivilen Dämmerung: {e}")
        return None, None


def get_moon_phase_skyfield():
    """Mondphase/Trend/Illumination ephemeridenbasiert (UTC)."""
    if not init_skyfield():
        return "Unbekannt", "?", 0
    
    try:
        t_now = TS.now()
        t_prev = TS.from_datetime(datetime.now(timezone.utc) - timedelta(hours=2))

        illum_now = moon_illumination_percent_skyfield(t_now)
        illum_prev = moon_illumination_percent_skyfield(t_prev)
        trend = "↑" if illum_now >= illum_prev else "↓"

        phase_name = classify_moon_phase(illum_now, trend)
        return phase_name, trend, illum_now
    except Exception as e:
        logging.debug(f"Fehler bei Mondphasenberechnung: {e}")
        return "Unbekannt", "?", 0


# ---------------------------------------------------
# TZ + MET Offset Helpers
# ---------------------------------------------------
def get_local_tz():
    """Europe/Zurich als tzinfo (mit DST), fallback +1h."""
    if ZoneInfo:
        return ZoneInfo("Europe/Zurich")
    return timezone(timedelta(hours=1))


def format_offset(dt):
    """Formatiert utcoffset als +HH:MM / -HH:MM."""
    off = dt.utcoffset()
    if off is None:
        return "+00:00"
    total_minutes = int(off.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hh = total_minutes // 60
    mm = total_minutes % 60
    return f"{sign}{hh:02d}:{mm:02d}"


def met_offset_for_date(local_tz, date_obj):
    """
    Offset für ein Datum robust bestimmen (12:00 lokale Zeit).
    """
    dt = datetime.combine(date_obj, dtime(12, 0), tzinfo=local_tz)
    return format_offset(dt)


# ---------------------------------------------------
# Hilfsfunktionen für api.met.no (Sunrise 3.0)
# ---------------------------------------------------
def parse_iso_to_local(time_str, local_tz):
    """Wandelt einen ISO-String von api.met.no in lokale Zeit um."""
    if not time_str:
        return None

    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"

    dt = datetime.fromisoformat(time_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if local_tz is not None:
        dt = dt.astimezone(local_tz)
    return dt


def calculate_moon_phase_fallback(date):
    """Lokale Berechnung der Mondphase (Fallback ohne Skyfield)."""
    known_new_moon = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)
    lunation = 29.530588
    diff = (date - known_new_moon).total_seconds() / 86400
    phase = (diff % lunation) / lunation

    elongation = 2 * math.pi * phase
    illumination = (1 - math.cos(elongation)) / 2
    illumination_percent = int(round(illumination * 100))

    trend = "↑" if phase < 0.5 else "↓"
    phase_name = classify_moon_phase(illumination_percent, trend)

    return phase_name, trend, illumination_percent


def refresh_access_token(refresh_token):
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def get_access_token():
    """Liest den vorhandenen Access Token oder führt OAuth-Fluss durch."""
    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
        raise EnvironmentError("CLIENT_ID, CLIENT_SECRET oder REDIRECT_URI fehlen.")

    try:
        with open(TOKEN_FILE, "r") as file:
            token_data = json.load(file)

        # Token noch gültig
        if time.time() < token_data.get("expires_at", 0):
            return token_data["access_token"]

        # refresh_token muss vorhanden sein
        old_refresh = token_data.get("refresh_token")
        if not old_refresh:
            raise KeyError("refresh_token fehlt im Token-File")

        new_data = refresh_access_token(old_refresh)

        if "refresh_token" not in new_data:
            new_data["refresh_token"] = old_refresh

        new_data["expires_at"] = time.time() + float(new_data.get("expires_in", 0))

        with open(TOKEN_FILE, "w") as wfile:
            json.dump(new_data, wfile)

        return new_data["access_token"]

    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError):
        pass

    # Autostart darf hier nicht blockieren
    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(
            "Netatmo OAuth nötig, aber kein TTY verfügbar (Autostart). "
            "Bitte TOKEN_FILE vorbereiten oder OAuth manuell im Terminal durchführen."
        )

    auth_url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=read_station"
        f"&response_type=code"
    )
    print("Öffne diesen Link im Browser und autorisiere die Anwendung:")
    print(auth_url)

    authorization_code = input("Authorization Code eingeben: ")
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": authorization_code,
        "redirect_uri": REDIRECT_URI,
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=20)
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time() + token_data["expires_in"]
    with open(TOKEN_FILE, "w") as file:
        json.dump(token_data, file)

    return token_data["access_token"]


def fetch_netatmo_data(access_token):
    response = requests.get(DATA_URL, params={"access_token": access_token}, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_pv_data():
    """Liest PV-Daten von SolarEdge."""
    if not SITE_ID or not PV_API_KEY:
        raise ValueError("SITE_ID oder PV_API_KEY fehlen in der Konfiguration.")

    url = f"https://monitoringapi.solaredge.com/site/{SITE_ID}/overview?api_key={PV_API_KEY}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

    ov = data.get("overview", {}) if isinstance(data, dict) else {}
    current_power = (ov.get("currentPower") or {}).get("power")
    daily_energy = (ov.get("lastDayData") or {}).get("energy")
    monthly_energy = (ov.get("lastMonthData") or {}).get("energy")
    yearly_energy = (ov.get("lastYearData") or {}).get("energy")

    return current_power, daily_energy, monthly_energy, yearly_energy


def get_sun_times():
    """Holt Sonnenauf-/untergang von api.met.no."""
    try:
        local_tz = get_local_tz()
        now_local = datetime.now(local_tz)
        date_obj = now_local.date()
        date_str = date_obj.strftime("%Y-%m-%d")

        params = {
            "lat": LOCATION_LAT,
            "lon": LOCATION_LON,
            "date": date_str,
            "offset": met_offset_for_date(local_tz, date_obj),
        }

        headers = {"User-Agent": METNO_USER_AGENT}

        resp = requests.get(
            "https://api.met.no/weatherapi/sunrise/3.0/sun",
            params=params,
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        props = data.get("properties", {})
        sunrise_obj = props.get("sunrise")
        sunset_obj = props.get("sunset")

        sunrise_str = "--:--"
        sunset_str = "--:--"
        day_length_str = "--:--"

        sunrise_dt = None
        sunset_dt = None

        if isinstance(sunrise_obj, dict) and sunrise_obj.get("time"):
            sunrise_dt = parse_iso_to_local(sunrise_obj["time"], local_tz)
            sunrise_str = sunrise_dt.strftime("%H:%M")

        if isinstance(sunset_obj, dict) and sunset_obj.get("time"):
            sunset_dt = parse_iso_to_local(sunset_obj["time"], local_tz)
            sunset_str = sunset_dt.strftime("%H:%M")

        if sunrise_dt and sunset_dt:
            diff_sec = (sunset_dt - sunrise_dt).total_seconds()
            if diff_sec < 0:
                day_length_str = "--:--"
            else:
                hrs = int(diff_sec // 3600)
                mins = int((diff_sec % 3600) // 60)
                day_length_str = f"{hrs}h {mins}m"
        else:
            day_length_str = "--:--"

        return sunrise_str, sunset_str, day_length_str

    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Sonnenzeiten (api.met.no): {e}")
        return "--:--", "--:--", "--:--"


def get_moon_times():
    """Holt Mondauf-/untergang und Phase."""
    try:
        local_tz = get_local_tz()
        now_local = datetime.now(local_tz)
        date_obj = now_local.date()
        date_str = date_obj.strftime("%Y-%m-%d")

        params = {
            "lat": LOCATION_LAT,
            "lon": LOCATION_LON,
            "date": date_str,
            "offset": met_offset_for_date(local_tz, date_obj),
        }

        headers = {"User-Agent": METNO_USER_AGENT}

        resp = requests.get(
            "https://api.met.no/weatherapi/sunrise/3.0/moon",
            params=params,
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        props = data.get("properties", {})

        moonrise_obj = props.get("moonrise")
        moonset_obj = props.get("moonset")

        moonrise_str = "--:--"
        moonset_str = "--:--"

        if isinstance(moonrise_obj, dict) and moonrise_obj.get("time"):
            dt = parse_iso_to_local(moonrise_obj["time"], local_tz)
            moonrise_str = dt.strftime("%H:%M")

        if isinstance(moonset_obj, dict) and moonset_obj.get("time"):
            dt = parse_iso_to_local(moonset_obj["time"], local_tz)
            moonset_str = dt.strftime("%H:%M")

        # Phase via Skyfield, mit Fallback
        try:
            phase_name, trend, illumination = get_moon_phase_skyfield()
        except Exception as e:
            logging.error(f"Skyfield Mondphase Fehler, Fallback aktiv: {e}")
            now_utc = datetime.now(timezone.utc)
            phase_name, trend, illumination = calculate_moon_phase_fallback(now_utc)

        return moonrise_str, moonset_str, phase_name, trend, illumination

    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Monddaten von api.met.no: {e}")
        try:
            now_utc = datetime.now(timezone.utc)
            phase_name, trend, illumination = calculate_moon_phase_fallback(now_utc)
        except Exception:
            phase_name, trend, illumination = "Unbekannt", "?", 0
        return "--:--", "--:--", phase_name, trend, illumination


# --------------------------
# Lokale Archivierung (JSONL Format - eine Zeile pro Messung)
# --------------------------
def ensure_local_archive_dir():
    """Stellt sicher, dass der lokale Archiv-Ordner existiert."""
    try:
        if not os.path.exists(LOCAL_ARCHIVE_DIR):
            os.makedirs(LOCAL_ARCHIVE_DIR)
            logging.info(f"Lokaler Archivordner erstellt: {LOCAL_ARCHIVE_DIR}")
        return True
    except Exception as e:
        logging.exception(f"Fehler beim Erstellen des Archivordners: {e}")
        return False


def get_yearly_archive_path():
    """Gibt den Pfad zur lokalen Jahresdatei zurück (JSONL Format)."""
    year = datetime.now().year
    return os.path.join(LOCAL_ARCHIVE_DIR, f"messwerte_{year}.jsonl")


# Lock für thread-sichere Archivierung
_archive_lock = threading.Lock()


def archive_measurement(data):
    """
    Archiviert eine Messung als einzelne JSON-Zeile (JSONL/append-only).
    Thread-sicher durch Lock.
    """
    if not ensure_local_archive_dir():
        return False
    
    try:
        filepath = get_yearly_archive_path()
        
        # Thread-sicher: Lock für Datei-Zugriff
        with _archive_lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        
        return True
        
    except Exception as e:
        logging.exception(f"Fehler bei Archivierung: {e}")
        return False


# --------------------------
# PV Tages-Daten Management (RAM-gepuffert)
# --------------------------
# Globaler Puffer für PV-Messungen (reduziert SD-Writes)
_pv_daily_buffer = {"date": None, "measurements": [], "dirty": False}
_pv_flush_interval = 20  # Alle 20 Messungen auf Disk schreiben
_pv_buffer_lock = threading.Lock()


def load_pv_daily_data():
    """Lädt die PV-Tagesdaten. Löscht alte Daten wenn neuer Tag."""
    global _pv_daily_buffer
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    with _pv_buffer_lock:
        # Bereits im Puffer und aktuell?
        if _pv_daily_buffer["date"] == today:
            return _pv_daily_buffer.copy()
        
        # Neuer Tag oder erster Aufruf - von Disk laden
        try:
            if os.path.exists(PV_DAILY_FILE):
                with open(PV_DAILY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Prüfen ob neuer Tag
                if data.get("date") == today:
                    _pv_daily_buffer = {
                        "date": today,
                        "measurements": data.get("measurements", []),
                        "dirty": False
                    }
                    return _pv_daily_buffer.copy()
            
            # Neuer Tag oder keine Datei - zurücksetzen
            logging.info(f"Neuer Tag erkannt, PV-Tagesdaten zurückgesetzt")
            _pv_daily_buffer = {"date": today, "measurements": [], "dirty": False}
            return _pv_daily_buffer.copy()
            
        except Exception as e:
            logging.exception(f"Fehler beim Laden der PV-Tagesdaten: {e}")
            _pv_daily_buffer = {"date": today, "measurements": [], "dirty": False}
            return _pv_daily_buffer.copy()


def save_pv_daily_data():
    """Speichert die PV-Tagesdaten auf Disk (nur wenn dirty). Thread-sicher."""
    global _pv_daily_buffer
    
    with _pv_buffer_lock:
        if not _pv_daily_buffer["dirty"]:
            return
        
        try:
            data = {
                "date": _pv_daily_buffer["date"],
                "measurements": _pv_daily_buffer["measurements"]
            }
            # Atomarer Write: erst temp, dann rename
            temp_file = PV_DAILY_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(temp_file, PV_DAILY_FILE)
            _pv_daily_buffer["dirty"] = False
        except Exception as e:
            logging.exception(f"Fehler beim Speichern der PV-Tagesdaten: {e}")


def add_pv_measurement(power):
    """Fügt eine PV-Messung hinzu. Ignoriert Nacht-Messungen (power <= 0)."""
    global _pv_daily_buffer
    
    try:
        # Nacht-Messungen ignorieren (keine 0-Werte in der Kurve)
        if power is None or power <= 0:
            with _pv_buffer_lock:
                return _pv_daily_buffer.get("measurements", []).copy()
        
        # Puffer initialisieren falls nötig
        load_pv_daily_data()
        
        # Konsistente Zeitzone verwenden
        now = datetime.now(get_local_tz())
        today = now.strftime("%Y-%m-%d")
        
        with _pv_buffer_lock:
            # Prüfen ob neuer Tag (nach Mitternacht)
            if _pv_daily_buffer["date"] != today:
                # Alten Puffer speichern falls dirty (Lock ist bereits gehalten)
                if _pv_daily_buffer["dirty"]:
                    try:
                        data = {
                            "date": _pv_daily_buffer["date"],
                            "measurements": _pv_daily_buffer["measurements"]
                        }
                        temp_file = PV_DAILY_FILE + ".tmp"
                        with open(temp_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False)
                        os.replace(temp_file, PV_DAILY_FILE)
                    except Exception:
                        pass
                _pv_daily_buffer = {"date": today, "measurements": [], "dirty": False}
            
            measurement = {
                "time": now.strftime("%H:%M:%S"),
                "power": power
            }
            _pv_daily_buffer["measurements"].append(measurement)
            _pv_daily_buffer["dirty"] = True
            
            # Memory-Schutz: Bei zu vielen Messungen Downsampling
            if len(_pv_daily_buffer["measurements"]) > MAX_PV_MEASUREMENTS_PER_DAY:
                # Behalte nur jeden 2. Wert
                _pv_daily_buffer["measurements"] = _pv_daily_buffer["measurements"][::2]
                logging.debug(f"PV-Messungen reduziert auf {len(_pv_daily_buffer['measurements'])}")
            
            # Nur alle N Messungen auf Disk schreiben
            should_flush = len(_pv_daily_buffer["measurements"]) % _pv_flush_interval == 0
            measurements_copy = _pv_daily_buffer["measurements"].copy()
        
        # Flush ausserhalb des Locks
        if should_flush:
            save_pv_daily_data()
        
        return measurements_copy
        
    except Exception as e:
        logging.exception(f"Fehler beim Hinzufügen der PV-Messung: {e}")
        with _pv_buffer_lock:
            return _pv_daily_buffer.get("measurements", []).copy()


# --------------------------
# Farb- und Hilfsfunktionen
# --------------------------
def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb_tuple):
    return '#{:02x}{:02x}{:02x}'.format(*rgb_tuple)


def interpolate_color(color1, color2, factor):
    c1 = hex_to_rgb(color1)
    c2 = hex_to_rgb(color2)
    r = int(c1[0] + (c2[0] - c1[0]) * factor)
    g = int(c1[1] + (c2[1] - c1[1]) * factor)
    b = int(c1[2] + (c2[2] - c1[2]) * factor)
    return rgb_to_hex((r, g, b))


def get_temp_gradient_color(temp):
    """
    Temperaturfarben nach Marc's Spezifikation:
    - Unter 0°C: Blau (je kälter desto stärker)
    - Bei 0°C: Hellblau, darüber sofort Weiss
    - 0-20°C: Weiss → Mittelgrau
    - 20-25°C: Angenehm = Grün (Grün NUR in diesem Bereich!)
    - 25-30°C: Orange
    - 30-35°C: Rot
    - Über 35°C: Magenta (gefährlich heiss)
    """
    if temp is None:
        return "#808080"

    # Farben definieren
    BLUE_DEEP = "#0040FF"      # Tiefes Blau für sehr kalt
    BLUE_LIGHT = "#80C0FF"     # Hellblau bei 0°C
    WHITE = "#FFFFFF"          # Weiss knapp über 0°C
    GRAY_MEDIUM = "#808080"    # Mittelgrau bei 20°C
    GREEN = "#00FF00"          # Reines Grün bei 22.5°C
    ORANGE = "#FF8000"         # Reines Orange ab 25°C
    RED = "#FF0000"            # Rot bei 32.5°C
    MAGENTA = "#FF00FF"        # Magenta ab 35°C

    # === BEREICH: Unter 0°C (Eisig - Blau) ===
    if temp <= 0:
        # Bei 0°C: Hellblau, je kälter desto stärker blau
        # -20°C = tiefes Blau, 0°C = Hellblau
        if temp <= -20:
            return BLUE_DEEP
        else:
            # Interpoliere von -20 (tiefblau) bis 0 (hellblau)
            factor = (temp + 20) / 20.0  # 0 bei -20°C, 1 bei 0°C
            return interpolate_color(BLUE_DEEP, BLUE_LIGHT, factor)

    # === BEREICH: 0-20°C (Kalt bis Unangenehm - Weiss zu Grau) ===
    # Direkt über 0°C sofort Weiss, dann langsam zu Grau
    if temp <= 20:
        # 0.1°C = fast reines Weiss, 20°C = Mittelgrau
        factor = temp / 20.0
        return interpolate_color(WHITE, GRAY_MEDIUM, factor)

    # === BEREICH: 20-25°C (Angenehm - Grün) ===
    # Grün darf NUR hier erscheinen!
    if temp <= 25:
        if temp <= 22.5:
            # 20.1°C = Grau-Grün, 22.5°C = reines Grün
            factor = (temp - 20) / 2.5
            return interpolate_color(GRAY_MEDIUM, GREEN, factor)
        else:
            # 22.5°C = reines Grün, 25°C = Grün-Orange
            factor = (temp - 22.5) / 2.5
            return interpolate_color(GREEN, ORANGE, factor)

    # === BEREICH: 25-30°C (Warm - Orange) ===
    if temp <= 30:
        # 25°C = Orange, 30°C = Orange-Rot
        factor = (temp - 25) / 5.0
        return interpolate_color(ORANGE, RED, factor)

    # === BEREICH: 30-35°C (Heiss - Rot) ===
    if temp <= 35:
        # 30°C = Rot, 35°C = Rot-Magenta
        factor = (temp - 30) / 5.0
        return interpolate_color(RED, MAGENTA, factor)

    # === BEREICH: Über 35°C (Gefährlich heiss - Magenta) ===
    return MAGENTA


def get_co2_gradient_color(co2):
    if co2 is None:
        return "#808080"
    if co2 <= 600:
        return "#00FF00"
    elif co2 <= 1000:
        factor = (co2 - 600) / 400
        return interpolate_color("#00FF00", "#FFFF00", factor)
    elif co2 <= 1500:
        factor = (co2 - 1000) / 500
        return interpolate_color("#FFFF00", "#FFA500", factor)
    elif co2 <= 2000:
        factor = (co2 - 1500) / 500
        return interpolate_color("#FFA500", "#FF0000", factor)
    else:
        return "#FF00FF"


def get_pv_power_color(power):
    if power is None or power <= 0:
        return "#404040"
    elif power < 1000:
        factor = power / 1000
        return interpolate_color("#404040", "#CC0000", factor)
    elif power < 3000:
        factor = (power - 1000) / 2000
        return interpolate_color("#CC0000", "#FFAA00", factor)
    elif power < 5000:
        factor = (power - 3000) / 2000
        return interpolate_color("#FFAA00", "#FFFF00", factor)
    elif power < 7000:
        factor = (power - 5000) / 2000
        return interpolate_color("#FFFF00", "#FFFFFF", factor)
    else:
        return "#FFFFFF"


# --------------------------
# Batterie-Logik (Farbig)
# --------------------------
def get_battery_color(battery):
    if battery is None:
        return COLORS['text_dim']
    
    if battery > 40:
        return COLORS['battery_good']      # Grün
    elif battery > 20:
        return COLORS['battery_medium']    # Gelb
    elif battery > 10:
        return COLORS['battery_low']       # Orange
    else:
        return COLORS['battery_critical']  # Rot


# ---------------------------------------------------
# Sonnen/Mond Icon-Zeichenfunktionen (radikal vereinfacht)
# ---------------------------------------------------
def draw_sun_icon(canvas, icon_type, color):
    """
    Zeichnet radikal vereinfachte Icons:
    - 'dawn': Pfeil hoch mit Querstrich darüber
    - 'sunrise': Pfeil hoch
    - 'noon': Ausgefüllter Kreis
    - 'sunset': Pfeil runter
    - 'dusk': Pfeil runter mit Querstrich darüber
    - 'day_length': Sonne mit Strahlen
    """
    canvas.delete("all")
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    if w < 5 or h < 5:
        return
    
    cx = w / 2
    cy = h / 2
    line_w = 1.5
    
    if icon_type == 'noon':
        # Ausgefüllter Kreis
        r = min(w, h) * 0.3
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
    
    elif icon_type == 'day_length':
        # Sonne mit Strahlen
        r = min(w, h) * 0.18
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
        ray_inner = r + 1
        ray_outer = r + 4
        for i in range(8):
            angle = i * math.pi / 4
            x1 = cx + ray_inner * math.cos(angle)
            y1 = cy + ray_inner * math.sin(angle)
            x2 = cx + ray_outer * math.cos(angle)
            y2 = cy + ray_outer * math.sin(angle)
            canvas.create_line(x1, y1, x2, y2, fill=color, width=line_w)
    
    elif icon_type in ['sunrise', 'sunset']:
        # Einfacher Pfeil
        arrow_up = (icon_type == 'sunrise')
        arrow_len = h * 0.7
        
        if arrow_up:
            tip_y = cy - arrow_len / 2
            base_y = cy + arrow_len / 2
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 4, tip_y + 5, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 4, tip_y + 5, cx, tip_y, fill=color, width=line_w)
        else:
            tip_y = cy + arrow_len / 2
            base_y = cy - arrow_len / 2
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 4, tip_y - 5, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 4, tip_y - 5, cx, tip_y, fill=color, width=line_w)
    
    elif icon_type in ['dawn', 'dusk']:
        # Pfeil mit Querstrich (Horizont)
        arrow_up = (icon_type == 'dawn')
        bar_y = cy - h * 0.25
        bar_w = w * 0.7
        arrow_len = h * 0.45
        
        # Querstrich (Horizont)
        canvas.create_line(cx - bar_w/2, bar_y, cx + bar_w/2, bar_y, fill=color, width=line_w)
        
        # Pfeil darunter
        if arrow_up:
            tip_y = bar_y + 3
            base_y = tip_y + arrow_len
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 3, tip_y + 4, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 3, tip_y + 4, cx, tip_y, fill=color, width=line_w)
        else:
            base_y = bar_y + 3
            tip_y = base_y + arrow_len
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 3, tip_y - 4, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 3, tip_y - 4, cx, tip_y, fill=color, width=line_w)


def draw_moon_icon(canvas, icon_type, color):
    """
    Zeichnet vereinfachte Mond-Icons:
    - 'rise': Pfeil hoch
    - 'set': Pfeil runter
    - 'phase': Nicht ausgefüllter Kreis
    """
    canvas.delete("all")
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    if w < 5 or h < 5:
        return
    
    cx = w / 2
    cy = h / 2
    line_w = 1.5
    
    if icon_type == 'phase':
        # Nicht ausgefüllter Kreis
        r = min(w, h) * 0.3
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=line_w)
    
    elif icon_type in ['rise', 'set']:
        # Einfacher Pfeil (wie Sonne)
        arrow_up = (icon_type == 'rise')
        arrow_len = h * 0.7
        
        if arrow_up:
            tip_y = cy - arrow_len / 2
            base_y = cy + arrow_len / 2
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 4, tip_y + 5, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 4, tip_y + 5, cx, tip_y, fill=color, width=line_w)
        else:
            tip_y = cy + arrow_len / 2
            base_y = cy - arrow_len / 2
            canvas.create_line(cx, base_y, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx - 4, tip_y - 5, cx, tip_y, fill=color, width=line_w)
            canvas.create_line(cx + 4, tip_y - 5, cx, tip_y, fill=color, width=line_w)


def draw_clock_icon(canvas, color):
    """Zeichnet Sonne mit Strahlen für Tageslänge."""
    draw_sun_icon(canvas, 'day_length', color)


def draw_battery(canvas, battery, blink_state=False):
    """
    Zeichnet eine liegende Batterie als Vektorgrafik.
    100-40%: Grün, 40-20%: Gelb, 20-10%: Orange, 10-5%: Rot, <5%: Rot blinkend
    """
    canvas.delete("all")
    
    if battery is None:
        return
    
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    if w < 10 or h < 10:
        return
    
    # Batterie-Dimensionen
    batt_width = min(w - 6, 44)
    batt_height = min(h - 4, 16)
    x_start = (w - batt_width - 4) / 2
    y_start = (h - batt_height) / 2
    
    # Farbe basierend auf Ladestand
    if battery > 40:
        fill_color = COLORS['battery_good']      # Grün
    elif battery > 20:
        fill_color = COLORS['battery_medium']    # Gelb
    elif battery > 10:
        fill_color = COLORS['battery_low']       # Orange
    else:
        fill_color = COLORS['battery_critical']  # Rot
    
    # Bei <5%: Blinken (nur zeichnen wenn blink_state True)
    if battery < 5 and not blink_state:
        fill_color = COLORS['card_bg']  # Ausblenden beim Blinken
    
    # Batterie-Körper (Umriss)
    canvas.create_rectangle(
        x_start, y_start,
        x_start + batt_width, y_start + batt_height,
        outline=COLORS['text_dim'],
        width=1
    )
    
    # Batterie-Pol (rechts)
    pole_width = 4
    pole_height = batt_height * 0.5
    pole_y = y_start + (batt_height - pole_height) / 2
    canvas.create_rectangle(
        x_start + batt_width, pole_y,
        x_start + batt_width + pole_width, pole_y + pole_height,
        fill=COLORS['text_dim'],
        outline=""
    )
    
    # Füllstand
    fill_width = (batt_width - 4) * (battery / 100.0)
    if fill_width > 0:
        canvas.create_rectangle(
            x_start + 2, y_start + 2,
            x_start + 2 + fill_width, y_start + batt_height - 2,
            fill=fill_color,
            outline=""
        )


# ---------------------------------------------------------
# Dashboard-Klasse für 7 Zoll Display - Redesign
# ---------------------------------------------------------
class Dashboard7inchRedesigned:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Smart Home Dashboard")

        width, height = 1024, 600
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        logging.info(f"Virtuelle Bildschirmgröße: {screen_w}x{screen_h}")

        x = screen_w - width
        y = 0

        logging.info(f"Nutze Dashboard-Größe: {width}x{height} an Position {x},{y}")
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.root.overrideredirect(True)
        self.root.attributes("-fullscreen", False)
        self.root.configure(bg=COLORS['bg'])
        self.root.configure(cursor="none")
        self.root.resizable(False, False)

        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<Button-3>", lambda e: self.root.destroy())

        self.pressure_history = self.load_pressure_history()

        # Pro Stunde genau einmal speichern
        self.last_saved_pressure_hour = None

        self.setup_layout()

        self.current_rain_1h = 0
        self.current_rain_24h = 0
        self.current_pressure = None
        self.noise_value = None
        self.outdoor_temperature = None  # NEU: Aussentemperatur für Barograph

        self.digits = []
        self.colons = []
        self.date_digits = []   # NEU: Datum-Ziffern
        self.date_colons = []   # NEU: Datum-Doppelpunkte

        self.astronomy_timer = None
        self.sun_position_timer = None

        self.pv_window_timer = None
        self.pv_query_timer = None
        self.pv_followup_timer = None
        
        # Intelligente PV-Abfrage: Zeiten und Intervalle
        self.pv_civil_dawn = None      # Beginn zivile Morgendämmerung
        self.pv_sunrise = None         # Sonnenaufgang
        self.pv_sunset = None          # Sonnenuntergang
        self.pv_civil_dusk = None      # Ende zivile Abenddämmerung
        
        # Budget und Zähler
        self.pv_max_queries = 280      # SolarEdge Limit: 300, Reserve: 20
        self.pv_queries_today = 0      # Zähler für heute
        self.pv_query_date = None      # Datum für Zähler-Reset
        
        # Berechnete Intervalle (werden täglich neu berechnet)
        self.pv_interval_ramp = None   # Intervall während Dämmerung
        self.pv_interval_core = None   # Intervall während Kernzeit
        self.last_pv_power = None
        
        # NEU: PV Tagesdaten für Grafik
        self.pv_daily_measurements = load_pv_daily_data().get("measurements", [])
        
        # NEU: Mondphase für Grafik
        self.moon_illumination = 0
        self.moon_trend = "↑"
        self.moon_phase_name = ""
        
        # Sonnenstand für Animation
        self.sun_elevation = 0           # Aktuelle Sonnenhöhe in Grad
        self.sun_max_elevation = 45      # Max. Höhe heute (wird aktualisiert)
        
        # Mondstand für Animation
        self.moon_elevation = 0          # Aktuelle Mondhöhe in Grad
        self.moon_max_elevation = 45     # Max. Höhe heute (wird aktualisiert)
        
        # Batterie-Blinken für <5%
        self.battery_blink_state = True
        
        # Timer-Debouncing für Resize (verhindert Timer-Akkumulation)
        self._pending_redraws = {}
        
        # Health-Check Tracking
        self._last_netatmo_update = None
        self._last_pv_update = None
        self._netatmo_retry_count = 0
        
        # PV Fehler-Tracking (Fix #1)
        self.pv_attempts_today = 0           # Alle Versuche (auch fehlgeschlagene)
        self.pv_consecutive_failures = 0     # Aufeinanderfolgende Fehler
        
        # Threading Lock für Netatmo (verhindert parallele Fetches)
        self._netatmo_fetch_lock = threading.Lock()
        
        # Timer-Referenz für sauberes Scheduling
        self._netatmo_timer = None
        
        # Shutdown-Guard (Fix #4)
        self._is_shutting_down = False
        
        # NEU: Letzte Netatmo-Daten für Archivierung
        self.last_netatmo_data = {}
        
        logging.info("Dashboard GUI erstellt")

    def load_pressure_history(self):
        """Lädt Druckhistorie. Migriert alte [ts, p] zu [ts, p, None]."""
        try:
            with open(PRESSURE_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            dq = deque(maxlen=72)
            for entry in data:
                # Migration: alte Einträge [timestamp, pressure] -> [timestamp, pressure, None]
                if isinstance(entry, list) and len(entry) == 2:
                    entry = [entry[0], entry[1], None]
                dq.append(entry)
            return dq
        except Exception:
            logging.info("Keine Druckhistorie gefunden, erstelle neue")
            return deque(maxlen=72)

    def save_pressure_history(self):
        try:
            data = list(self.pressure_history)
            # Atomarer Write: erst temp, dann replace
            temp_file = PRESSURE_HISTORY_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(temp_file, PRESSURE_HISTORY_FILE)
        except Exception as e:
            logging.exception(f"Fehler beim Speichern der Druckhistorie: {e}")

    # ---------------------------------------------------
    # CACHE MECHANISMUS (NEU)
    # ---------------------------------------------------
    def load_cached_data(self):
        """Lädt den letzten bekannten Zustand und aktualisiert die Anzeige sofort."""
        if not os.path.exists(CACHE_FILE):
            logging.info("Kein Cache gefunden. Starte mit leeren Werten.")
            return

        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)

            logging.info("Cache geladen. Stelle letzte bekannte Werte wieder her.")

            # Netatmo Daten wiederherstellen (ohne Archivierung/Health-Tracking)
            if "netatmo" in cache:
                self._apply_netatmo_data(cache["netatmo"], from_cache=True)

            # Astro Daten wiederherstellen
            if "astro" in cache:
                self._apply_astronomy(cache["astro"])

            # PV Daten wiederherstellen
            if "pv" in cache:
                pv = cache["pv"]
                self.update_pv_labels(
                    pv.get("current"),
                    pv.get("daily"),
                    pv.get("monthly"),
                    pv.get("yearly")
                )

        except Exception as e:
            logging.error(f"Fehler beim Laden des Caches: {e}")

    def save_to_cache(self, key, data):
        """Speichert einen Teilbereich der Daten in die JSON-Datei (atomar)."""
        try:
            cache = {}
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    try:
                        cache = json.load(f)
                    except json.JSONDecodeError:
                        cache = {}

            cache[key] = data

            # Atomarer Write: erst temp, dann replace
            temp_file = CACHE_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(temp_file, CACHE_FILE)

        except Exception as e:
            logging.exception(f"Fehler beim Schreiben des Caches: {e}")

    def setup_layout(self):
        main_frame = tk.Frame(self.root, bg=COLORS['bg'])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        modules_frame = tk.Frame(main_frame, bg=COLORS['bg'], height=220)
        modules_frame.pack(fill="x", pady=(0, 3))
        modules_frame.pack_propagate(False)

        self.module_frames = []
        self.module_labels = []

        for i in range(4):
            module = tk.Frame(
                modules_frame,
                bg=COLORS['card_bg'],
                highlightbackground=COLORS['border'],
                highlightthickness=0
            )
            module.pack(side="left", fill="both", expand=True, padx=2)

            content = tk.Frame(module, bg=COLORS['card_bg'])
            content.pack(expand=True, pady=2)

            name_label = tk.Label(
                content, text=f"Modul {i+1}",
                font=("Arial", 16, "bold"),
                fg=COLORS['text_secondary'], bg=COLORS['card_bg']
            )
            name_label.pack(pady=(2, 0))

            temp_label = tk.Label(
                content, text="--.-°",
                font=("Arial", 64, "bold"),
                fg=COLORS['text_primary'], bg=COLORS['card_bg']
            )
            temp_label.pack(pady=(0, 2))

            minmax_label = tk.Label(
                content, text="↓--° ↑--°",
                font=("Arial", 14, "bold"),
                fg=COLORS['text_primary'], bg=COLORS['card_bg']
            )
            minmax_label.pack(pady=(0, 1))

            stats_frame = tk.Frame(content, bg=COLORS['card_bg'])
            stats_frame.pack(pady=(1, 0))

            co2_label = tk.Label(
                stats_frame, text="CO₂: ---",
                font=("Arial", 16, "bold"),
                fg=COLORS['text_secondary'], bg=COLORS['card_bg']
            )
            co2_label.pack(side="left", padx=(0, 10))

            humidity_label = tk.Label(
                stats_frame, text="---%",
                font=("Arial", 16),
                fg=COLORS['accent_blue'], bg=COLORS['card_bg']
            )
            humidity_label.pack(side="left")

            # Battery Canvas für Vektorgrafik
            battery_canvas = tk.Canvas(
                content,
                bg=COLORS['card_bg'],
                highlightthickness=0,
                width=50,
                height=20
            )
            battery_canvas.pack(pady=(2, 2))

            self.module_labels.append({
                "name": name_label,
                "temperature": temp_label,
                "minmax": minmax_label,
                "co2": co2_label,
                "humidity": humidity_label,
                "battery_canvas": battery_canvas
            })
            self.module_frames.append(module)

        barograph_frame = tk.Frame(
            main_frame, bg=COLORS['card_bg'], height=210,
            highlightbackground=COLORS['border'], highlightthickness=0
        )
        barograph_frame.pack(fill="x", pady=3)
        barograph_frame.pack_propagate(False)

        baro_header = tk.Frame(barograph_frame, bg=COLORS['card_bg'])
        baro_header.pack(fill="x", padx=10, pady=(5, 0))

        # NEU: Luftdruck-Label links mit Wert daneben
        tk.Label(
            baro_header, text="Luftdruck",
            font=("Arial", 20),
            fg=COLORS['text_primary'], bg=COLORS['card_bg']
        ).pack(side="left")

        self.pressure_label = tk.Label(
            baro_header, text=" ---- hPa",
            font=("Arial", 20, "bold"),
            fg=COLORS['text_primary'], bg=COLORS['card_bg']
        )
        self.pressure_label.pack(side="left")

        self.barograph_canvas = tk.Canvas(
            barograph_frame, bg=COLORS['card_bg'],
            highlightthickness=0, height=150
        )
        self.barograph_canvas.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        bottom_frame = tk.Frame(main_frame, bg=COLORS['bg'], height=120)
        bottom_frame.pack(fill="both", expand=True)
        bottom_frame.pack_propagate(False)

        # NEU: 4 Spalten - PV-Grafik bekommt mehr Platz
        bottom_frame.grid_columnconfigure(0, weight=2, uniform="bottom")  # PV Daten
        bottom_frame.grid_columnconfigure(1, weight=4, uniform="bottom")  # PV Grafik (breiter)
        bottom_frame.grid_columnconfigure(2, weight=2, uniform="bottom")  # Sonne
        bottom_frame.grid_columnconfigure(3, weight=2, uniform="bottom")  # Mond
        bottom_frame.grid_rowconfigure(0, weight=1)

        # === PV DATEN (Spalte 0) ===
        pv_frame = tk.Frame(
            bottom_frame, bg=COLORS['card_bg'],
            highlightbackground=COLORS['border'], highlightthickness=0
        )
        pv_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2), pady=0)

        pv_content = tk.Frame(pv_frame, bg=COLORS['card_bg'])
        pv_content.pack(expand=True)

        tk.Label(
            pv_content, text="Stromproduktion",
            font=("Arial", 18),
            fg=COLORS['text_primary'], bg=COLORS['card_bg']
        ).pack(pady=(5, 2))

        self.pv_power_label = tk.Label(
            pv_content, text="---- W",
            font=("Arial", 36, "bold"),
            fg="#FFFF00", bg=COLORS['card_bg']
        )
        self.pv_power_label.pack()

        self.pv_stats_label = tk.Label(
            pv_content, text="T: -- • M: -- • J: -- kWh",
            font=("Arial", 11),
            fg=COLORS['text_secondary'], bg=COLORS['card_bg']
        )
        self.pv_stats_label.pack(pady=(2, 5))

        # === PV GRAFIK (Spalte 1 - NEU) ===
        pv_graph_frame = tk.Frame(
            bottom_frame, bg=COLORS['card_bg'],
            highlightbackground=COLORS['border'], highlightthickness=0
        )
        pv_graph_frame.grid(row=0, column=1, sticky="nsew", padx=2, pady=0)

        self.pv_graph_canvas = tk.Canvas(
            pv_graph_frame,
            bg=COLORS['card_bg'],
            highlightthickness=0
        )
        self.pv_graph_canvas.pack(expand=True, fill="both", padx=3, pady=3)

        # === SONNE (Spalte 2) - ersetzt Uhr ===
        sun_field = tk.Frame(
            bottom_frame, bg=COLORS['card_bg'],
            highlightbackground=COLORS['border'], highlightthickness=0
        )
        sun_field.grid(row=0, column=2, sticky="nsew", padx=2, pady=0)

        sun_content = tk.Frame(sun_field, bg=COLORS['card_bg'])
        sun_content.pack(expand=True, fill="both", padx=5, pady=3)

        # Sonnen-Canvas für Animation (links)
        self.sun_canvas = tk.Canvas(
            sun_content,
            bg=COLORS['card_bg'],
            highlightthickness=0,
            width=55,
            height=95
        )
        self.sun_canvas.pack(side="left", padx=(0, 5))

        # Sonnen-Info (rechts) - Icons mit Zeiten
        sun_info_frame = tk.Frame(sun_content, bg=COLORS['card_bg'])
        sun_info_frame.pack(side="left", fill="both", expand=True)

        # Dämmerungsfarbe (Orange)
        twilight_color = "#FF8800"
        # Sonnenauf-/untergang (tiefes Orange)
        sunrise_color = "#FF6600"
        # Sonnenhöchststand (Gelb)
        noon_color = COLORS['sun']  # #FFD700
        
        icon_size = 18  # Icon-Größe
        indent = 25    # Einrückung für sekundäre Zeiten

        # Helper: Erstellt eine Zeile mit Icon-Canvas und Zeit-Label
        def create_sun_row(parent, icon_type, color, indented=False):
            row = tk.Frame(parent, bg=COLORS['card_bg'])
            row.pack(anchor="w", pady=1)
            # Einrückung für sekundäre Zeiten (Dämmerung, Höchststand)
            if indented:
                spacer = tk.Frame(row, width=indent, bg=COLORS['card_bg'])
                spacer.pack(side="left")
            icon_canvas = tk.Canvas(row, width=icon_size, height=icon_size, 
                                   bg=COLORS['card_bg'], highlightthickness=0)
            icon_canvas.pack(side="left", padx=(0, 3))
            label = tk.Label(row, text="--:--", font=("Arial", 13, "bold"),
                           fg=color, bg=COLORS['card_bg'])
            label.pack(side="left")
            return icon_canvas, label, icon_type, color

        # Dämmerungsbeginn (eingerückt)
        self.dawn_icon, self.civil_dawn_label, _, _ = create_sun_row(
            sun_info_frame, 'dawn', twilight_color, indented=True)
        
        # Sonnenaufgang
        self.sunrise_icon, self.sunrise_label, _, _ = create_sun_row(
            sun_info_frame, 'sunrise', sunrise_color)
        
        # Sonnenhöchststand (eingerückt)
        self.noon_icon, self.solar_noon_label, _, _ = create_sun_row(
            sun_info_frame, 'noon', noon_color, indented=True)
        
        # Sonnenuntergang
        self.sunset_icon, self.sunset_label, _, _ = create_sun_row(
            sun_info_frame, 'sunset', sunrise_color)
        
        # Dämmerungsende (eingerückt)
        self.dusk_icon, self.civil_dusk_label, _, _ = create_sun_row(
            sun_info_frame, 'dusk', twilight_color, indented=True)
        
        # Tageslänge (mit Uhr-Icon)
        day_row = tk.Frame(sun_info_frame, bg=COLORS['card_bg'])
        day_row.pack(anchor="w", pady=1)
        self.day_icon = tk.Canvas(day_row, width=icon_size, height=icon_size,
                                  bg=COLORS['card_bg'], highlightthickness=0)
        self.day_icon.pack(side="left", padx=(0, 3))
        self.day_length_label = tk.Label(day_row, text="--:--", font=("Arial", 13, "bold"),
                                        fg="#FFFFFF", bg=COLORS['card_bg'])
        self.day_length_label.pack(side="left")
        
        # Icons nach Layout-Update zeichnen
        def draw_sun_icons():
            draw_sun_icon(self.dawn_icon, 'dawn', twilight_color)
            draw_sun_icon(self.sunrise_icon, 'sunrise', sunrise_color)
            draw_sun_icon(self.noon_icon, 'noon', noon_color)
            draw_sun_icon(self.sunset_icon, 'sunset', sunrise_color)
            draw_sun_icon(self.dusk_icon, 'dusk', twilight_color)
            draw_clock_icon(self.day_icon, "#FFFFFF")
        
        self.root.after(100, draw_sun_icons)

        # === MOND (Spalte 3) - volles Feld ===
        moon_field = tk.Frame(
            bottom_frame, bg=COLORS['card_bg'],
            highlightbackground=COLORS['border'], highlightthickness=0
        )
        moon_field.grid(row=0, column=3, sticky="nsew", padx=(2, 0), pady=0)

        moon_content = tk.Frame(moon_field, bg=COLORS['card_bg'])
        moon_content.pack(expand=True, fill="both", padx=5, pady=3)

        # Mond-Canvas für Animation (links) - größer
        self.moon_canvas = tk.Canvas(
            moon_content,
            bg=COLORS['card_bg'],
            highlightthickness=0,
            width=55,
            height=95
        )
        self.moon_canvas.pack(side="left", padx=(0, 5))

        # Mond-Info (rechts) - Icons mit Zeiten, vertikal zentriert
        moon_info_outer = tk.Frame(moon_content, bg=COLORS['card_bg'])
        moon_info_outer.pack(side="left", fill="both", expand=True)
        
        # Innerer Frame für vertikale Zentrierung
        moon_info_frame = tk.Frame(moon_info_outer, bg=COLORS['card_bg'])
        moon_info_frame.place(relx=0, rely=0.5, anchor="w")

        moon_color = COLORS['moon_light']  # Gelblicher Mond
        icon_size = 18

        # Mondaufgang
        rise_row = tk.Frame(moon_info_frame, bg=COLORS['card_bg'])
        rise_row.pack(anchor="w", pady=1)
        self.moonrise_icon = tk.Canvas(rise_row, width=icon_size, height=icon_size,
                                       bg=COLORS['card_bg'], highlightthickness=0)
        self.moonrise_icon.pack(side="left", padx=(0, 3))
        self.moonrise_label = tk.Label(rise_row, text="--:--", font=("Arial", 13, "bold"),
                                      fg=moon_color, bg=COLORS['card_bg'])
        self.moonrise_label.pack(side="left")

        # Monduntergang
        set_row = tk.Frame(moon_info_frame, bg=COLORS['card_bg'])
        set_row.pack(anchor="w", pady=1)
        self.moonset_icon = tk.Canvas(set_row, width=icon_size, height=icon_size,
                                      bg=COLORS['card_bg'], highlightthickness=0)
        self.moonset_icon.pack(side="left", padx=(0, 3))
        self.moonset_label = tk.Label(set_row, text="--:--", font=("Arial", 13, "bold"),
                                     fg=moon_color, bg=COLORS['card_bg'])
        self.moonset_label.pack(side="left")

        # Beleuchtungsgrad
        phase_row = tk.Frame(moon_info_frame, bg=COLORS['card_bg'])
        phase_row.pack(anchor="w", pady=1)
        self.moon_phase_icon = tk.Canvas(phase_row, width=icon_size, height=icon_size,
                                         bg=COLORS['card_bg'], highlightthickness=0)
        self.moon_phase_icon.pack(side="left", padx=(0, 3))
        self.moon_percent_label = tk.Label(phase_row, text="--%", font=("Arial", 13, "bold"),
                                          fg=moon_color, bg=COLORS['card_bg'])
        self.moon_percent_label.pack(side="left")

        # Icons nach Layout-Update zeichnen
        def draw_moon_icons():
            draw_moon_icon(self.moonrise_icon, 'rise', moon_color)
            draw_moon_icon(self.moonset_icon, 'set', moon_color)
            draw_moon_icon(self.moon_phase_icon, 'phase', moon_color)
        
        self.root.after(100, draw_moon_icons)

    # ---------------------------------------------------
    # Timer-Debouncing Helper
    # ---------------------------------------------------
    def _debounced_redraw(self, canvas_name, draw_func):
        """
        Verhindert Timer-Akkumulation bei schnellem Resize.
        Cancelt vorherige Timer für dasselbe Canvas.
        """
        if canvas_name in self._pending_redraws:
            try:
                self.root.after_cancel(self._pending_redraws[canvas_name])
            except Exception:
                pass
        self._pending_redraws[canvas_name] = self.root.after(
            INTERVALS['redraw_debounce'], draw_func
        )

    # ---------------------------------------------------
    # Barograph mit Temperaturkurve (NEU)
    # ---------------------------------------------------
    def draw_barograph(self):
        try:
            self.barograph_canvas.delete("all")

            width = self.barograph_canvas.winfo_width()
            height = self.barograph_canvas.winfo_height()
            if width < 10 or height < 10:
                self._debounced_redraw("barograph", self.draw_barograph)
                return

            left_margin = 40
            right_margin = 40  # NEU: Platz für Temperatur-Skala rechts
            top_margin = 10
            bottom_margin = 25

            graph_width = width - left_margin - right_margin
            graph_height = height - top_margin - bottom_margin

            # Daten extrahieren
            pressures = []
            temperatures = []
            for entry in self.pressure_history:
                if len(entry) >= 2 and isinstance(entry[1], (int, float)):
                    pressures.append(entry[1])
                if len(entry) >= 3 and entry[2] is not None and isinstance(entry[2], (int, float)):
                    temperatures.append(entry[2])

            # ===== DRUCK-SKALA (links) =====
            if pressures:
                min_pressure = min(pressures)
                max_pressure = max(pressures)

                low = min(min_pressure, NORMAL_PRESSURE)
                high = max(max_pressure, NORMAL_PRESSURE)

                if low == high:
                    high = low + 1.0

                raw_range = high - low
                padding = max(raw_range * 0.1, 1.0)

                scale_min = int(math.floor((low - padding) / 5.0) * 5)
                scale_max = int(math.ceil((high + padding) / 5.0) * 5)
            else:
                scale_min = 1005
                scale_max = 1025

            scale_range = scale_max - scale_min
            if scale_range <= 0:
                scale_range = 1

            # ===== TEMPERATUR-SKALA (rechts, dynamisch) =====
            if temperatures:
                t_min = min(temperatures)
                t_max = max(temperatures)
                if t_min == t_max:
                    t_min -= 1
                    t_max += 1
                t_scale_min = int(math.floor(t_min))
                t_scale_max = int(math.ceil(t_max))
            else:
                t_scale_min = 0
                t_scale_max = 20

            t_scale_range = t_scale_max - t_scale_min
            if t_scale_range <= 0:
                t_scale_range = 1

            # ===== ACHSEN ZEICHNEN =====
            # Linke Y-Achse (Druck)
            self.barograph_canvas.create_line(
                left_margin, top_margin,
                left_margin, top_margin + graph_height,
                fill=COLORS['text_dim'], width=1
            )

            # Rechte Y-Achse (Temperatur)
            self.barograph_canvas.create_line(
                left_margin + graph_width, top_margin,
                left_margin + graph_width, top_margin + graph_height,
                fill=COLORS['text_dim'], width=1
            )

            # ===== DRUCK-SKALA BESCHRIFTUNG (links, farbcodiert) =====
            for p in range(scale_min, scale_max + 1, 5):
                y = top_margin + graph_height - ((p - scale_min) / scale_range * graph_height)
                
                # Farbe: Cyan für Hochdruck, Lila für Tiefdruck
                label_color = COLORS['pressure_high'] if p >= NORMAL_PRESSURE else COLORS['pressure_low']

                self.barograph_canvas.create_text(
                    left_margin - 5, y,
                    text=str(p),
                    font=("Arial", 11, "bold"),
                    fill=label_color,
                    anchor="e"
                )

            # Normaldruck-Linie (wichtige Referenz behalten)
            if scale_min <= NORMAL_PRESSURE <= scale_max:
                y_normal = top_margin + graph_height - ((NORMAL_PRESSURE - scale_min) / scale_range * graph_height)
                self.barograph_canvas.create_line(
                    left_margin, y_normal,
                    left_margin + graph_width, y_normal,
                    fill=COLORS['text_dim'],
                    width=1, dash=(2, 4)
                )
            else:
                y_normal = None

            # ===== TEMPERATUR-SKALA BESCHRIFTUNG (rechts) =====
            t_step = 1 if t_scale_range <= 5 else (2 if t_scale_range <= 15 else 5)
            for t in range(int(t_scale_min), int(t_scale_max) + 1, t_step):
                y = top_margin + graph_height - ((t - t_scale_min) / t_scale_range * graph_height)
                color = get_temp_gradient_color(t)
                self.barograph_canvas.create_text(
                    left_margin + graph_width + 5, y,
                    text=f"{t}°",
                    font=("Arial", 11, "bold"),
                    fill=color,
                    anchor="w"
                )

            if len(self.pressure_history) < 1:
                return

            num_slots = 72
            x_step = graph_width / max(1, (num_slots - 1))

            history_len = len(self.pressure_history)
            offset = num_slots - history_len
            if offset < 0:
                offset = 0

            # ===== ZEITACHSE (nur Beschriftung, keine vertikalen Linien) =====
            for idx, entry in enumerate(self.pressure_history):
                try:
                    ts_str = entry[0]
                    dt = datetime.fromisoformat(ts_str)
                except Exception:
                    continue

                x = left_margin + (idx + offset) * x_step

                if dt.hour == 0:
                    text = f"{dt.day}.{dt.month:02d}"
                    self.barograph_canvas.create_text(
                        x, height - 5,
                        text=text,
                        font=("Arial", 10, "bold"),
                        fill="#FFFFFF"
                    )

            # ===== DRUCK-KURVE ZEICHNEN =====
            points = []
            for idx, entry in enumerate(self.pressure_history):
                if len(entry) < 2:
                    continue
                pressure = entry[1]
                if not isinstance(pressure, (int, float)):
                    continue

                x = left_margin + (idx + offset) * x_step
                y = top_margin + graph_height - ((pressure - scale_min) / scale_range * graph_height)
                points.append((x, y))

            if len(points) >= 2:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i + 1]

                    if y_normal is not None:
                        mid_y = (y1 + y2) / 2
                        if mid_y > y_normal:
                            color = COLORS['pressure_low']
                        else:
                            color = COLORS['pressure_high']
                    else:
                        mid_y = (y1 + y2) / 2
                        rel = (mid_y - top_margin) / graph_height
                        rel = min(max(rel, 0.0), 1.0)
                        pressure_mid = scale_min + (1 - rel) * scale_range
                        color = COLORS['pressure_low'] if pressure_mid < NORMAL_PRESSURE else COLORS['pressure_high']

                    self.barograph_canvas.create_line(
                        x1, y1, x2, y2,
                        fill=color,
                        width=3
                    )

                for x, y in points:
                    if y_normal is not None:
                        point_color = COLORS['pressure_low'] if y > y_normal else COLORS['pressure_high']
                    else:
                        rel = (y - top_margin) / graph_height
                        rel = min(max(rel, 0.0), 1.0)
                        pressure_value = scale_min + (1 - rel) * scale_range
                        point_color = COLORS['pressure_low'] if pressure_value < NORMAL_PRESSURE else COLORS['pressure_high']

                    self.barograph_canvas.create_oval(
                        x - 2, y - 2, x + 2, y + 2,
                        fill=point_color,
                        outline=""
                    )

            # ===== TEMPERATUR-KURVE ZEICHNEN (NEU) =====
            temp_points = []
            for idx, entry in enumerate(self.pressure_history):
                if len(entry) < 3 or entry[2] is None:
                    continue
                temp = entry[2]
                if not isinstance(temp, (int, float)):
                    continue

                x = left_margin + (idx + offset) * x_step
                y = top_margin + graph_height - ((temp - t_scale_min) / t_scale_range * graph_height)
                temp_points.append((x, y, temp))

            if len(temp_points) >= 2:
                # Linien mit Temperatur-abhängiger Farbe
                for i in range(len(temp_points) - 1):
                    x1, y1, t1 = temp_points[i]
                    x2, y2, t2 = temp_points[i + 1]
                    avg_temp = (t1 + t2) / 2
                    color = get_temp_gradient_color(avg_temp)
                    self.barograph_canvas.create_line(
                        x1, y1, x2, y2,
                        fill=color,
                        width=2
                    )

        except Exception as e:
            logging.error(f"Fehler in draw_barograph: {e}")

    # ---------------------------------------------------
    # Dynamische Sonnengrafik mit Dämmerungsschimmern
    # ---------------------------------------------------
    def draw_sun(self):
        """
        Zeichnet die Sonne mit Position basierend auf aktueller Elevation.
        Horizont ist am unteren Rand des Canvas.
        
        Bei Elevation >= 0: Sonne sichtbar (wandert nach oben)
        Bei Elevation < 0: Dämmerungsschimmern am Horizont
          - Zivile Dämmerung (0° bis -6°): Helles Orange
          - Nautische Dämmerung (-6° bis -12°): Dunkles Orange/Rot
          - Astronomische Dämmerung (-12° bis -18°): Schwaches Dunkelrot
          - Nacht (< -18°): Nichts
        """
        try:
            self.sun_canvas.delete("all")

            w = self.sun_canvas.winfo_width()
            h = self.sun_canvas.winfo_height()
            if w < 10 or h < 10:
                self._debounced_redraw("sun", self.draw_sun)
                return

            sun_radius = 12
            cx = w / 2
            
            # Dämmerungsphasen-Grenzen (Grad unter Horizont)
            CIVIL_END = -6
            NAUTICAL_END = -12
            ASTRONOMICAL_END = -18
            
            elevation = self.sun_elevation
            
            # === NACHT: Nichts zeichnen ===
            if elevation < ASTRONOMICAL_END:
                return
            
            # === DÄMMERUNG: Schimmern am Horizont ===
            if elevation < 0:
                self._draw_twilight_glow(w, h, cx, elevation)
                return
            
            # === TAG: Sonne zeichnen ===
            margin_top = 5
            usable_height = h - margin_top - sun_radius
            
            max_elev = max(self.sun_max_elevation, 1)
            normalized = elevation / max_elev
            normalized = max(0, min(1, normalized))
            
            cy = h - (normalized * usable_height)
            
            # Farbe je nach Höhe
            if elevation < 10:
                sun_color = "#FF6600"  # Orange (tief)
            else:
                sun_color = COLORS['sun']  # Gelb
            
            # Sonnenstrahlen
            ray_length = 6
            num_rays = 8
            for i in range(num_rays):
                angle = (i * 360 / num_rays) * math.pi / 180
                x1 = cx + (sun_radius + 2) * math.cos(angle)
                y1 = cy + (sun_radius + 2) * math.sin(angle)
                x2 = cx + (sun_radius + ray_length) * math.cos(angle)
                y2 = cy + (sun_radius + ray_length) * math.sin(angle)
                
                if y1 < h and y2 < h:
                    self.sun_canvas.create_line(x1, y1, x2, y2, fill=sun_color, width=2)
                elif min(y1, y2) < h:
                    if y1 > h: y1 = h
                    if y2 > h: y2 = h
                    self.sun_canvas.create_line(x1, y1, x2, y2, fill=sun_color, width=2)
            
            # Sonnenscheibe
            sun_top = cy - sun_radius
            sun_bottom = cy + sun_radius
            
            if sun_bottom > h:
                # Teilweise unter Horizont
                visible_height = h - sun_top
                if visible_height > 0:
                    below = sun_bottom - h
                    ratio = below / (2 * sun_radius)
                    ratio = max(0, min(1, ratio))
                    
                    if ratio < 1:
                        extent = 360 * (1 - ratio)
                        start = -90 + (180 * ratio)
                        self.sun_canvas.create_arc(
                            cx - sun_radius, sun_top,
                            cx + sun_radius, sun_bottom,
                            start=start, extent=extent,
                            fill=sun_color, outline=""
                        )
            else:
                # Volle Scheibe
                self.sun_canvas.create_oval(
                    cx - sun_radius, sun_top,
                    cx + sun_radius, sun_bottom,
                    fill=sun_color, outline=""
                )

        except Exception as e:
            logging.error(f"Fehler beim Zeichnen der Sonne: {e}")
    
    def _draw_twilight_glow(self, w, h, cx, elevation):
        """
        Zeichnet das Dämmerungsschimmern am Horizont.
        Konzentrische Halbkreise mit abnehmender Intensität.
        """
        # Dämmerungsphasen
        CIVIL_END = -6
        NAUTICAL_END = -12
        ASTRONOMICAL_END = -18
        
        # Bestimme Phase und Intensität (verstärkt für kontrastarme Displays)
        if elevation >= CIVIL_END:
            # Zivile Dämmerung: 0° bis -6°
            phase_progress = abs(elevation) / 6  # 0 bei 0°, 1 bei -6°
            base_color = "#FF8800"  # Helles Orange
            glow_intensity = 1.0 - (phase_progress * 0.2)  # 1.0 bis 0.8
            max_radius = 20
        elif elevation >= NAUTICAL_END:
            # Nautische Dämmerung: -6° bis -12°
            phase_progress = (abs(elevation) - 6) / 6  # 0 bei -6°, 1 bei -12°
            base_color = "#FF5500"  # Orange-Rot
            glow_intensity = 0.8 - (phase_progress * 0.25)  # 0.8 bis 0.55
            max_radius = 16
        else:
            # Astronomische Dämmerung: -12° bis -18°
            phase_progress = (abs(elevation) - 12) / 6  # 0 bei -12°, 1 bei -18°
            base_color = "#CC2200"  # Kräftiges Rot
            glow_intensity = 0.55 - (phase_progress * 0.4)  # 0.55 bis 0.15
            max_radius = 12
        
        if glow_intensity <= 0.05:
            return
        
        # Hintergrundfarbe für Farbmischung
        bg_color = COLORS['card_bg']
        
        # Zeichne konzentrische Halbkreise (von aussen nach innen)
        num_layers = 5
        for i in range(num_layers):
            layer_progress = i / num_layers  # 0 = aussen, 1 = innen
            
            # Radius nimmt nach innen ab
            radius = max_radius * (1 - layer_progress * 0.6)
            
            # Intensität nimmt nach aussen ab
            layer_intensity = glow_intensity * (1 - layer_progress * 0.5)
            
            # Mische Farbe mit Hintergrund
            color = self._blend_colors(bg_color, base_color, layer_intensity)
            
            # Zeichne Halbkreis am unteren Rand (Horizont)
            y_center = h + radius * 0.3  # Leicht unter dem Horizont zentriert
            
            self.sun_canvas.create_arc(
                cx - radius, y_center - radius,
                cx + radius, y_center + radius,
                start=0, extent=180,  # Obere Hälfte
                fill=color, outline=""
            )
    
    def _blend_colors(self, bg_hex, fg_hex, intensity):
        """
        Mischt zwei Hex-Farben basierend auf Intensität.
        intensity=0: nur bg, intensity=1: nur fg
        """
        try:
            # Parse Hex-Farben
            bg_r = int(bg_hex[1:3], 16)
            bg_g = int(bg_hex[3:5], 16)
            bg_b = int(bg_hex[5:7], 16)
            
            fg_r = int(fg_hex[1:3], 16)
            fg_g = int(fg_hex[3:5], 16)
            fg_b = int(fg_hex[5:7], 16)
            
            # Lineare Interpolation
            r = int(bg_r + (fg_r - bg_r) * intensity)
            g = int(bg_g + (fg_g - bg_g) * intensity)
            b = int(bg_b + (fg_b - bg_b) * intensity)
            
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return fg_hex

    def update_sun_position(self):
        """Aktualisiert die Sonnenposition basierend auf aktueller Elevation."""
        try:
            if not init_skyfield():
                return
            t_now = TS.now()
            self.sun_elevation = get_sun_elevation_skyfield(t_now)
            self.draw_sun()
        except Exception as e:
            logging.debug(f"Fehler bei Sonnenpositions-Update: {e}")
    
    def update_moon_position(self):
        """Aktualisiert die Mondposition basierend auf aktueller Elevation."""
        try:
            if not init_skyfield():
                return
            t_now = TS.now()
            self.moon_elevation = get_moon_elevation_skyfield(t_now)
            self.draw_moon()
        except Exception as e:
            logging.debug(f"Fehler bei Mondpositions-Update: {e}")

    def draw_moon(self):
        """
        Zeichnet den Mond mit korrekter Beleuchtung und Höhenposition.
        Der Mond wandert wie die Sonne über einen imaginären Horizont.
        """
        try:
            self.moon_canvas.delete("all")

            w = self.moon_canvas.winfo_width()
            h = self.moon_canvas.winfo_height()
            if w < 10 or h < 10:
                self._debounced_redraw("moon", self.draw_moon)
                return

            moon_radius = 14
            cx = w / 2
            
            elevation = self.moon_elevation
            
            # === UNTER HORIZONT: Sanftes Schimmern ===
            if elevation < 0:
                self._draw_moon_glow(w, h, cx, elevation)
                return
            
            # === ÜBER HORIZONT: Mond zeichnen ===
            margin_top = 5
            usable_height = h - margin_top - moon_radius
            
            max_elev = max(self.moon_max_elevation, 1)
            normalized = elevation / max_elev
            normalized = max(0, min(1, normalized))
            
            cy = h - (normalized * usable_height)
            
            # Mondgrafik mit Phase zeichnen
            self._draw_moon_phase(cx, cy, moon_radius)

        except Exception as e:
            logging.error(f"Fehler beim Zeichnen des Mondes: {e}")
    
    def _draw_moon_glow(self, w, h, cx, elevation):
        """Zeichnet ein sanftes Mondschimmern am Horizont wenn unter 0°."""
        # Nur bis -10° sichtbar (Mond geht schneller "aus" als Sonne)
        if elevation < -10:
            return
        
        # Intensität: 0° = 1.0, -10° = 0.0
        intensity = 1.0 + (elevation / 10.0)  # elevation ist negativ
        if intensity <= 0.05:
            return
        
        moon_color = COLORS['moon_light']
        bg_color = COLORS['card_bg']
        
        max_radius = 16
        num_layers = 4
        
        for i in range(num_layers):
            layer_progress = i / num_layers
            radius = max_radius * (1 - layer_progress * 0.5)
            layer_intensity = intensity * (1 - layer_progress * 0.4)
            
            color = self._blend_colors(bg_color, moon_color, layer_intensity * 0.6)
            
            y_center = h + radius * 0.3
            
            self.moon_canvas.create_arc(
                cx - radius, y_center - radius,
                cx + radius, y_center + radius,
                start=0, extent=180,
                fill=color, outline=""
            )
    
    def _draw_moon_phase(self, cx, cy, radius):
        """Zeichnet die Mondphase an der angegebenen Position."""
        illumination = self.moon_illumination / 100.0
        is_waxing = (self.moon_trend == "↑")
        
        # Nur Outline bei Neumond
        outline_color = COLORS['text_dim'] if illumination < 0.03 else ""
        
        # Hintergrund (dunkle Seite)
        self.moon_canvas.create_oval(
            cx - radius, cy - radius,
            cx + radius, cy + radius,
            fill=COLORS['moon_dark'],
            outline=outline_color,
            width=1
        )
        
        if illumination <= 0.01:
            return
        
        if illumination >= 0.99:
            # Vollmond
            self.moon_canvas.create_oval(
                cx - radius + 1, cy - radius + 1,
                cx + radius - 1, cy + radius - 1,
                fill=COLORS['moon_light'],
                outline=""
            )
        elif illumination <= 0.5:
            # Mondsichel
            self._draw_moon_crescent_at(cx, cy, radius, illumination, is_waxing)
        else:
            # Gibbous (mehr als halb)
            self._draw_moon_gibbous_at(cx, cy, radius, illumination, is_waxing)

    def _draw_moon_crescent_at(self, cx, cy, radius, illumination, is_waxing):
        """Zeichnet eine Mondsichel (weniger als 50% beleuchtet)."""
        # Zeichne mit vielen kleinen Linien für glatte Kurve
        steps = 50
        points = []

        for i in range(steps + 1):
            # Winkel von -90 bis +90 Grad (oberer bis unterer Rand)
            angle = math.radians(-90 + 180 * i / steps)
            y = cy + radius * math.sin(angle)

            # Äusserer Rand (immer auf dem Kreis)
            x_outer = cx + radius * math.cos(angle)

            # Innerer Rand (elliptisch eingerückt)
            # Die Einrückung hängt von der Beleuchtung ab
            inner_factor = 1 - (illumination * 2)  # 1 bei 0%, 0 bei 50%
            x_inner = cx + radius * math.cos(angle) * inner_factor

            if is_waxing:
                # Rechte Seite beleuchtet
                points.append((x_inner, y, x_outer, y))
            else:
                # Linke Seite beleuchtet (spiegeln)
                points.append((2*cx - x_outer, y, 2*cx - x_inner, y))

        # Zeichne die Sichel als Polygon
        if points:
            polygon_points = []
            # Äussere Kante (oben nach unten)
            for p in points:
                if is_waxing:
                    polygon_points.extend([p[2], p[3]])
                else:
                    polygon_points.extend([p[0], p[1]])
            # Innere Kante (unten nach oben)
            for p in reversed(points):
                if is_waxing:
                    polygon_points.extend([p[0], p[1]])
                else:
                    polygon_points.extend([p[2], p[3]])

            if len(polygon_points) >= 6:
                self.moon_canvas.create_polygon(
                    polygon_points,
                    fill=COLORS['moon_light'],
                    outline=""
                )

    def _draw_moon_gibbous_at(self, cx, cy, radius, illumination, is_waxing):
        """Zeichnet einen Dreiviertelmond (mehr als 50% beleuchtet)."""
        # Bei Gibbous ist mehr als die Hälfte beleuchtet
        # Wir zeichnen erst den kompletten hellen Halbkreis,
        # dann fügen wir den gewölbten Teil hinzu

        steps = 50
        polygon_points = []

        # Der beleuchtete Teil besteht aus:
        # 1. Einem Halbkreis auf der beleuchteten Seite
        # 2. Einem gewölbten Teil der über die Mitte hinausragt

        # Berechne wie weit der gewölbte Teil reicht
        # Bei illumination=0.5: geht bis zur Mitte (0)
        # Bei illumination=1.0: geht bis zum anderen Rand
        bulge_factor = (illumination - 0.5) * 2  # 0 bei 50%, 1 bei 100%

        for i in range(steps + 1):
            angle = math.radians(-90 + 180 * i / steps)
            y = cy + radius * math.sin(angle)

            # Äusserer Rand auf der beleuchteten Seite
            x_outer = radius * math.cos(angle)

            # Gewölbter Rand (elliptische Kurve)
            x_bulge = -radius * math.cos(angle) * bulge_factor

            if is_waxing:
                # Rechte Seite beleuchtet, Wölbung nach links
                polygon_points.extend([cx + x_outer, y])
            else:
                # Linke Seite beleuchtet, Wölbung nach rechts
                polygon_points.extend([cx - x_outer, y])

        # Gewölbter Teil (rückwärts)
        for i in range(steps, -1, -1):
            angle = math.radians(-90 + 180 * i / steps)
            y = cy + radius * math.sin(angle)
            x_bulge = radius * math.cos(angle) * bulge_factor

            if is_waxing:
                polygon_points.extend([cx - x_bulge, y])
            else:
                polygon_points.extend([cx + x_bulge, y])

        if len(polygon_points) >= 6:
            self.moon_canvas.create_polygon(
                polygon_points,
                fill=COLORS['moon_light'],
                outline=""
            )

    # ---------------------------------------------------
    # PV Tagesgrafik (NEU)
    # ---------------------------------------------------
    def draw_pv_graph(self):
        """
        Zeichnet die PV-Tagesgrafik mit fester Zeitachse (civil_dawn bis civil_dusk).
        Volle Feldbreite, keine Zeitbeschriftungen.
        """
        try:
            self.pv_graph_canvas.delete("all")

            w = self.pv_graph_canvas.winfo_width()
            h = self.pv_graph_canvas.winfo_height()
            if w < 10 or h < 10:
                self._debounced_redraw("pv_graph", self.draw_pv_graph)
                return

            measurements = self.pv_daily_measurements
            
            # Minimale Ränder für volle Breite
            margin_top = 2
            margin_bottom = 16  # Nur für Peak-Anzeige
            margin_sides = 2
            graph_width = w - 2 * margin_sides
            graph_height = h - margin_top - margin_bottom

            # Hilfsfunktion: Zeit in Minuten seit Mitternacht
            def time_to_minutes(time_str):
                try:
                    parts = time_str.split(":")
                    return int(parts[0]) * 60 + int(parts[1])
                except Exception:
                    return 0
            
            def datetime_to_minutes(dt):
                if dt is None:
                    return None
                return dt.hour * 60 + dt.minute

            # Feste Zeitgrenzen: civil_dawn bis civil_dusk
            dawn_min = datetime_to_minutes(self.pv_civil_dawn)
            dusk_min = datetime_to_minutes(self.pv_civil_dusk)
            
            # Fallback wenn Dämmerungszeiten noch nicht berechnet
            if dawn_min is None or dusk_min is None:
                dawn_min = 6 * 60    # 06:00 als Fallback
                dusk_min = 20 * 60   # 20:00 als Fallback
            
            time_range = dusk_min - dawn_min
            if time_range <= 0:
                time_range = 14 * 60  # 14 Stunden Fallback

            if not measurements:
                self.pv_graph_canvas.create_text(
                    w / 2, h / 2 - 5,
                    text="Keine Daten",
                    font=("Arial", 11),
                    fill=COLORS['text_dim']
                )
                return
            
            if len(measurements) < 2:
                # Nur ein Datenpunkt - zeige einfach den Wert
                power = measurements[0].get("power", 0) or 0
                self.pv_graph_canvas.create_text(
                    w / 2, h / 2 - 5,
                    text=f"{power:.0f} W",
                    font=("Arial", 14, "bold"),
                    fill=get_pv_power_color(power) if power > 0 else COLORS['text_dim']
                )
                return

            # Zeiten in Minuten umrechnen
            times_minutes = [time_to_minutes(m.get("time", "00:00:00")) for m in measurements]

            # Maximalen Wert finden für Y-Skalierung
            max_power = max((m.get("power") or 0) for m in measurements)
            if max_power <= 0:
                max_power = 1

            # Minimale Balkenbreite (für Sichtbarkeit bei sehr kurzen Intervallen)
            min_bar_width = 1
            
            # Zeichne Balken - jeder reicht von seiner Zeit bis zur nächsten Messung
            y_bottom = h - margin_bottom
            
            for i, m in enumerate(measurements):
                power = m.get("power") or 0
                time_min = times_minutes[i]
                
                # X-Position: proportional zur festen Zeitachse (dawn bis dusk)
                time_fraction = (time_min - dawn_min) / time_range
                time_fraction = max(0, min(1, time_fraction))  # Clamp 0-1
                x_left = margin_sides + time_fraction * graph_width
                
                # Balkenbreite: bis zur nächsten Messung (oder bis jetzt/dusk)
                if i < len(measurements) - 1:
                    next_time_min = times_minutes[i + 1]
                else:
                    # Letzter Balken: bis zur aktuellen Zeit oder dusk
                    now = datetime.now(get_local_tz())
                    now_min = now.hour * 60 + now.minute
                    next_time_min = min(now_min, dusk_min)
                
                next_fraction = (next_time_min - dawn_min) / time_range
                next_fraction = max(0, min(1, next_fraction))
                x_right = margin_sides + next_fraction * graph_width
                
                bar_width = x_right - x_left
                bar_width = max(bar_width, min_bar_width)

                # Balkenhöhe proportional zur Leistung
                if max_power > 0 and power > 0:
                    bar_height = (power / max_power) * graph_height
                    bar_height = max(bar_height, 1)
                else:
                    bar_height = 0

                y_top = y_bottom - bar_height

                color = get_pv_power_color(power)

                if bar_height > 0:
                    self.pv_graph_canvas.create_rectangle(
                        x_left, y_top,
                        x_left + bar_width, y_bottom,
                        fill=color,
                        outline=color
                    )

            # Maximalwert unten mittig anzeigen
            self.pv_graph_canvas.create_text(
                w / 2, h - 2,
                text=f"Peak: {max_power:.0f} W",
                font=("Arial", 10, "bold"),
                fill=COLORS['text_secondary'],
                anchor="s"
            )

        except Exception as e:
            logging.error(f"Fehler beim Zeichnen der PV-Grafik: {e}")

    def calculate_pressure_trend(self):
        if len(self.pressure_history) < 3:
            return ""

        recent = list(self.pressure_history)[-3:]
        pressures = []
        for entry in recent:
            if len(entry) >= 2 and entry[1] is not None:
                pressures.append(entry[1])

        if len(pressures) < 2:
            return ""

        trend = pressures[-1] - pressures[0]

        if trend > 1:
            return " ↑↑"
        elif trend > 0.3:
            return " ↑"
        elif trend < -1:
            return " ↓↓"
        elif trend < -0.3:
            return " ↓"
        else:
            return " →"

    def get_noise_text(self, noise):
        """Kurzer Noise-Text für 50x20 Canvas."""
        return f"🔊{noise}dB"

    # ---------------------------------------------------
    # THREADING: Netatmo Update
    # ---------------------------------------------------
    def schedule_netatmo(self):
        """Plant periodische Netatmo-Updates. Wird genau einmal gestartet."""
        self.update_netatmo_once()
        self._netatmo_timer = self.root.after(INTERVALS['netatmo'], self.schedule_netatmo)
    
    def update_netatmo_once(self):
        """Startet einen einzelnen Netatmo-Fetch (ohne neuen Timer zu planen)."""
        threading.Thread(target=self._bg_fetch_netatmo, daemon=True).start()

    def _bg_fetch_netatmo(self):
        """Läuft im Hintergrundthread mit Retry-Logik und Lock."""
        import time as time_module
        
        # Lock: Verhindert parallele Fetches
        if not self._netatmo_fetch_lock.acquire(blocking=False):
            logging.debug("Netatmo: Fetch läuft bereits, überspringe.")
            return
        
        try:
            for attempt in range(3):
                try:
                    token = get_access_token()
                    data = fetch_netatmo_data(token)
                    if data and data.get("body", {}).get("devices"):
                        self.root.after(0, lambda d=data: self._apply_netatmo_data(d))
                        return
                    else:
                        logging.warning(f"Netatmo: Leere Antwort bei Versuch {attempt + 1}")
                except Exception as e:
                    wait = min(30 * (2 ** attempt), 300)  # 30s, 60s, 120s, max 5min
                    logging.warning(f"Netatmo Versuch {attempt + 1}/3 fehlgeschlagen: {e}")
                    if attempt < 2:
                        logging.debug(f"Warte {wait}s vor erneutem Versuch...")
                        time_module.sleep(wait)
            
            logging.error("Netatmo: Alle 3 Versuche fehlgeschlagen")
        finally:
            self._netatmo_fetch_lock.release()

    def _apply_netatmo_data(self, data, from_cache=False):
        """Läuft im Main-Thread: Aktualisiert GUI.
        
        Args:
            data: Netatmo API response
            from_cache: True wenn aus Cache geladen (überspringt Archivierung/Health-Tracking)
        """
        try:
            # Health-Check nur bei echtem API-Call
            if not from_cache:
                self._last_netatmo_update = datetime.now()
                self._netatmo_retry_count = 0
            
            devices = data.get("body", {}).get("devices", [])
            if not devices:
                return

            main_device = devices[0]
            dash_main = main_device.get("dashboard_data", {})

            # NEU: Aussentemperatur vom Aussenmodul (NAModule1) suchen
            outdoor_temp = None
            for device in devices:
                mods = [device] + device.get("modules", [])
                for module in mods:
                    if module.get("type") == "NAModule1":  # Aussenmodul
                        dash_data = module.get("dashboard_data", {})
                        outdoor_temp = dash_data.get("Temperature")
                        if outdoor_temp is not None:
                            self.outdoor_temperature = outdoor_temp
                        break
                if outdoor_temp is not None:
                    break

            pressure = dash_main.get("Pressure")
            if pressure is not None and isinstance(pressure, (int, float)):
                self.current_pressure = pressure
                trend = self.calculate_pressure_trend()
                color = COLORS['pressure_high'] if pressure >= NORMAL_PRESSURE else COLORS['pressure_low']
                self.pressure_label.config(text=f" {pressure:.0f} hPa{trend}", fg=color)

                # Stundenlogging nur bei echtem API-Call (nicht bei Cache)
                if not from_cache:
                    now = datetime.now(get_local_tz())
                    hour_timestamp = now.replace(minute=0, second=0, microsecond=0)

                    if self.last_saved_pressure_hour != hour_timestamp:
                        ts_iso = hour_timestamp.isoformat()
                        if not any(entry[0] == ts_iso for entry in self.pressure_history):
                            # NEU: Speichere [timestamp, pressure, temperature]
                            self.pressure_history.append([ts_iso, pressure, outdoor_temp])
                            self.save_pressure_history()
                            self.root.after(100, self.draw_barograph)
                        self.last_saved_pressure_hour = hour_timestamp

            noise = dash_main.get("Noise")
            if noise is not None and isinstance(noise, (int, float)):
                self.noise_value = noise

            # Archivierung nur bei echtem API-Call
            archive_data = None
            if not from_cache:
                archive_data = {
                    "timestamp": datetime.now(get_local_tz()).isoformat(),
                    "modules": []
                }

            gui_module_count = 0  # Zähler für GUI (max 4 Module)
            archive_module_count = 0  # Zähler für alle Module im Archiv
            
            # Sammle alle Module aus allen Devices
            all_modules = []
            for device in devices:
                # Hauptgerät (NAMain) hinzufügen
                all_modules.append(device)
                # Untermodule hinzufügen
                all_modules.extend(device.get("modules", []))
            
            # Priorisierung für GUI:
            # 1. NAMain (Innen, hat CO2/Lärm/Druck) - Wohnzimmer
            # 2. NAModule4 (Zusätzliche Innenmodule) - Oben, Unten
            # 3. NAModule1 (Außen, Temperatur) - Carport
            # 4. Rest (NAModule2=Wind, NAModule3=Regen haben keine Temperatur)
            MODULE_PRIORITY = {
                "NAMain": 0,      # Wohnzimmer
                "NAModule4": 1,   # Zusatz-Innen (Oben, Unten)
                "NAModule1": 2,   # Außen (Carport)
                "NAModule3": 3,   # Regen
                "NAModule2": 4,   # Wind
            }
            
            # Sortiere Module nach Priorität für GUI
            gui_modules = sorted(
                all_modules,
                key=lambda m: MODULE_PRIORITY.get(m.get("type", ""), 99)
            )
            
            # 1. Archivierung: Alle Module durchgehen
            for module in all_modules:
                dash_data = module.get("dashboard_data", {})

                # Regendaten extrahieren
                if module.get("type") == "NAModule3":
                    self.current_rain_1h = dash_data.get("sum_rain_1", 0) or 0
                    self.current_rain_24h = dash_data.get("sum_rain_24", 0) or 0

                # Modul-Daten für Archiv sammeln (nur bei echtem API-Call)
                if archive_data is not None:
                    module_archive = {
                        "name": module.get("module_name", f"Modul {archive_module_count + 1}"),
                        "type": module.get("type", "unknown"),
                        "temperature": dash_data.get("Temperature"),
                        "humidity": dash_data.get("Humidity"),
                        "co2": dash_data.get("CO2"),
                        "pressure": dash_data.get("Pressure"),
                        "noise": dash_data.get("Noise"),
                        "battery_percent": module.get("battery_percent"),
                        "min_temp": dash_data.get("min_temp"),
                        "max_temp": dash_data.get("max_temp"),
                        "rain_1h": dash_data.get("sum_rain_1"),
                        "rain_24h": dash_data.get("sum_rain_24")
                    }
                    # Entferne None-Werte für kompakteres JSON
                    module_archive = {k: v for k, v in module_archive.items() if v is not None}
                    archive_data["modules"].append(module_archive)
                archive_module_count += 1

            # 2. GUI: Erste 4 priorisierte Module anzeigen
            for module in gui_modules:
                if gui_module_count >= 4:
                    break
                    
                dash_data = module.get("dashboard_data") or {}
                
                # Überspringe Module ohne dashboard_data (ausser NAMain für CO₂/Noise)
                if not dash_data and module.get("type") != "NAMain":
                    continue
                
                # Überspringe Module ohne Temperatur (Wind/Regen)
                if dash_data.get("Temperature") is None and module.get("type") not in ["NAMain"]:
                    continue
                
                lbls = self.module_labels[gui_module_count]

                mod_name = module.get("module_name", f"Modul {gui_module_count + 1}")
                # Ellipsis statt harter Truncation
                display_name = (mod_name[:11] + "…") if len(mod_name) > 12 else mod_name
                lbls["name"].config(text=display_name)

                temp = dash_data.get("Temperature")
                if temp is not None:
                    color = get_temp_gradient_color(temp)
                    lbls["temperature"].config(text=f"{temp:.1f}°", fg=color)
                else:
                    lbls["temperature"].config(text="--.-°", fg=COLORS['text_dim'])

                min_temp = dash_data.get("min_temp")
                max_temp = dash_data.get("max_temp")
                if min_temp is not None and max_temp is not None:
                    lbls["minmax"].config(text=f"↓{min_temp:.0f}° ↑{max_temp:.0f}°")
                else:
                    lbls["minmax"].config(text="")

                co2 = dash_data.get("CO2")
                if co2 is not None:
                    color = get_co2_gradient_color(co2)
                    lbls["co2"].config(text=f"CO₂: {co2}", fg=color)
                else:
                    lbls["co2"].config(text="")

                humidity = dash_data.get("Humidity")
                if humidity is not None:
                    lbls["humidity"].config(text=f"{humidity}%")
                else:
                    lbls["humidity"].config(text="")

                # Hauptstation (erstes Modul, NAMain): Lärm statt Batterie
                if module.get("type") == "NAMain" and self.noise_value is not None:
                    lbls["battery_value"] = None  # Kein Akku
                    canvas = lbls.get("battery_canvas")
                    if canvas:
                        canvas.delete("all")
                        text = self.get_noise_text(self.noise_value)
                        color = (
                            COLORS['accent_green'] if self.noise_value < 45 else
                            COLORS['accent_orange'] if self.noise_value < 65 else
                            COLORS['accent_red']
                        )
                        w = canvas.winfo_width()
                        h = canvas.winfo_height()
                        if w > 10 and h > 10:
                            canvas.create_text(
                                w/2, h/2, text=text,
                                font=("Arial", 11, "bold"),
                                fill=color
                            )
                else:
                    battery = module.get("battery_percent")
                    canvas = lbls.get("battery_canvas")
                    lbls["battery_value"] = battery
                    if canvas and battery is not None:
                        # Canvas muss erst gelayoutet sein für korrekte Größe
                        canvas.update_idletasks()
                        draw_battery(canvas, battery, self.battery_blink_state)
                    elif canvas:
                        canvas.delete("all")

                gui_module_count += 1
            
            # Batterie-Grafiken nochmal nach kurzer Verzögerung neu zeichnen
            # (Canvas-Größe ist beim ersten Mal oft noch nicht korrekt)
            self.root.after(200, self._redraw_all_batteries)

            # Archivierung nur bei echtem API-Call (nicht bei Cache)
            if archive_data is not None:
                # PV-Daten zur Archivierung hinzufügen
                if self.last_pv_power is not None:
                    archive_data["pv_power"] = self.last_pv_power

                # Archiviere auf OneDrive (im Hintergrund um UI nicht zu blockieren)
                threading.Thread(
                    target=archive_measurement,
                    args=(archive_data,),
                    daemon=True
                ).start()
                
                # SPEICHERN für nächsten Reboot
                self.save_to_cache("netatmo", data)

        except Exception as e:
            logging.error(f"Fehler beim Anwenden der Netatmo-Daten: {e}")

    # ---------------------------------------------------
    # THREADING: Astronomie Update
    # ---------------------------------------------------
    def update_astronomy(self):
        """Startet den Hintergrund-Thread für Astro-Daten."""
        threading.Thread(target=self._bg_fetch_astronomy, daemon=True).start()

    def _bg_fetch_astronomy(self):
        try:
            sunrise_str, sunset_str, day_len_str = get_sun_times()
            moonrise, moonset, phase_name, trend, illumination = get_moon_times()
            
            # Berechne max. Sonnenhöhe und Solar Noon Zeit
            max_sun_elevation, solar_noon_str = self._calculate_solar_noon()
            
            # Berechne zivile Dämmerungszeiten
            civil_dawn_str, civil_dusk_str = self._calculate_civil_twilight()
            
            # Berechne max. Mondhöhe
            max_moon_elevation = self._calculate_max_moon_elevation()
            
            result = (sunrise_str, sunset_str, day_len_str, civil_dawn_str, civil_dusk_str, 
                     solar_noon_str, moonrise, moonset, phase_name, trend, 
                     illumination, max_sun_elevation, max_moon_elevation)
            self.root.after(0, lambda: self._apply_astronomy(result))
        except Exception as e:
            logging.exception(f"Astro-Fetch-Fehler: {e}")
    
    def _calculate_solar_noon(self):
        """Berechnet Solar Noon (Sonnenhöchststand) Zeit und maximale Höhe."""
        try:
            if not init_skyfield():
                return 45, "--:--"
            
            local_tz = get_local_tz()
            today = datetime.now(local_tz).date()
            
            max_elev = 0
            noon_hour = 12
            noon_minute = 0
            
            # Suche in 5-Minuten-Schritten zwischen 11:00 und 14:00
            for hour in range(11, 15):
                for minute in range(0, 60, 5):
                    dt = datetime(today.year, today.month, today.day, hour, minute, 0, tzinfo=local_tz)
                    t = TS.from_datetime(dt)
                    elev = get_sun_elevation_skyfield(t)
                    if elev > max_elev:
                        max_elev = elev
                        noon_hour = hour
                        noon_minute = minute
            
            solar_noon_str = f"{noon_hour:02d}:{noon_minute:02d}"
            return max(max_elev, 10), solar_noon_str
        except Exception as e:
            logging.debug(f"Fehler bei Solar Noon Berechnung: {e}")
            return 45, "--:--"
    
    def _calculate_civil_twilight(self):
        """Berechnet Beginn und Ende der zivilen Dämmerung."""
        try:
            # Nutze die robuste Binary-Search-Funktion
            dawn_dt, dusk_dt = get_civil_twilight_skyfield()
            
            if dawn_dt is None or dusk_dt is None:
                return "--:--", "--:--"
            
            civil_dawn_str = dawn_dt.strftime("%H:%M")
            civil_dusk_str = dusk_dt.strftime("%H:%M")
            
            return civil_dawn_str, civil_dusk_str
        except Exception as e:
            logging.debug(f"Fehler bei Dämmerungsberechnung: {e}")
            return "--:--", "--:--"
    
    def _calculate_max_sun_elevation(self):
        """Berechnet die maximale Sonnenhöhe für heute (bei Solar Noon)."""
        try:
            init_skyfield()
            local_tz = get_local_tz()
            today = datetime.now(local_tz).date()
            
            # Solar noon ist ungefähr um 12:00-13:00 Uhr
            # Wir prüfen jede Stunde und finden das Maximum
            max_elev = 0
            for hour in range(6, 19):  # 6:00 bis 18:00
                dt = datetime(today.year, today.month, today.day, hour, 0, 0, tzinfo=local_tz)
                t = TS.from_datetime(dt)
                elev = get_sun_elevation_skyfield(t)
                if elev > max_elev:
                    max_elev = elev
            
            return max(max_elev, 10)  # Mindestens 10° für sinnvolle Skalierung
        except Exception as e:
            logging.debug(f"Fehler bei Max-Sonnenhöhe-Berechnung: {e}")
            return 45  # Fallback
    
    def _calculate_max_moon_elevation(self):
        """Berechnet die maximale Mondhöhe für heute."""
        try:
            if not init_skyfield():
                return 45
            
            local_tz = get_local_tz()
            today = datetime.now(local_tz).date()
            
            max_elev = 0
            # Mond kann zu jeder Tageszeit am höchsten stehen
            for hour in range(0, 24):
                dt = datetime(today.year, today.month, today.day, hour, 0, 0, tzinfo=local_tz)
                t = TS.from_datetime(dt)
                elev = get_moon_elevation_skyfield(t)
                if elev > max_elev:
                    max_elev = elev
            
            return max(max_elev, 10)  # Mindestens 10° für sinnvolle Skalierung
        except Exception as e:
            logging.debug(f"Fehler bei Max-Mondhöhe-Berechnung: {e}")
            return 45  # Fallback

    def _apply_astronomy(self, data):
        try:
            # Unterstütze verschiedene Cache-Formate (Abwärtskompatibilität)
            if not data or not isinstance(data, (list, tuple)):
                logging.warning("Ungültige Astro-Daten empfangen")
                return
            
            # Format-Erkennung basierend auf Länge
            if len(data) == 13:
                # Aktuelles Format mit max_moon_elevation
                (sunrise_str, sunset_str, day_len_str, civil_dawn_str, civil_dusk_str, 
                 solar_noon_str, moonrise, moonset, phase_name, trend, 
                 illumination, max_sun_elevation, max_moon_elevation) = data
            elif len(data) == 12:
                # Älteres Format ohne max_moon_elevation
                (sunrise_str, sunset_str, day_len_str, civil_dawn_str, civil_dusk_str, 
                 solar_noon_str, moonrise, moonset, phase_name, trend, 
                 illumination, max_sun_elevation) = data
                max_moon_elevation = 45  # Fallback
                logging.debug("Altes Cache-Format erkannt, verwende Fallback für Mondhöhe")
            elif len(data) == 11:
                # Noch älteres Format ohne day_len_str
                (sunrise_str, sunset_str, civil_dawn_str, civil_dusk_str, 
                 solar_noon_str, moonrise, moonset, phase_name, trend, 
                 illumination, max_sun_elevation) = data
                day_len_str = "--:--"
                max_moon_elevation = 45
                logging.debug("Sehr altes Cache-Format erkannt")
            else:
                logging.warning(f"Unbekanntes Astro-Cache-Format: {len(data)} Elemente")
                return
            
            # Sonnen-Labels (nur Zeiten, Icons übernehmen Symbole)
            self.civil_dawn_label.config(text=civil_dawn_str)
            self.sunrise_label.config(text=sunrise_str)
            self.solar_noon_label.config(text=solar_noon_str)
            self.sunset_label.config(text=sunset_str)
            self.civil_dusk_label.config(text=civil_dusk_str)
            self.day_length_label.config(text=day_len_str)
            
            # Speichere Monddaten für Grafik
            self.moon_illumination = illumination
            self.moon_trend = trend
            self.moon_phase_name = phase_name
            
            # Speichere max. Sonnenhöhe und aktualisiere Sonnenposition
            self.sun_max_elevation = max_sun_elevation
            self.update_sun_position()
            
            # Speichere max. Mondhöhe und aktualisiere Mondposition
            self.moon_max_elevation = max_moon_elevation
            self.update_moon_position()
            
            # Mond-Labels (nur Zeiten, Icons übernehmen Symbole)
            self.moonrise_label.config(text=moonrise)
            self.moonset_label.config(text=moonset)
            self.moon_percent_label.config(text=f"{illumination}%")
            
            # SPEICHERN für nächsten Reboot
            self.save_to_cache("astro", data)

        except Exception as e:
            logging.exception(f"Fehler beim Anwenden der Astro-Daten: {e}")

    def schedule_astronomy(self):
        self.update_astronomy()
        self.astronomy_timer = self.root.after(INTERVALS['astronomy'], self.schedule_astronomy)
    
    def schedule_sun_position(self):
        """Aktualisiert die Sonnenposition alle 60 Sekunden."""
        # Versuche init falls noch nicht verfügbar (z.B. Ephemeride später kopiert)
        if not init_skyfield():
            # Ohne Skyfield: seltener prüfen ob es verfügbar wird
            self.sun_position_timer = self.root.after(10_000, self.schedule_sun_position)
            return
        self.update_sun_position()
        self.sun_position_timer = self.root.after(INTERVALS['sun_position'], self.schedule_sun_position)
    
    def schedule_moon_position(self):
        """Aktualisiert die Mondposition alle 60 Sekunden."""
        # Versuche init falls noch nicht verfügbar (z.B. Ephemeride später kopiert)
        if not init_skyfield():
            # Ohne Skyfield: seltener prüfen ob es verfügbar wird
            self.moon_position_timer = self.root.after(10_000, self.schedule_moon_position)
            return
        self.update_moon_position()
        self.moon_position_timer = self.root.after(INTERVALS['moon_position'], self.schedule_moon_position)
    
    def _redraw_all_batteries(self):
        """Zeichnet alle Batterie-Grafiken neu (nach Layout-Update)."""
        for lbls in self.module_labels:
            canvas = lbls.get("battery_canvas")
            battery = lbls.get("battery_value")
            if canvas and battery is not None:
                draw_battery(canvas, battery, self.battery_blink_state)
    
    def toggle_battery_blink(self):
        """Toggelt den Blink-Zustand für kritische Batterien (<5%) und zeichnet neu."""
        self.battery_blink_state = not self.battery_blink_state
        
        # Zeichne alle kritischen Batterien neu
        for i, lbls in enumerate(self.module_labels):
            canvas = lbls.get("battery_canvas")
            battery = lbls.get("battery_value")
            if canvas and battery is not None and battery < 5:
                draw_battery(canvas, battery, self.battery_blink_state)
        
        self.root.after(500, self.toggle_battery_blink)

    # ---------------------------------------------------
    # PV – UI Update helper
    # ---------------------------------------------------
    def update_pv_labels(self, current_power, daily, monthly, yearly):
        try:
            # Health-Check: Update-Zeitpunkt tracken
            self._last_pv_update = datetime.now()
            
            self.last_pv_power = current_power

            if current_power is None or current_power <= 0:
                self.pv_power_label.config(
                    text="Nacht",
                    fg=COLORS['text_dim'],
                    font=("Arial", 28, "bold")
                )
            else:
                color = get_pv_power_color(current_power)
                self.pv_power_label.config(
                    text=f"{current_power:.0f} W",
                    fg=color,
                    font=("Arial", 36, "bold")
                )

            def _kwh(v, fmt):
                if v is None:
                    return "--"
                return format(v / 1000.0, fmt)

            stats_text = (
                f"T: {_kwh(daily, '.1f')} • "
                f"M: {_kwh(monthly, '.0f')} • "
                f"J: {_kwh(yearly, '.0f')} kWh"
            )
            self.pv_stats_label.config(text=stats_text)
            
            # NEU: PV-Messung zu Tagesdaten hinzufügen
            self.pv_daily_measurements = add_pv_measurement(current_power)
            
            # NEU: PV-Grafik aktualisieren
            self.draw_pv_graph()
            
            # SPEICHERN für nächsten Reboot
            pv_data = {
                "current": current_power,
                "daily": daily,
                "monthly": monthly,
                "yearly": yearly
            }
            self.save_to_cache("pv", pv_data)

        except Exception as e:
            logging.error(f"update_pv_labels error: {e}")

    # ---------------------------------------------------
    # PV – Abfragefenster & Followups (mit Threading)
    # ---------------------------------------------------
    def fetch_sunrise_sunset_datetimes_local(self, date=None):
        try:
            local_tz = get_local_tz()
            if date is None:
                date_obj = datetime.now(local_tz).date()
            else:
                date_obj = date

            date_str = date_obj.strftime("%Y-%m-%d")
            params = {
                "lat": LOCATION_LAT,
                "lon": LOCATION_LON,
                "date": date_str,
                "offset": met_offset_for_date(local_tz, date_obj),
            }
            headers = {"User-Agent": METNO_USER_AGENT}
            resp = requests.get(
                "https://api.met.no/weatherapi/sunrise/3.0/sun",
                params=params, headers=headers, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            props = data.get("properties", {})
            sunrise_obj = props.get("sunrise")
            sunset_obj = props.get("sunset")
            sunrise_dt = None
            sunset_dt = None
            if isinstance(sunrise_obj, dict) and sunrise_obj.get("time"):
                sunrise_dt = parse_iso_to_local(sunrise_obj["time"], local_tz)
            if isinstance(sunset_obj, dict) and sunset_obj.get("time"):
                sunset_dt = parse_iso_to_local(sunset_obj["time"], local_tz)
            return sunrise_dt, sunset_dt
        except Exception as e:
            logging.error(f"fetch_sunrise_sunset_datetimes_local error: {e}")
            return None, None

    def schedule_pv_window(self):
        """
        Plant das PV-Abfragefenster basierend auf ziviler Dämmerung.
        Verteilt exakt 280 Abfragen intelligent über den Tag:
        - Kernzeit (Sunrise-Sunset): 5x so viele Abfragen wie Dämmerung
        - Dämmerung (morgens/abends): weniger häufig
        """
        try:
            # Zähler zurücksetzen bei neuem Tag
            local_tz = get_local_tz()
            today = datetime.now(local_tz).date()
            if self.pv_query_date != today:
                self.pv_queries_today = 0
                self.pv_query_date = today
                logging.info(f"PV-Abfragezähler zurückgesetzt für {today}")
            
            # Zivile Dämmerung mit Skyfield berechnen
            civil_dawn, civil_dusk = get_civil_twilight_skyfield()
            
            # Fallback auf api.met.no wenn Skyfield fehlschlägt
            if not civil_dawn or not civil_dusk:
                logging.info("Skyfield-Dämmerung nicht verfügbar, verwende api.met.no Fallback")
                sunrise_dt, sunset_dt = self.fetch_sunrise_sunset_datetimes_local()
                
                if sunrise_dt and sunset_dt:
                    # Schätze zivile Dämmerung: ca. 30 Minuten vor/nach Sonnenauf-/untergang
                    civil_dawn = sunrise_dt - timedelta(minutes=30)
                    civil_dusk = sunset_dt + timedelta(minutes=30)
                    logging.info(f"Fallback-Dämmerung: {civil_dawn.strftime('%H:%M')} - {civil_dusk.strftime('%H:%M')}")
                else:
                    logging.warning("Weder Skyfield noch api.met.no verfügbar. Erneuter Versuch in 30 Minuten.")
                    self._schedule_pv_retry(30 * 60 * 1000)
                    return
            
            # Sonnenauf-/untergang von API holen (falls noch nicht geholt)
            sunrise_dt, sunset_dt = self.fetch_sunrise_sunset_datetimes_local()
            
            if not sunrise_dt or not sunset_dt:
                # Fallback: Kernzeit = Dämmerung + 30min bis Dämmerung - 30min
                sunrise_dt = civil_dawn + timedelta(minutes=30)
                sunset_dt = civil_dusk - timedelta(minutes=30)
                logging.info("Sonnenzeiten nicht verfügbar, verwende Fallback")
            
            # Zeiten speichern
            self.pv_civil_dawn = civil_dawn
            self.pv_sunrise = sunrise_dt
            self.pv_sunset = sunset_dt
            self.pv_civil_dusk = civil_dusk
            
            # Phasendauern in Minuten berechnen
            ramp_morning_min = max(0, (sunrise_dt - civil_dawn).total_seconds() / 60)
            core_min = max(0, (sunset_dt - sunrise_dt).total_seconds() / 60)
            ramp_evening_min = max(0, (civil_dusk - sunset_dt).total_seconds() / 60)
            
            # Gewichtete Verteilung: Kernzeit bekommt 5x so viele Abfragen pro Minute
            CORE_WEIGHT = 5
            RAMP_WEIGHT = 1
            
            weighted_total = (
                ramp_morning_min * RAMP_WEIGHT +
                core_min * CORE_WEIGHT +
                ramp_evening_min * RAMP_WEIGHT
            )
            
            if weighted_total <= 0:
                logging.warning("Ungültige Zeitberechnung, verwende Fallback-Intervalle")
                self.pv_interval_ramp = 10 * 60
                self.pv_interval_core = 2 * 60
            else:
                # Abfragen pro Phase berechnen
                queries_ramp_morning = int(self.pv_max_queries * (ramp_morning_min * RAMP_WEIGHT) / weighted_total)
                queries_core = int(self.pv_max_queries * (core_min * CORE_WEIGHT) / weighted_total)
                queries_ramp_evening = int(self.pv_max_queries * (ramp_evening_min * RAMP_WEIGHT) / weighted_total)
                
                # Sicherstellen dass wir nicht über 280 kommen
                total_queries = queries_ramp_morning + queries_core + queries_ramp_evening
                if total_queries > self.pv_max_queries:
                    queries_core -= (total_queries - self.pv_max_queries)
                
                # Intervalle berechnen (Minuten / Abfragen = Minuten pro Abfrage)
                if queries_ramp_morning > 0 and ramp_morning_min > 0:
                    self.pv_interval_ramp = (ramp_morning_min / queries_ramp_morning) * 60
                else:
                    self.pv_interval_ramp = 10 * 60  # Fallback 10 min
                
                if queries_core > 0 and core_min > 0:
                    self.pv_interval_core = (core_min / queries_core) * 60
                else:
                    self.pv_interval_core = 2 * 60  # Fallback 2 min
                
                logging.info(
                    f"PV-Budget: {self.pv_max_queries} Abfragen | "
                    f"Morgen: {queries_ramp_morning} ({self.pv_interval_ramp/60:.1f}min) | "
                    f"Kern: {queries_core} ({self.pv_interval_core/60:.1f}min) | "
                    f"Abend: {queries_ramp_evening} ({self.pv_interval_ramp/60:.1f}min)"
                )
            
            now = datetime.now(civil_dawn.tzinfo)
            
            logging.info(
                f"PV-Fenster: {civil_dawn.strftime('%H:%M')} | "
                f"↑{sunrise_dt.strftime('%H:%M')} | "
                f"↓{sunset_dt.strftime('%H:%M')} | "
                f"{civil_dusk.strftime('%H:%M')}"
            )
            
            # Entscheide basierend auf aktueller Zeit
            if now < civil_dawn:
                delay_ms = int((civil_dawn - now).total_seconds() * 1000)
                delay_ms = max(delay_ms, 1000)
                self._schedule_pv_retry(delay_ms, start_queries=True)
                logging.info(f"PV startet bei Morgendämmerung um {civil_dawn.strftime('%H:%M')}")
                
            elif civil_dawn <= now <= civil_dusk:
                logging.info("Aktuell im PV-Fenster -> Starte Abfragen")
                self.pv_query_step()
                
            else:
                self._schedule_next_day()
                
        except Exception as e:
            logging.exception(f"schedule_pv_window Fehler: {e}")
            self._schedule_pv_retry(30 * 60 * 1000)
    
    def _schedule_pv_retry(self, delay_ms, start_queries=False):
        """Hilfsfunktion: Plant erneuten Versuch oder Start."""
        if self.pv_window_timer:
            try:
                self.root.after_cancel(self.pv_window_timer)
            except Exception:
                pass
        
        if start_queries:
            self.pv_window_timer = self.root.after(delay_ms, self.pv_query_step)
        else:
            self.pv_window_timer = self.root.after(delay_ms, self.schedule_pv_window)
    
    def _schedule_next_day(self):
        """Plant PV-Abfragen für den nächsten Tag."""
        try:
            local_tz = get_local_tz()
            now = datetime.now(local_tz)
            tomorrow = (now + timedelta(days=1)).date()
            
            civil_dawn_tomorrow, _ = get_civil_twilight_skyfield(tomorrow)
            
            if civil_dawn_tomorrow:
                delay_ms = int((civil_dawn_tomorrow - now).total_seconds() * 1000)
                delay_ms = max(delay_ms, 60 * 1000)
                self._schedule_pv_retry(delay_ms, start_queries=True)
                logging.info(f"PV für morgen geplant: {civil_dawn_tomorrow.strftime('%Y-%m-%d %H:%M')}")
            else:
                # Fallback: In 12 Stunden erneut versuchen
                self._schedule_pv_retry(12 * 60 * 60 * 1000)
                logging.info("Konnte morgen nicht berechnen, versuche in 12h erneut")
                
        except Exception as e:
            logging.exception(f"_schedule_next_day Fehler: {e}")
            self._schedule_pv_retry(12 * 60 * 60 * 1000)
    
    def _get_current_pv_interval(self):
        """
        Bestimmt das aktuelle Abfrageintervall basierend auf Tageszeit.
        Prüft auch das Tagesbudget.
        
        Returns:
            interval_seconds: Sekunden bis zur nächsten Abfrage
            continue_queries: True wenn weitere Abfragen nötig und erlaubt
        """
        # Budget-Check
        if self.pv_queries_today >= self.pv_max_queries:
            logging.warning(f"PV-Tageslimit erreicht ({self.pv_queries_today}/{self.pv_max_queries})")
            return 0, False
        
        if not all([self.pv_civil_dawn, self.pv_sunrise, self.pv_sunset, self.pv_civil_dusk]):
            return self.pv_interval_core or 120, False
        
        if not self.pv_interval_ramp or not self.pv_interval_core:
            return 120, False  # Fallback 2 min
        
        now = datetime.now(self.pv_civil_dawn.tzinfo)
        
        # Vor Dämmerung oder nach Dämmerung: Stoppen
        if now < self.pv_civil_dawn or now > self.pv_civil_dusk:
            return 0, False
        
        # Morgendämmerung (civil_dawn bis sunrise): Rampe
        if self.pv_civil_dawn <= now < self.pv_sunrise:
            return self.pv_interval_ramp, True
        
        # Kernzeit (sunrise bis sunset): Häufig
        if self.pv_sunrise <= now <= self.pv_sunset:
            return self.pv_interval_core, True
        
        # Abenddämmerung (sunset bis civil_dusk): Rampe
        if self.pv_sunset < now <= self.pv_civil_dusk:
            return self.pv_interval_ramp, True
        
        return 0, False
    
    def pv_query_step(self):
        """Führt eine PV-Abfrage durch und plant die nächste."""
        threading.Thread(target=self._bg_fetch_pv, args=("smart",), daemon=True).start()
    
    def _finalize_pv_smart(self, result):
        """Verarbeitet PV-Ergebnis und plant nächste Abfrage."""
        # Immer Versuch zählen (für API-Limit-Schutz)
        self.pv_attempts_today += 1
        
        if result:
            self.update_pv_labels(*result)
            self.pv_queries_today += 1
            self.pv_consecutive_failures = 0
        else:
            self.pv_consecutive_failures += 1
            logging.debug(f"PV: Fehler #{self.pv_consecutive_failures}")
            
            # Bei 5+ aufeinanderfolgenden Fehlern: 15 Min Pause
            if self.pv_consecutive_failures >= 5:
                logging.warning(f"PV: {self.pv_consecutive_failures} Fehler in Folge, pausiere 15 Min")
                self._schedule_pv_retry(15 * 60 * 1000)
                return
        
        interval_s, continue_queries = self._get_current_pv_interval()
        
        if continue_queries and interval_s > 0:
            ms = int(interval_s * 1000)
            if self.pv_query_timer:
                try:
                    self.root.after_cancel(self.pv_query_timer)
                except Exception:
                    pass
            self.pv_query_timer = self.root.after(ms, self.pv_query_step)
            
            # Log alle 50 Abfragen
            if self.pv_queries_today % 50 == 0:
                logging.info(f"PV-Abfragen heute: {self.pv_queries_today}/{self.pv_max_queries} (Versuche: {self.pv_attempts_today})")
        else:
            # Prüfe ob noch Produktion vorhanden (Followup)
            if self.last_pv_power is not None and self.last_pv_power > 0:
                # Nur Followup wenn noch Budget übrig
                if self.pv_queries_today < self.pv_max_queries:
                    logging.info("PV-Fenster beendet, aber noch Produktion -> Followup alle 10min")
                    self.pv_followup_step()
                else:
                    logging.info("PV-Fenster beendet, Budget erschöpft")
                    self._schedule_next_day()
            else:
                logging.info(f"PV-Fenster beendet. Abfragen heute: {self.pv_queries_today}")
                self._schedule_next_day()

    def _bg_fetch_pv(self, mode):
        """Hintergrund-Funktion für PV-Abfrage."""
        try:
            current_power, daily, monthly, yearly = fetch_pv_data()
            result = (current_power, daily, monthly, yearly)
        except Exception as e:
            logging.debug(f"PV fetch error: {e}")
            result = None

        if mode == "smart":
            self.root.after(0, lambda: self._finalize_pv_smart(result))
        elif mode == "followup":
            self.root.after(0, lambda: self._finalize_pv_followup(result))
        elif mode == "single":
            self.root.after(0, lambda: self._finalize_pv_single(result))

    def pv_followup_step(self):
        """Startet Followup-Request im Hintergrund."""
        threading.Thread(target=self._bg_fetch_pv, args=("followup",), daemon=True).start()

    def _finalize_pv_followup(self, result):
        # Immer Versuch zählen
        self.pv_attempts_today += 1
        
        if result:
            self.update_pv_labels(*result)
            self.pv_queries_today += 1
            self.pv_consecutive_failures = 0
        else:
            self.pv_consecutive_failures += 1
            logging.debug(f"PV followup: Fehler #{self.pv_consecutive_failures}")
            
            # Bei 5+ Fehlern: längere Pause
            if self.pv_consecutive_failures >= 5:
                logging.warning(f"PV: {self.pv_consecutive_failures} Fehler in Folge, pausiere 15 Min")
                if self.pv_followup_timer:
                    try:
                        self.root.after_cancel(self.pv_followup_timer)
                    except Exception:
                        pass
                self.pv_followup_timer = self.root.after(15 * 60 * 1000, self.pv_followup_step)
                return

        # Budget-Check
        if self.pv_queries_today >= self.pv_max_queries:
            logging.info(f"PV-Followup beendet: Tageslimit erreicht ({self.pv_queries_today})")
            self._schedule_next_day()
            return

        if self.last_pv_power is not None and self.last_pv_power > 0:
            if self.pv_followup_timer:
                try:
                    self.root.after_cancel(self.pv_followup_timer)
                except Exception:
                    pass
            self.pv_followup_timer = self.root.after(10 * 60 * 1000, self.pv_followup_step)
            logging.debug("Weiterer PV-Followup in 10 Minuten (Produktion > 0).")
        else:
            logging.info(f"PV-Followups beendet (keine Produktion). Abfragen heute: {self.pv_queries_today}")
            self._schedule_next_day()

    def pv_single_update(self):
        """Einmaliger Start-Abruf im Hintergrund."""
        threading.Thread(target=self._bg_fetch_pv, args=("single",), daemon=True).start()

    def _finalize_pv_single(self, result):
        # Immer Versuch zählen
        self.pv_attempts_today += 1
        
        if result:
            self.update_pv_labels(*result)
            self.pv_queries_today += 1
            self.pv_consecutive_failures = 0
        else:
            self.pv_consecutive_failures += 1
            logging.debug(f"PV single: Fehler #{self.pv_consecutive_failures}")

    # ---------------------------------------------------
    # Run
    # ---------------------------------------------------
    def run(self):
        logging.info("Dashboard wird gestartet...")
        
        # Signal-Handler für sauberes Beenden (systemd, Ctrl+C)
        import signal
        signal.signal(signal.SIGTERM, lambda *args: self._shutdown())
        signal.signal(signal.SIGINT, lambda *args: self._shutdown())
        
        # 0. SKYFIELD: Einmal beim Start initialisieren und Status loggen
        if init_skyfield():
            logging.info("Skyfield bereit für Astronomie-Berechnungen")
        else:
            logging.warning("Skyfield nicht verfügbar - Astronomie-Funktionen eingeschränkt")
        
        # 1. Barograph sofort zeichnen (Historiedaten sind bereits geladen)
        self.root.after(100, self.draw_barograph)
        
        # NEU: PV-Grafik initialisieren
        self.root.after(150, self.draw_pv_graph)
        
        # NEU: Mond-Grafik initialisieren (wird später durch Astronomie-Daten aktualisiert)
        self.root.after(200, self.draw_moon)
        
        # NEU: Sonnen-Grafik initialisieren
        self.root.after(250, self.draw_sun)

        # 3. NETATMO: Cache sofort laden und anzeigen
        self.load_cached_data()
        
        # Barograph aktualisieren (falls im Cache ein neuerer Wert war)
        self.root.after(300, self.draw_barograph)

        # 4. NETATMO: Reguläre Updates starten (alle 5 Minuten)
        #    Erster API-Call erfolgt sofort, dann alle 300s
        self.schedule_netatmo()
        
        # 5. ASTRONOMIE: Sofort abrufen und anzeigen
        #    Danach alle 15 Minuten aktualisieren
        self.schedule_astronomy()
        
        # 5b. SONNENPOSITION: Alle 60 Sekunden aktualisieren
        self.root.after(1000, self.schedule_sun_position)
        
        # 5c. MONDPOSITION: Alle 60 Sekunden aktualisieren
        self.root.after(1500, self.schedule_moon_position)
        
        # 5d. BATTERIE-BLINKEN: Alle 500ms toggeln für <5% Batterien
        self.root.after(INTERVALS['battery_blink'], self.toggle_battery_blink)
        
        # 5e. PV-BUFFER-FLUSH: Alle 5 Minuten prüfen und speichern wenn dirty
        self.root.after(INTERVALS['pv_flush'], self.schedule_pv_flush)
        
        # 5f. HEALTH-CHECK: Überwacht ob Updates hängen
        self.root.after(INTERVALS['health_check'], self._health_check)
        
        # 6. SOLAREDGE: Einmalig sofort abrufen
        logging.info("Erzwinge sofortiges SolarEdge-Update beim Start.")
        self.pv_single_update()
        
        # 7. PV-FENSTER: Sobald Astronomie-Daten da sind, Zeitplan berechnen
        #    (schedule_pv_window nutzt die Sonnenzeiten von api.met.no)
        self.schedule_pv_window()

        logging.info("Mainloop gestartet")
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
    
    def _shutdown(self):
        """Sauberes Herunterfahren mit Datensicherung und Timer-Cleanup."""
        # Guard gegen mehrfachen Aufruf
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        
        logging.info("Shutdown-Signal empfangen, speichere Daten...")
        
        # Timer canceln
        for timer_name in ['_netatmo_timer', 'astronomy_timer', 'sun_position_timer', 
                           'moon_position_timer', 'pv_window_timer', 'pv_query_timer', 
                           'pv_followup_timer']:
            timer = getattr(self, timer_name, None)
            if timer:
                try:
                    self.root.after_cancel(timer)
                except Exception:
                    pass
        
        # Daten speichern
        try:
            save_pv_daily_data()
            self.save_pressure_history()
        except Exception as e:
            logging.error(f"Fehler beim Speichern: {e}")
        
        logging.info("Dashboard beendet.")
        try:
            self.root.quit()
        except Exception:
            pass
    
    def _health_check(self):
        """Überwacht ob alle Updates noch laufen."""
        try:
            now = datetime.now()
            
            # Netatmo-Updates prüfen (sollten alle 5 Minuten kommen)
            if self._last_netatmo_update:
                delta = (now - self._last_netatmo_update).total_seconds()
                if delta > 600:  # 10 Minuten ohne Update
                    logging.warning(f"Netatmo-Updates hängen ({delta/60:.1f} min), starte neu...")
                    self._netatmo_retry_count += 1
                    if self._netatmo_retry_count <= 3:
                        self.update_netatmo_once()  # Nur einmal triggern, nicht neuen Loop
            
            # PV-Updates prüfen (nur während Tageszeit relevant)
            local_tz = get_local_tz()
            hour = datetime.now(local_tz).hour
            if 7 <= hour <= 20 and self._last_pv_update:
                delta = (now - self._last_pv_update).total_seconds()
                if delta > 1800:  # 30 Minuten ohne PV-Update
                    logging.warning(f"PV-Updates hängen ({delta/60:.1f} min)")
        
        except Exception as e:
            logging.debug(f"Health-Check Fehler: {e}")
        
        # Nächsten Check planen
        self.root.after(INTERVALS['health_check'], self._health_check)
    
    def schedule_pv_flush(self):
        """Flusht den PV-Buffer alle 5 Minuten (falls dirty). Schutz gegen Stromausfall."""
        try:
            save_pv_daily_data()
        except Exception as e:
            logging.debug(f"PV-Flush Fehler: {e}")
        # Nächsten Flush planen
        self.root.after(INTERVALS['pv_flush'], self.schedule_pv_flush)

if __name__ == "__main__":
    logging.info("Starte Dashboard v6 (bereinigt)...")
    dashboard = Dashboard7inchRedesigned()
    dashboard.run()
