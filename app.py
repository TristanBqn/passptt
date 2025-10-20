import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import folium
from streamlit_folium import st_folium
import time
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

def geocode_address_france(address):
    """Convertit une adresse française en coordonnées géographiques avec l'API Adresse (data.gouv.fr)"""
    
    if not address.strip():
        st.error("❌ Veuillez entrer une adresse valide.")
        return None, None
    
    # Méthode 1 : API Adresse officielle France (data.gouv.fr) - GRATUITE et SANS LIMITE
    try:
        url = "https://api-adresse.data.gouv.fr/search/"
        params = {
            'q': address,
            'limit': 1
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('features') and len(data['features']) > 0:
                feature = data['features'][0]
                coords = feature['geometry']['coordinates']  # [longitude, latitude]
                full_address = feature['properties'].get('label', address)
                score = feature['properties'].get('score', 0)
                
                lat = coords[1]
                lon = coords[0]
                
                # VALIDATION STRICTE : Vérifier que c'est en France métropolitaine
                if not (41 <= lat <= 51.5 and -5.5 <= lon <= 10):
                    st.error("❌ Cette adresse ne semble pas être en France métropolitaine.")
                    st.info(f"Coordonnées trouvées : Lat {lat:.4f}, Lon {lon:.4f}")
                    return None, None
                
                # Vérifier la qualité du résultat
                if score >= 0.4:  # Score de confiance minimum
                    st.success(f"✅ Adresse trouvée : {full_address} (confiance: {score:.2f})")
                    return lat, lon
                else:
                    st.warning(f"⚠️ Adresse trouvée avec un faible score de confiance ({score:.2f})")
                    st.info(f"Adresse suggérée : {full_address}")
                    # Permettre quand même si les coordonnées sont en France
                    if score >= 0.3:
                        return lat, lon
                    else:
                        st.error("Score trop faible, adresse rejetée.")
                        return None, None
                    
    except Exception as e:
        st.error(f"❌ Erreur API Adresse : {str(e)}")
    
    # Méthode 2 : Photon API (komoot) - Alternative gratuite
    try:
        url = "https://photon.komoot.io/api/"
        params = {
            'q': address,
            'limit': 1,
            'lang': 'fr',
            'location_bias_scale': 0.5
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('features') and len(data['features']) > 0:
                feature = data['features'][0]
                coords = feature['geometry']['coordinates']  # [longitude, latitude]
                properties = feature['properties']
                
                lat = coords[1]
                lon = coords[0]
                
                # Vérifier strictement que c'est en France
                if 41 <= lat <= 51.5 and -5.5 <= lon <= 10:
                    country = properties.get('country', '')
                    if country.lower() in ['france', 'fr', '']:
                        st.info(f"📍 Adresse trouvée via Photon API")
                        return lat, lon
                    else:
                        st.warning(f"⚠️ Pays détecté : {country}, pas en France")
                else:
                    st.warning(f"⚠️ Coordonnées hors France : Lat {lat:.4f}, Lon {lon:.4f}")
                    
    except Exception as e:
        st.warning(f"⚠️ Erreur Photon API : {str(e)}")
    
    st.error("❌ Impossible de géocoder cette adresse en France.")
    st.info("💡 Astuce : Essayez avec le format complet : numéro + rue + code postal + ville")
    st.info("Exemple : 10 rue de la Paix, 75002 Paris")
    return None, None

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
        m = folium.Map(
            location=FRANCE_CENTER, 
            zoom_start=FRANCE_ZOOM,
            tiles='OpenStreetMap'
        )
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    # Vérifier qu'il y a des coordonnées valides
    valid_coords = df[['Latitude', 'Longitude']].notna().all(axis=1)
    df_valid = df[valid_coords]
    
    if df_valid.empty:
        st.warning("⚠️ Aucune coordonnée valide trouvée.")
        # Afficher quand même une carte de la France
        m = folium.Map(
            location=FRANCE_CENTER, 
            zoom_start=FRANCE_ZOOM,
            tiles='OpenStreetMap'
        )
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    # Vérifier si au moins une coordonnée est en France métropolitaine
    france_coords = df_valid[
        (df_valid['Latitude'] >= 41) & 
        (df_valid['Latitude'] <= 51.5) & 
        (df_valid['Longitude'] >= -5.5) & 
        (df_valid['Longitude'] <= 10)
    ]
    
    if france_coords.empty:
        st.error("⚠️ Aucune des adresses ne semble être en France métropolitaine.")
        st.info("Vérifiez que les adresses ont été correctement géocodées.")
    
    # Cas 1 : Une seule adresse
    if len(df_valid) == 1:
        lat = float(df_valid.iloc[0]['Latitude'])
        lon = float(df_valid.iloc[0]['Longitude'])
        
        # Créer la carte centrée sur cette adresse
        m = folium.Map(
            location=[lat, lon],
            zoom_start=14,  # Zoom plus proche pour voir le détail
            tiles='OpenStreetMap'
        )
        
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(f"<b>{df_valid.iloc[0]['Adresse']}</b>", max_width=300),
            tooltip=df_valid.iloc[0]['Adresse'],
            icon=folium.Icon(color='red', icon='home', prefix='fa')
        ).add_to(m)
    
    # Cas 2 : Plusieurs adresses
    else:
        # Calculer le centre moyen des coordonnées
        center_lat = df_valid['Latitude'].mean()
        center_lon = df_valid['Longitude'].mean()
        
        # Créer la carte centrée sur la moyenne
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=8,
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
        
        # Ajuster les limites pour inclure tous les points
        sw = df_valid[['Latitude', 'Longitude']].min().values.tolist()
        ne = df_valid[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne], padding=[30, 30])
    
    # Afficher la carte
    st_folium(m, width=1400, height=600, returned_objects=[])

# Interface principale
def main():
    st.title("🏠 Application de Gestion d'Adresses Françaises")
    st.caption("Utilise l'API Adresse officielle du gouvernement français (data.gouv.fr)")
    
    # Connexion au Google Sheet
    sheet = connect_to_google_sheet()
    
    if sheet is None:
        st.stop()
    
    # Sélection de la page
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
                    time.sleep(1)
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
                            time.sleep(1)
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
            
            # Afficher les détails sous la carte
            with st.expander("📊 Détails des adresses"):
                st.dataframe(df, use_container_width=True)
        else:
            st.info("📭 Aucune adresse à afficher. Ajoutez des adresses depuis la page 'Gestion des adresses'.")
            # Afficher quand même une carte de la France vide
            display_map(pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude']))

if __name__ == "__main__":
    main()
