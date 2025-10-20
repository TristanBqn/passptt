import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import re

# ============================================================================
# CONSTANTES
# ============================================================================
SHEET_URL = "https://docs.google.com/spreadsheets/d/1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw/edit?gid=0#gid=0"
SHEET_ID = "1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw"

# Constantes géographiques pour la France métropolitaine
FRANCE_LAT_MIN, FRANCE_LAT_MAX = 41.0, 51.5
FRANCE_LON_MIN, FRANCE_LON_MAX = -5.5, 10.0
FRANCE_CENTER = [46.603354, 1.888334]
FRANCE_ZOOM = 6

# Seuils de confiance pour le géocodage
GEOCODE_SCORE_MIN = 0.4
GEOCODE_SCORE_FALLBACK = 0.3

# Configuration des API
API_ADRESSE_URL = "https://api-adresse.data.gouv.fr/search/"
PHOTON_API_URL = "https://photon.komoot.io/api/"
API_TIMEOUT = 10

# ============================================================================
# Configuration de la page
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
    """
    Parse une chaîne contenant plusieurs adresses séparées par des virgules
    et extrait les notes entre parenthèses.
    
    Exemple: "adresse 1 (beau balcon), Adresse 2, Adresse 3 (jardin ouvert)"
    Retourne: [
        ("adresse 1", "beau balcon"),
        ("Adresse 2", ""),
        ("Adresse 3", "jardin ouvert")
    ]
    """
    # Séparer par les virgules
    addresses = [addr.strip() for addr in input_text.split(',')]
    
    parsed = []
    for addr in addresses:
        if not addr:
            continue
            
        # Extraire les notes entre parenthèses
        note_match = re.search(r'\(([^)]+)\)', addr)
        
        if note_match:
            note = note_match.group(1).strip()
            # Retirer les parenthèses de l'adresse
            address_clean = re.sub(r'\s*\([^)]+\)', '', addr).strip()
        else:
            note = ""
            address_clean = addr.strip()
        
        if address_clean:  # Ignorer les adresses vides
            parsed.append((address_clean, note))
    
    return parsed

def normalize_coordinate(value):
    """
    Normalise une coordonnée qui peut être dans différents formats:
    - 48.857739 (format normal avec décimales)
    - 48857739 (format sans décimales - micro-degrés)
    """
    try:
        coord = float(value)
        # Si la valeur est supérieure à 360, c'est probablement en micro-degrés
        if abs(coord) > 360:
            coord = coord / 1000000
        return coord
    except (ValueError, TypeError):
        return None

def is_in_france(lat, lon):
    """Vérifie si les coordonnées sont en France métropolitaine"""
    return (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX and 
            FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX)

def create_empty_france_map():
    """Crée une carte vide centrée sur la France"""
    return folium.Map(
        location=FRANCE_CENTER,
        zoom_start=FRANCE_ZOOM,
        tiles='OpenStreetMap'
    )

def create_marker(lat, lon, address, note=""):
    """Crée un marqueur Folium standardisé avec note optionnelle et lien Street View"""
    # URL Google Street View
    street_view_url = f"https://www.google.com/maps?layer=c&cbll={lat},{lon}"
    
    # Créer le contenu HTML du popup avec Street View
    popup_html = f"""
    <div style="font-family: Arial; min-width: 250px;">
        <h4 style="margin-bottom: 10px; color: #2c3e50;">{address}</h4>
    """
    
    if note:
        popup_html += f"""
        <p style="margin: 5px 0; color: #7f8c8d;">
            <b>📝 Note:</b> <i>{note}</i>
        </p>
        """
    
    popup_html += f"""
        <p style="margin: 10px 0; font-size: 12px; color: #95a5a6;">
            📍 Lat: {lat:.6f}, Lon: {lon:.6f}
        </p>
        <hr style="margin: 10px 0; border: none; border-top: 1px solid #ecf0f1;">
        <a href="{street_view_url}" target="_blank" 
           style="display: inline-block; padding: 8px 15px; background-color: #3498db; 
                  color: white; text-decoration: none; border-radius: 5px; 
                  text-align: center; font-weight: bold;">
            🗺️ Voir dans Street View
        </a>
    </div>
    """
    
    # Tooltip simple
    if note:
        tooltip_text = f"{address} ({note})"
    else:
        tooltip_text = address
    
    return folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=tooltip_text,
        icon=folium.Icon(color='red', icon='home', prefix='fa')
    )

# ============================================================================
# CONNEXION ET GESTION GOOGLE SHEETS
# ============================================================================

@st.cache_resource
def connect_to_google_sheet():
    """Initialise la connexion au Google Sheet"""
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        # Vérifier et créer les en-têtes si nécessaire (avec colonne Note)
        try:
            headers = sheet.row_values(1)
            if not headers or headers != ['Adresse', 'Latitude', 'Longitude', 'Note']:
                sheet.update('A1:D1', [['Adresse', 'Latitude', 'Longitude', 'Note']])
        except:
            sheet.update('A1:D1', [['Adresse', 'Latitude', 'Longitude', 'Note']])
        
        return sheet
    except Exception as e:
        st.error(f"❌ Erreur de connexion au Google Sheet : {e}")
        st.info("Assurez-vous que les secrets sont correctement configurés dans Streamlit Cloud.")
        return None

def get_all_addresses(sheet):
    """Récupère toutes les adresses depuis le Google Sheet"""
    try:
        data = sheet.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if not df.empty:
                # S'assurer que la colonne Note existe
                if 'Note' not in df.columns:
                    df['Note'] = ''
                
                # Normaliser les coordonnées (gérer les formats avec/sans décimales)
                df['Latitude'] = df['Latitude'].apply(normalize_coordinate)
                df['Longitude'] = df['Longitude'].apply(normalize_coordinate)
                
                # Supprimer les lignes avec coordonnées invalides
                df = df.dropna(subset=['Latitude', 'Longitude'])
                
                # Remplir les notes vides
                df['Note'] = df['Note'].fillna('')
                
                return df
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])
    except Exception as e:
        st.error(f"❌ Erreur lors de la récupération des données : {e}")
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])

# ============================================================================
# GÉOCODAGE
# ============================================================================

def try_api_adresse(address):
    """Tente de géocoder avec l'API Adresse officielle (data.gouv.fr)"""
    try:
        params = {'q': address, 'limit': 1}
        response = requests.get(API_ADRESSE_URL, params=params, timeout=API_TIMEOUT)
        
        if response.status_code == 200:
            data = response.json()
            features = data.get('features', [])
            
            if features:
                feature = features[0]
                coords = feature['geometry']['coordinates']
                properties = feature['properties']
                
                lat, lon = coords[1], coords[0]
                full_address = properties.get('label', address)
                score = properties.get('score', 0)
                
                # Validation géographique
                if not is_in_france(lat, lon):
                    return None
                
                # Validation du score de confiance
                if score >= GEOCODE_SCORE_MIN or score >= GEOCODE_SCORE_FALLBACK:
                    return lat
