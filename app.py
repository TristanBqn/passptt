import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import re

# ===============================================================
# AUTHENTIFICATION
# ===============================================================
def check_password():
    """Retourne True si l'utilisateur a entré le bon mot de passe."""
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input("🔒 Mot de passe", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("😕 Mot de passe incorrect")
    return False

if not check_password():
    st.stop()

# ===============================================================
# CONSTANTES
# ===============================================================
SHEET_ID = "1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw"
FRANCE_LAT_MIN, FRANCE_LAT_MAX = 41.0, 51.5
FRANCE_LON_MIN, FRANCE_LON_MAX = -5.5, 10.0
FRANCE_CENTER = [46.603354, 1.888334]
FRANCE_ZOOM = 6

API_ADRESSE_URL = "https://api-adresse.data.gouv.fr/search/"
PHOTON_API_URL = "https://photon.komoot.io/api/"
API_TIMEOUT = 10

# ===============================================================
# PAGE CONFIG
# ===============================================================
st.set_page_config(page_title="Gestion d'adresses", page_icon="📍", layout="wide")

# ===============================================================
# UTILITAIRES
# ===============================================================
def parse_addresses_with_notes(input_text):
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
    try:
        coord = float(value)
        if abs(coord) > 360:
            coord = coord / 1000000
        return coord
    except (ValueError, TypeError):
        return None

def correct_paris_longitude(lat, lon, address):
    if lat and lon:
        if ("paris" in str(address).lower() or "75" in str(address)) and (0 < lon < 1):
            corrected_lon = lon + 2
            if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                return corrected_lon
    return lon

def validate_france_coordinates(lat, lon, address=""):
    if lat is None or lon is None:
        return lat, lon, False, "Coordonnées nulles"
    if not (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX):
        return lat, lon, False, f"Latitude {lat:.6f} hors de France"
    if not (FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX):
        if "paris" in address.lower() or "75" in address:
            if 0 < lon < 1:
                corrected_lon = lon + 2
                if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                    return lat, corrected_lon, True, f"⚠️ Correction longitude: {lon:.6f} → {corrected_lon:.6f}"
        return lat, lon, False, f"Longitude {lon:.6f} hors de France"
    return lat, lon, True, ""

def is_in_france(lat, lon):
    return (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX and FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX)

def create_empty_france_map(tile_type='OpenStreetMap'):
    """Crée une carte vide centrée sur la France avec le fond choisi."""
    return folium.Map(location=FRANCE_CENTER, zoom_start=FRANCE_ZOOM, tiles=tile_type)

def create_marker(lat, lon, address, note=""):
    street_view_url = f"https://www.google.com/maps?layer=c&cbll={lat},{lon}"
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
    tooltip_text = f"{address} ({note})" if note else address
    return folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=tooltip_text,
        icon=folium.Icon(color='red', icon='home', prefix='fa')
    )

# ===============================================================
# GOOGLE SHEETS
# ===============================================================
@st.cache_resource
def connect_to_google_sheet():
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        headers = sheet.row_values(1)
        if not headers or headers != ['Adresse', 'Latitude', 'Longitude', 'Note']:
            sheet.update('A1:D1', [['Adresse', 'Latitude', 'Longitude', 'Note']])
        return sheet
    except Exception as e:
        st.error(f"❌ Erreur de connexion au Google Sheet : {e}")
        return None

def get_all_addresses(sheet):
    try:
        data = sheet.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if not df.empty:
                if 'Note' not in df.columns:
                    df['Note'] = ''
                df['Latitude'] = df['Latitude'].apply(normalize_coordinate)
                df['Longitude'] = df['Longitude'].apply(normalize_coordinate)
                df = df.dropna(subset=['Latitude', 'Longitude'])
                df['Longitude'] = df.apply(
                    lambda row: correct_paris_longitude(row['Latitude'], row['Longitude'], row['Adresse']),
                    axis=1
                )
                return df
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])
    except Exception as e:
        st.error(f"❌ Erreur lors de la récupération des données : {e}")
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])

