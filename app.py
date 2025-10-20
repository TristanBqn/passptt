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
                    return lat, lon
    except Exception:
        pass
    
    return None

def try_photon_api(address):
    """Tente de géocoder avec Photon API (komoot) comme fallback"""
    try:
        params = {
            'q': address,
            'limit': 1,
            'lang': 'fr',
            'location_bias_scale': 0.5
        }
        response = requests.get(PHOTON_API_URL, params=params, timeout=API_TIMEOUT)
        
        if response.status_code == 200:
            data = response.json()
            features = data.get('features', [])
            
            if features:
                feature = features[0]
                coords = feature['geometry']['coordinates']
                properties = feature['properties']
                
                lat, lon = coords[1], coords[0]
                
                if is_in_france(lat, lon):
                    country = properties.get('country', '').lower()
                    if country in ['france', 'fr', '']:
                        return lat, lon
    except Exception:
        pass
    
    return None

def geocode_address_france(address):
    """Convertit une adresse française en coordonnées géographiques"""
    if not address.strip():
        return None, None
    
    # Tentative avec l'API principale
    result = try_api_adresse(address)
    if result:
        return result
    
    # Fallback sur Photon API
    result = try_photon_api(address)
    if result:
        return result
    
    return None, None

# ============================================================================
# GESTION DES ADRESSES
# ============================================================================

def add_addresses_batch(sheet, addresses_with_notes):
    """
    Ajoute plusieurs adresses en mode batch
    addresses_with_notes: liste de tuples (adresse, note)
    """
    results = {
        'success': [],
        'failed': []
    }
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, (address, note) in enumerate(addresses_with_notes):
        # Mettre à jour la progression
        progress = (i + 1) / len(addresses_with_notes)
        progress_bar.progress(progress)
        status_text.text(f"Géocodage en cours... ({i+1}/{len(addresses_with_notes)})")
        
        # Géocoder l'adresse
        lat, lon = geocode_address_france(address)
        
        if lat is not None and lon is not None:
            try:
                # Ajouter au Google Sheet
                sheet.append_row(
                    [address, float(lat), float(lon), note],
                    value_input_option='USER_ENTERED'
                )
                results['success'].append((address, note))
            except Exception as e:
                results['failed'].append((address, note, f"Erreur d'ajout: {e}"))
        else:
            results['failed'].append((address, note, "Géocodage échoué"))
    
    progress_bar.empty()
    status_text.empty()
    
    return results

def add_address(sheet, address, note=""):
    """Ajoute une nouvelle adresse dans le Google Sheet"""
    if not address.strip():
        st.warning("⚠️ Veuillez entrer une adresse valide.")
        return False
    
    with st.spinner("🔍 Géocodage de l'adresse en cours..."):
        lat, lon = geocode_address_france(address)
        
        if lat is None or lon is None:
            st.error(f"❌ Impossible de géocoder l'adresse : {address}")
            return False
        
        try:
            # Utiliser value_input_option='USER_ENTERED' pour préserver les décimales
            sheet.append_row([address, float(lat), float(lon), note], value_input_option='USER_ENTERED')
            if note:
                st.success(f"✅ Adresse ajoutée : {address} (📝 {note})")
            else:
                st.success(f"✅ Adresse ajoutée : {address}")
            return True
        except Exception as e:
            st.error(f"❌ Erreur lors de l'ajout : {e}")
            return False

def delete_address(sheet, index):
    """Supprime une adresse du Google Sheet"""
    try:
        # +2 car : +1 pour l'en-tête, +1 car gspread commence à 1
        sheet.delete_rows(index + 2)
        st.success("✅ Adresse supprimée avec succès !")
        return True
    except Exception as e:
        st.error(f"❌ Erreur lors de la suppression : {e}")
        return False

# ============================================================================
# VISUALISATION CARTE
# ============================================================================

def display_map(df):
    """Affiche les adresses sur une carte Folium centrée sur la France"""
    # Cas 1 : DataFrame vide
    if df.empty:
        st.info("📭 Aucune adresse à afficher sur la carte.")
        m = create_empty_france_map()
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    # Filtrer les coordonnées valides en France
    france_coords = df[
        df['Latitude'].between(FRANCE_LAT_MIN, FRANCE_LAT_MAX) &
        df['Longitude'].between(FRANCE_LON_MIN, FRANCE_LON_MAX)
    ].copy()
    
    # Cas 2 : Aucune coordonnée valide
    if france_coords.empty:
        st.warning("⚠️ Aucune coordonnée valide en France métropolitaine.")
        st.info("Vérifiez que les adresses ont été correctement géocodées.")
        
        # Afficher les coordonnées problématiques pour diagnostic
        with st.expander("🔍 Diagnostic des coordonnées"):
            st.dataframe(df[['Adresse', 'Latitude', 'Longitude', 'Note']])
            st.caption("💡 Vérifiez que les coordonnées sont au format décimal (ex: 48.857739, 2.294844)")
        
        m = create_empty_france_map()
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    # Cas 3 : Une seule adresse
    if len(france_coords) == 1:
        row = france_coords.iloc[0]
        lat, lon = float(row['Latitude']), float(row['Longitude'])
        note = row.get('Note', '')
        
        m = folium.Map(
            location=[lat, lon],
            zoom_start=14,
            tiles='OpenStreetMap'
        )
        create_marker(lat, lon, row['Adresse'], note).add_to(m)
    
    # Cas 4 : Plusieurs adresses
    else:
        center_lat = france_coords['Latitude'].mean()
        center_lon = france_coords['Longitude'].mean()
        
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=8,
            tiles='OpenStreetMap'
        )
        
        # Ajouter tous les marqueurs
        for _, row in france_coords.iterrows():
            note = row.get('Note', '')
            create_marker(
                float(row['Latitude']),
                float(row['Longitude']),
                row['Adresse'],
                note
            ).add_to(m)
        
        # Ajuster les limites pour inclure tous les points
        sw = france_coords[['Latitude', 'Longitude']].min().values.tolist()
        ne = france_coords[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne], padding=[30, 30])
    
    st_folium(m, width=1400, height=600, returned_objects=[])

# ============================================================================
# INTERFACE PRINCIPALE
# ============================================================================

def main():
    st.title("🏠 Application de Gestion d'Adresses Françaises")
    st.caption("Utilise l'API Adresse officielle du gouvernement français (data.gouv.fr)")
    
    # Connexion au Google Sheet
    sheet = connect_to_google_sheet()
    if sheet is None:
        st.stop()
    
    # Navigation
        st.stop()
    
    # Navigation
