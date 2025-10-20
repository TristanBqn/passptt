import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import folium
from streamlit_folium import st_folium
import time

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
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
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
        st.info("Assurez-vous que le fichier credentials.json est présent dans le même répertoire.")
        return None

def geocode_address(address):
    """Convertit une adresse en coordonnées géographiques"""
    try:
        geolocator = Nominatim(user_agent="streamlit_address_app")
        time.sleep(1)  # Respecter les limites de l'API
        location = geolocator.geocode(address)
        
        if location:
            return location.latitude, location.longitude
        else:
            return None, None
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        st.warning(f"⚠️ Erreur de géocodage : {e}")
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
    """Affiche les adresses sur une carte Folium"""
    if df.empty:
        st.info("📭 Aucune adresse à afficher sur la carte.")
        return
    
    # Calculer le centre de la carte
    center_lat = df['Latitude'].mean()
    center_lon = df['Longitude'].mean()
    
    # Créer la carte
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)
    
    # Ajouter les marqueurs
    for idx, row in df.iterrows():
        folium.Marker(
            location=[row['Latitude'], row['Longitude']],
            popup=f"<b>{row['Adresse']}</b>",
            tooltip=row['Adresse'],
            icon=folium.Icon(color='red', icon='home', prefix='fa')
        ).add_to(m)
    
    # Ajuster le zoom pour inclure tous les points
    if len(df) > 1:
        sw = df[['Latitude', 'Longitude']].min().values.tolist()
        ne = df[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne])
    
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

if __name__ == "__main__":
    main()