# ===============================================================
# GÉOCODAGE
# ===============================================================
def try_api_adresse(address):
    try:
        params = {'q': address, 'limit': 1}
        response = requests.get(API_ADRESSE_URL, params=params, timeout=API_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            features = data.get('features', [])
            if features:
                f = features[0]
                lat, lon = f['geometry']['coordinates'][1], f['geometry']['coordinates'][0]
                if is_in_france(lat, lon):
                    return lat, lon
    except Exception:
        pass
    return None

def try_photon_api(address):
    try:
        params = {'q': address, 'limit': 1, 'lang': 'fr'}
        response = requests.get(PHOTON_API_URL, params=params, timeout=API_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            features = data.get('features', [])
            if features:
                f = features[0]
                lat, lon = f['geometry']['coordinates'][1], f['geometry']['coordinates'][0]
                if is_in_france(lat, lon):
                    return lat, lon
    except Exception:
        pass
    return None

def geocode_address_france(address):
    result = try_api_adresse(address)
    if result:
        return result
    result = try_photon_api(address)
    if result:
        return result
    return None, None

# ===============================================================
# VISUALISATION CARTE AVEC CHOIX DU LAYER
# ===============================================================
def display_map(df):
    """Affiche les adresses sur une carte avec choix du fond de carte"""
    # Sélecteur du type de fond
    map_type = st.radio(
        "🗺️ Type de carte",
        ["Vue standard (OpenStreetMap)", "Vue satellite (Esri WorldImagery)"],
        horizontal=True,
    )
    if map_type == "Vue standard (OpenStreetMap)":
        tiles_choice = "OpenStreetMap"
    else:
        tiles_choice = "Esri WorldImagery"

    if df.empty:
        st.info("📭 Aucune adresse à afficher.")
        m = create_empty_france_map(tiles_choice)
        st_folium(m, width=1400, height=600, returned_objects=[])
        return

    france_coords = df[
        df['Latitude'].between(FRANCE_LAT_MIN, FRANCE_LAT_MAX) &
        df['Longitude'].between(FRANCE_LON_MIN, FRANCE_LON_MAX)
    ].copy()

    if france_coords.empty:
        st.warning("⚠️ Aucune coordonnée valide en France métropolitaine.")
        m = create_empty_france_map(tiles_choice)
        st_folium(m, width=1400, height=600)
        return

    if len(france_coords) == 1:
        row = france_coords.iloc[0]
        lat, lon = float(row['Latitude']), float(row['Longitude'])
        note = row.get('Note', '')
        m = folium.Map(location=[lat, lon], zoom_start=14, tiles=tiles_choice)
        create_marker(lat, lon, row['Adresse'], note).add_to(m)
    else:
        center_lat = france_coords['Latitude'].mean()
        center_lon = france_coords['Longitude'].mean()
        m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles=tiles_choice)
        for _, row in france_coords.iterrows():
            create_marker(float(row['Latitude']), float(row['Longitude']), row['Adresse'], row.get('Note', '')).add_to(m)
        sw = france_coords[['Latitude', 'Longitude']].min().values.tolist()
        ne = france_coords[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne])

    st_folium(m, width=1400, height=600)

# ===============================================================
# INTERFACE PRINCIPALE
# ===============================================================
def main():
    st.title("🏠 Gestion de mes pass PTT et codes")
    st.caption("Propriété intellectuelle de Tristan BANNIER")

    sheet = connect_to_google_sheet()
    if sheet is None:
        st.stop()

    page = st.sidebar.radio("Navigation", ["📍 Gestion des adresses", "🗺️ Carte interactive"], index=0)
    if st.sidebar.button("🔄 Rafraîchir les données"):
        st.rerun()

    if page == "🗺️ Carte interactive":
        st.header("🗺️ Visualisation sur carte")
        df = get_all_addresses(sheet)
        display_map(df)
    else:
        st.header("📋 Liste des adresses (mode réduit pour test)")
        df = get_all_addresses(sheet)
        st.dataframe(df)

if __name__ == "__main__":
    main()
