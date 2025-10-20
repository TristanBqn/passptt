import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import folium
from streamlit_folium import st_folium
import time
import json
import requests

# Configuration de la page
st.set_page_config(
    page_title="Gestion d'adresses",
    page_icon="📍",
    layout="wide"
)

# URL du Google Sheet
SHEET_URL = "https://docs.google.com/spreadsheets/d/1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw/edit?gid=0#gid=0"
SHEET_ID = "1ADrY7zDRoDnn_7piQc-xQzMrHgFvqfG1I6YtvOYM4xw"

@st.cache_resource
def connect_to_google_sheet():
    """Initialise la connexion au Google Sheet"""
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        # Utiliser les secrets Streamlit Cloud avec google-auth
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        # Vérifier si les en-têtes existent, sinon les créer
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

def geocode_address(address):
    """Convertit une adresse française en coordonnées géographiques"""
    
    # Ajouter ", France" si pas déjà présent pour améliorer la précision
    if "france" not in address.lower():
        address_with_country = f"{address}, France"
    else:
        address_with_country = address
    
    # Méthode 1 : Nominatim avec restriction à la France
    try:
        geolocator = Nominatim(
            user_agent="streamlit_address_manager_france_v1.0",
            timeout=10
        )
        time.sleep(1)  # Respecter la politique d'usage de Nominatim
        
        # Utiliser countrycodes pour limiter à la France
        location = geolocator.geocode(
            address_with_country,
            country_codes=['fr'],  # Restriction à la France
            addressdetails=True,
            language='fr'
        )
        
        if location:
            st.info(f"📍 Adresse trouvée : {location.address}")
            return location.latitude, location.longitude
            
    except Exception as e:
        st.warning(f"⚠️ Nominatim n'a pas fonctionné : {str(e)}")
    
    # Méthode 2 : API directe OpenStreetMap avec restriction France
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address_with_country,
            'format': 'json',
            'limit': 1,
            'countrycodes': 'fr',  # Restriction à la France
            'addressdetails': 1
        }
        headers = {
            'User-Agent': 'StreamlitAddressManager/1.0'
        }
        
        time.sleep(1)  # Respecter la politique d'usage
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                st.info(f"📍 Adresse trouvée : {data[0].get('display_name', '')}")
                return float(data[0]['lat']), float(data[0]['lon'])
                
    except Exception as e:
        st.warning(f"⚠️ API Nominatim n'a pas fonctionné : {str(e)}")
    
    # Méthode 3 : Photon avec restriction France (fallback)
    try:
        geolocator = Photon(user_agent="streamlit_address_manager", timeout=10)
        location = geolocator.geocode(address_with_country)
        
        if location:
            # Vérifier que c'est bien en France (latitude entre 41 et 51, longitude entre -5 et 10)
            if 41 <= location.latitude <= 51 and -5 <= location.longitude <= 10:
                st.info(f"📍 Adresse trouvée via Photon")
                return location.latitude, location.longitude
            else:
                st.warning("⚠️ Les coordonnées trouvées ne semblent pas être en France")
                
    except Exception as e:
        st.warning(f"⚠️ Photon n'a pas fonctionné : {str(e)}")
    
    st.error("❌ Impossible de géocoder cette adresse. Vérifiez qu'elle est complète et valide.")
    return None, None

def add_address(sheet, address):
    """Ajoute une nouvelle adresse dans le Google Sheet"""
    if not address.strip():
        st.warning("⚠️ Veuillez entrer une adresse valide.")
        return False
    
    with st.spinner("🔍 Géocodage de l'adresse en cours..."):
        lat, lon = geocode_address(address)
    
    if lat is None or lon is None:
        st.error("❌ Impossible de géocoder cette adresse. Vérifiez qu'elle est valide.")
        return False
    
    try:
        sheet.append_row([address, lat, lon])
        st.success(f"✅ Adresse ajoutée avec succès ! (Lat: {lat:.6f}, Lon: {lon:.6f})")
        return True
    except Exception as e:
        st.error(f"❌ Erreur lors de l'ajout : {e}")
        return False

def get_all_addresses(sheet):
    """Récupère toutes les adresses depuis le Google Sheet"""
    try:
        data = sheet.get_all_records()
        if data:
            df = pd.DataFrame(data)
            # Convertir Latitude et Longitude en nombres
            if not df.empty:
                df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
                df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
                # Supprimer les lignes avec des coordonnées invalides
                df = df.dropna(subset=['Latitude', 'Longitude'])
            return df
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude'])
    except Exception as e:
        st.error(f"❌ Erreur lors de la récupération des données : {e}")
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude'])

