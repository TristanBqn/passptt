import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import re

# ============================================================================
# AUTHENTIFICATION PAR MOT DE PASSE
# ============================================================================
def check_password():
    """Retourne True si l'utilisateur a entré le bon mot de passe."""
    def password_entered():
        """Vérifie si le mot de passe est correct."""
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"] # Ne pas garder le mot de passe en mémoire
        else:
            st.session_state["password_correct"] = False

    # Si déjà authentifié
    if st.session_state.get("password_correct", False):
        return True
    # Afficher le formulaire de connexion
    st.text_input(
        "🔒 Mot de passe", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Mot de passe incorrect")
    return False

# Vérifier l'authentification avant d'afficher l'application
if not check_password():
    st.stop()

# ============================================================================
# CONSTANTES
# ============================================================================
SHEET_URL = "https://docs.google.com/spreadsheets/d/1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw/edit?gid=0#gid=0"
SHEET_ID = "1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw"

FRANCE_LAT_MIN, FRANCE_LAT_MAX = 41.0, 51.5
FRANCE_LON_MIN, FRANCE_LON_MAX = -5.5, 10.0
FRANCE_CENTER = [46.603354, 1.888334]
FRANCE_ZOOM = 6

GEOCODE_SCORE_MIN = 0.4
GEOCODE_SCORE_FALLBACK = 0.3

API_ADRESSE_URL = "https://api-adresse.data.gouv.fr/search/"
PHOTON_API_URL = "https://photon.komoot.io/api/"
API_TIMEOUT = 10

# ============================================================================
# CONFIGURATION STREAMLIT
# ============================================================================
st.set_page_config(
    page_title="Gestion d'adresses",
    page_icon="📍",
    layout="wide"
)

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================
def parse_addresses_with_notes(input_text):
    """Parse une chaîne contenant plusieurs adresses séparées par des virgules"""
    addresses = [addr.strip() for addr in input_text.split(',')]
    parsed = []
    for addr in addresses:
        if not addr:
            continue
        note_match = re.search(r'\(([^)]+)\)', addr)
        if note_match:
            note = note_match.group(1).strip()
            address_clean = re.sub(r'\s*\([^)]+\)', '', addr).strip()
        else:
            note = ""
            address_clean = addr.strip()
        if address_clean:
            parsed.append((address_clean, note))
    return parsed

def normalize_coordinate(value):
    """Normalise une coordonnée"""
    try:
        coord = float(value)
        if abs(coord) > 360:
            coord = coord / 1000000
        return coord
    except (ValueError, TypeError):
        return None

def correct_paris_longitude(lat, lon, address):
    """Corrige automatiquement les longitudes incorrectes pour Paris"""
    if lat and lon:
        if ("paris" in str(address).lower() or "75" in str(address)) and (0 < lon < 1):
            corrected_lon = lon + 2
            if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                return corrected_lon
    return lon

def validate_france_coordinates(lat, lon, address=""):
    """Valide et corrige les coordonnées France"""
    if lat is None or lon is None:
        return lat, lon, False, "Coordonnées nulles"
    # Latitude
    if not (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX):
        return lat, lon, False, f"Latitude {lat:.6f} hors de France"
    # Longitude avec correction Paris
    if not (FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX):
        if "paris" in address.lower() or "75" in address:
            if 0 < lon < 1:
                corrected_lon = lon + 2
                if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                    return lat, corrected_lon, True, f"⚠️ Correction longitude: {lon:.6f} → {corrected_lon:.6f}"
        return lat, lon, False, f"Longitude {lon:.6f} hors de France"
    return lat, lon, True, ""

def is_in_france(lat, lon):
    """Vérifie si les coordonnées sont en France métropolitaine"""
    return (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX and FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX)

# ============================================================================
# CARTE INTERACTIVE
# Ajout du choix entre différents layers (voir et satellite)
# ============================================================================
def create_empty_france_map():
    """Crée une carte vide centrée sur la France avec choix du fond de carte"""
    m = folium.Map(
        location=FRANCE_CENTER,
        zoom_start=FRANCE_ZOOM,
        tiles=None  # Important: ne pas fixer de tiles par défaut pour LayerControl
    )

    # Ajout OpenStreetMap
    folium.TileLayer(
        tiles='OpenStreetMap',
        name='Carte standard',
        control=True,
        overlay=False
    ).add_to(m)

    # Ajout Satellite Google
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google',
        name='Satellite',
        control=True,
        overlay=False
    ).add_to(m)

    # Ajout Satellite Hybride Google (labels)
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        attr='Google Labels',
        name='Satellite Hybride',
        control=True,
        overlay=False
    ).add_to(m)

    # Ajout Satellite Esri (option)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Esri Satellite',
        control=True,
        overlay=False
    ).add_to(m)

    # Contrôle des couches
    folium.LayerControl(collapsed=False).add_to(m)
    return m

def create_marker(lat, lon, address, note=""):
    """Ajoute un marqueur Folium avec street view dans la popup"""
    street_view_url = f"https://www.google.com/maps?layer=c&cbll={lat},{lon}"
    popup_html = f"""
    <b>{address}</b><br>
    <a href="{street_view_url}" target="_blank">Voir Street View</a>
    <br>
    <strong>📝 Note:</strong> <em>{note}</em><br>
    📍 Lat: {lat:.6f}, Lon: {lon:.6f}
    """
    return folium.Marker(location=[lat, lon], popup=popup_html)

# ============================================================================
# INTERFACE UTILISATEUR
# ============================================================================
st.title("📍 Gestion d'adresses interactive France")

input_text = st.text_area("Entrez les adresses, séparées par des virgules (ajoutez une note entre parenthèses si besoin)")
parsed_addresses = parse_addresses_with_notes(input_text)

map_object = create_empty_france_map()

for address, note in parsed_addresses:
    # Géocodage simplifié (à adapter selon tes besoins)
    response = requests.get(API_ADRESSE_URL, params={"q": address, "limit": 1}, timeout=API_TIMEOUT)
    if response.status_code == 200 and response.json()["features"]:
        coords = response.json()["features"][0]["geometry"]["coordinates"]
        lon, lat = coords
        lat, lon, is_valid, msg = validate_france_coordinates(lat, lon, address)
        if is_valid:
            marker = create_marker(lat, lon, address, note)
            marker.add_to(map_object)
        else:
            st.error(f"Erreur sur l'adresse '{address}' : {msg}")

# Afficher la carte avec le contrôle interactif
st_folium(map_object, width=1200, height=800)
