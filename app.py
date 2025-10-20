import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests

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

def create_marker(lat, lon, address):
    """Crée un marqueur Folium standardisé"""
    return folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(address, max_width=300),
        tooltip=address,
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
        
        # Vérifier et créer les en-têtes si nécessaire
        try:
            headers = sheet.row_values(1)
            if not headers or headers != ['Adresse', 'Latitude', 'Longitude']:
                sheet.update('A1:C1', [['Adresse', 'Latitude', 'Longitude']])
        except:
            sheet.update('A1:C1', [['Adresse', 'Latitude', 'Longitude']])
        
        return sheet
    except Exception as e:
        st.error(f"❌ Erreur de connexion au Google Sheet : {e}")
        st.info("Assurez-vous que les secrets sont correctement configurés dans Streamlit Cloud.")
        return None

@st.cache_data(ttl=300)
def get_all_addresses(_sheet):
    """Récupère toutes les adresses depuis le Google Sheet avec cache de 5 minutes"""
    try:
        data = _sheet.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if not df.empty:
                # Conversion et nettoyage des données
                df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
                df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
                df = df.dropna(subset=['Latitude', 'Longitude'])
                return df
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude'])
    except Exception as e:
        st.error(f"❌ Erreur lors de la récupération des données : {e}")
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude'])

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
                    st.error("❌ Cette adresse ne semble pas être en France métropolitaine.")
                    st.info(f"Coordonnées trouvées : Lat {lat:.4f}, Lon {lon:.4f}")
                    return None
                
                # Validation du score de confiance
                if score >= GEOCODE_SCORE_MIN:
                    st.success(f"✅ Adresse trouvée : {full_address} (confiance: {score:.2f})")
                    return lat, lon
                elif score >= GEOCODE_SCORE_FALLBACK:
                    st.warning(f"⚠️ Adresse trouvée avec un faible score de confiance ({score:.2f})")
                    st.info(f"Adresse suggérée : {full_address}")
                    return lat, lon
                else:
                    st.error("Score trop faible, adresse rejetée.")
                    return None
    except Exception as e:
        st.warning(f"⚠️ Erreur API Adresse : {str(e)}")
    
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
                        st.info("📍 Adresse trouvée via Photon API")
                        return lat, lon
                    else:
                        st.warning(f"⚠️ Pays détecté : {country}, pas en France")
                else:
                    st.warning(f"⚠️ Coordonnées hors France : Lat {lat:.4f}, Lon {lon:.4f}")
    except Exception as e:
        st.warning(f"⚠️ Erreur Photon API : {str(e)}")
    
    return None

def geocode_address_france(address):
    """Convertit une adresse française en coordonnées géographiques"""
    if not address.strip():
        st.error("❌ Veuillez entrer une adresse valide.")
        return None, None
    
    # Tentative avec l'API principale
    result = try_api_adresse(address)
    if result:
        return result
    
    # Fallback sur Photon API
    result = try_photon_api(address)
    if result:
        return result
    
    # Échec complet
    st.error("❌ Impossible de géocoder cette adresse en France.")
    st.info("💡 Astuce : Essayez avec le format complet : numéro + rue + code postal + ville")
    st.info("Exemple : 10 rue de la Paix, 75002 Paris")
    return None, None

# ============================================================================
# GESTION DES ADRESSES
# ============================================================================

def add_address(sheet, address):
    """Ajoute une nouvelle adresse dans le Google Sheet"""
    if not address.strip():
        st.warning("⚠️ Veuillez entrer une adresse valide.")
        return False
    
    with st.spinner("🔍 Géocodage de l'adresse en cours..."):
        lat, lon = geocode_address_france(address)
        
        if lat is None or lon is None:
            st.error("❌ Impossible de géocoder cette adresse.")
            return False
        
        try:
            sheet.append_row([address, lat, lon])
            st.success(f"✅ Adresse ajoutée avec succès ! (Lat: {lat:.6f}, Lon: {lon:.6f})")
            # Invalider le cache après ajout
            get_all_addresses.clear()
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
        # Invalider le cache après suppression
        get_all_addresses.clear()
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
        m = create_empty_france_map()
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    # Cas 3 : Une seule adresse
    if len(france_coords) == 1:
        row = france_coords.iloc[0]
        lat, lon = float(row['Latitude']), float(row['Longitude'])
        
        m = folium.Map(
            location=[lat, lon],
            zoom_start=14,
            tiles='OpenStreetMap'
        )
        create_marker(lat, lon, row['Adresse']).add_to(m)
    
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
            create_marker(
                float(row['Latitude']),
                float(row['Longitude']),
                row['Adresse']
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
    page = st.sidebar.radio(
        "Navigation",
        ["📍 Gestion des adresses", "🗺️ Carte interactive"],
        index=0
    )
    
    # PAGE 1 : Gestion des adresses
    if page == "📍 Gestion des adresses":
        st.header("📍 Gestion des adresses")
        
        # Formulaire d'ajout
        with st.form("add_address_form", clear_on_submit=True):
            st.subheader("➕ Ajouter une nouvelle adresse")
            new_address = st.text_input(
                "Adresse complète",
                placeholder="Ex: 10 boulevard Aristide Briand, 93100 Montreuil"
            )
            st.caption("💡 Pour de meilleurs résultats, incluez le code postal et la ville")
            submitted = st.form_submit_button("Ajouter l'adresse", use_container_width=True)
            
            if submitted:
                if add_address(sheet, new_address):
                    st.rerun()
        
        st.divider()
        
        # Affichage des adresses existantes
        st.subheader("📋 Liste des adresses")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=False)
            st.write(f"**Total : {len(df)} adresse(s)**")
            
            # Option de suppression
            with st.expander("🗑️ Supprimer une adresse"):
                selected_idx = st.selectbox(
                    "Sélectionnez une adresse à supprimer",
                    options=range(len(df)),
                    format_func=lambda x: f"{x+1}. {df.iloc[x]['Adresse']}"
                )
                
                if st.button("🗑️ Supprimer cette adresse", type="secondary"):
                    if delete_address(sheet, selected_idx):
                        st.rerun()
        else:
            st.info("📭 Aucune adresse enregistrée pour le moment.")
            st.markdown("**Exemples d'adresses à ajouter :**")
            st.code("10 boulevard Aristide Briand, 93100 Montreuil")
            st.code("21 rue des Petits Carreaux, 75002 Paris")
            st.code("1 Place de la Concorde, 75008 Paris")
            st.code("Tour Eiffel, 75007 Paris")
    
    # PAGE 2 : Carte
    elif page == "🗺️ Carte interactive":
        st.header("🗺️ Visualisation sur carte")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            st.success(f"📍 {len(df)} adresse(s) affichée(s) sur la carte")
            display_map(df)
            
            with st.expander("📊 Détails des adresses"):
                st.dataframe(df, use_container_width=True)
        else:
            st.info("📭 Aucune adresse à afficher. Ajoutez des adresses depuis la page 'Gestion des adresses'.")
            display_map(pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude']))

if __name__ == "__main__":
    main()