def delete_address(sheet, index):
    """Supprime une adresse du Google Sheet (index commence à 0 pour les données)"""
    try:
        # +2 car : +1 pour l'en-tête, +1 car gspread commence à 1
        sheet.delete_rows(index + 2)
        st.success("✅ Adresse supprimée avec succès !")
        return True
    except Exception as e:
        st.error(f"❌ Erreur lors de la suppression : {e}")
        return False

def display_map(df):
    """Affiche les adresses sur une carte Folium centrée sur la France"""
    
    # Coordonnées du centre de la France métropolitaine
    FRANCE_CENTER = [46.603354, 1.888334]
    FRANCE_ZOOM = 6
    
    if df.empty:
        st.info("📭 Aucune adresse à afficher sur la carte.")
        # Afficher quand même une carte de la France
        m = folium.Map(location=FRANCE_CENTER, zoom_start=FRANCE_ZOOM)
        st_folium(m, width=1400, height=600)
        return
    
    # Vérifier qu'il y a des coordonnées valides
    valid_coords = df[['Latitude', 'Longitude']].notna().all(axis=1)
    df_valid = df[valid_coords]
    
    if df_valid.empty:
        st.warning("⚠️ Aucune coordonnée valide trouvée.")
        # Afficher quand même une carte de la France
        m = folium.Map(location=FRANCE_CENTER, zoom_start=FRANCE_ZOOM)
        st_folium(m, width=1400, height=600)
        return
    
    # Créer la carte centrée sur la France
    m = folium.Map(
        location=FRANCE_CENTER,
        zoom_start=FRANCE_ZOOM,
        tiles='OpenStreetMap'
    )
    
    # Ajouter les marqueurs
    for idx, row in df_valid.iterrows():
        folium.Marker(
            location=[float(row['Latitude']), float(row['Longitude'])],
            popup=folium.Popup(f"<b>{row['Adresse']}</b>", max_width=300),
            tooltip=row['Adresse'],
            icon=folium.Icon(color='red', icon='home', prefix='fa')
        ).add_to(m)
    
    # Ajuster le zoom pour inclure tous les points (si plusieurs adresses)
    if len(df_valid) > 1:
        # Calculer les limites pour inclure tous les points
        sw = df_valid[['Latitude', 'Longitude']].min().values.tolist()
        ne = df_valid[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne], padding=[50, 50])
    elif len(df_valid) == 1:
        # Centrer sur le seul point avec un zoom approprié
        m = folium.Map(
            location=[float(df_valid.iloc[0]['Latitude']), 
                     float(df_valid.iloc[0]['Longitude'])],
            zoom_start=13
        )
        folium.Marker(
            location=[float(df_valid.iloc[0]['Latitude']), 
                     float(df_valid.iloc[0]['Longitude'])],
            popup=folium.Popup(f"<b>{df_valid.iloc[0]['Adresse']}</b>", max_width=300),
            tooltip=df_valid.iloc[0]['Adresse'],
            icon=folium.Icon(color='red', icon='home', prefix='fa')
        ).add_to(m)
    
    # Afficher la carte
    st_folium(m, width=1400, height=600)

# Interface principale
def main():
    st.title("🏠 Application de Gestion d'Adresses")
    
    # Connexion au Google Sheet
    sheet = connect_to_google_sheet()
    
    if sheet is None:
        st.stop()
    
    # Sélection de la page
    page = st.sidebar.radio(
        "Navigation",
        ["📍 Gestion des adresses", "🗺️ Carte Google Maps"],
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
                placeholder="Ex: 10 boulevard Aristide Briand, Montreuil"
            )
            
            submitted = st.form_submit_button("Ajouter l'adresse", use_container_width=True)
            
            if submitted:
                if add_address(sheet, new_address):
                    st.rerun()
        
        st.divider()
        
        # Affichage des adresses existantes
        st.subheader("📋 Liste des adresses")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=False
            )
            st.write(f"**Total : {len(df)} adresse(s)**")
            
            # Option de suppression
            with st.expander("🗑️ Supprimer une adresse"):
                if len(df) > 0:
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
            st.code("10 boulevard Aristide Briand, Montreuil")
            st.code("21 rue des Petits Carreaux, Paris")
            st.code("40 rue d'Aboukir, Paris")
    
    # PAGE 2 : Carte
    elif page == "🗺️ Carte Google Maps":
        st.header("🗺️ Visualisation sur carte")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            st.success(f"📍 {len(df)} adresse(s) affichée(s) sur la carte")
            display_map(df)
            
            # Afficher les détails sous la carte
            with st.expander("📊 Détails des adresses"):
                st.dataframe(df, use_container_width=True)
        else:
            st.info("📭 Aucune adresse à afficher. Ajoutez des adresses depuis la page 'Gestion des adresses'.")
            # Afficher quand même une carte de la France vide
            display_map(pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude']))

if __name__ == "__main__":
    main()
