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
    """Crée un marqueur Folium standardisé avec note optionnelle"""
    # Créer le texte du popup et tooltip
    if note:
        popup_text = f"<b>{address}</b><br><i>📝 {note}</i>"
        tooltip_text = f"{address} ({note})"
    else:
        popup_text = f"<b>{address}</b>"
        tooltip_text = address
    
    return folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(popup_text, max_width=300),
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
    page = st.sidebar.radio(
        "Navigation",
        ["📍 Gestion des adresses", "🗺️ Carte interactive"],
        index=0
    )
    
    # Bouton de rafraîchissement dans la sidebar
    if st.sidebar.button("🔄 Rafraîchir les données", help="Recharge les données depuis Google Sheets"):
        st.rerun()
    
    # PAGE 1 : Gestion des adresses
    if page == "📍 Gestion des adresses":
        st.header("📍 Gestion des adresses")
        
        # Mode de saisie : simple ou multiple
        input_mode = st.radio(
            "Mode de saisie",
            ["➕ Adresse simple", "📝 Adresses multiples"],
            horizontal=True
        )
        
        if input_mode == "➕ Adresse simple":
            # Formulaire d'ajout simple
            with st.form("add_address_form", clear_on_submit=True):
                st.subheader("➕ Ajouter une nouvelle adresse")
                
                col1, col2 = st.columns([3, 1])
                with col1:
                    new_address = st.text_input(
                        "Adresse complète",
                        placeholder="Ex: 10 boulevard Aristide Briand, 93100 Montreuil"
                    )
                with col2:
                    new_note = st.text_input(
                        "Note (optionnelle)",
                        placeholder="Ex: beau balcon"
                    )
                
                st.caption("💡 Pour de meilleurs résultats, incluez le code postal et la ville")
                submitted = st.form_submit_button("Ajouter l'adresse", use_container_width=True)
                
                if submitted:
                    if add_address(sheet, new_address, new_note):
                        st.rerun()
        
        else:  # Mode multiple
            # Formulaire d'ajout multiple
            with st.form("add_addresses_batch_form", clear_on_submit=True):
                st.subheader("📝 Ajouter plusieurs adresses")
                
                st.info("💡 **Format attendu :** Séparez les adresses par des virgules. Ajoutez des notes entre parenthèses.")
                st.caption("**Exemple :** Tour Eiffel, 75007 Paris (vue imprenable), 10 rue de Rivoli, 75001 Paris, Arc de Triomphe (monument historique)")
                
                batch_input = st.text_area(
                    "Adresses (séparées par des virgules)",
                    placeholder="Adresse 1 (note 1), Adresse 2, Adresse 3 (note 3)...",
                    height=150
                )
                
                submitted_batch = st.form_submit_button("Ajouter toutes les adresses", use_container_width=True)
                
                if submitted_batch and batch_input.strip():
                    # Parser les adresses
                    addresses_with_notes = parse_addresses_with_notes(batch_input)
                    
                    if addresses_with_notes:
                        st.info(f"📊 {len(addresses_with_notes)} adresse(s) détectée(s)")
                        
                        # Afficher un aperçu
                        with st.expander("👀 Aperçu des adresses à ajouter"):
                            for i, (addr, note) in enumerate(addresses_with_notes, 1):
                                if note:
                                    st.write(f"{i}. **{addr}** 📝 _{note}_")
                                else:
                                    st.write(f"{i}. **{addr}**")
                        
                        # Ajouter les adresses
                        results = add_addresses_batch(sheet, addresses_with_notes)
                        
                        # Afficher les résultats
                        if results['success']:
                            st.success(f"✅ {len(results['success'])} adresse(s) ajoutée(s) avec succès !")
                            with st.expander("✅ Adresses ajoutées"):
                                for addr, note in results['success']:
                                    if note:
                                        st.write(f"• {addr} 📝 _{note}_")
                                    else:
                                        st.write(f"• {addr}")
                        
                        if results['failed']:
                            st.error(f"❌ {len(results['failed'])} adresse(s) échouée(s)")
                            with st.expander("❌ Adresses échouées"):
                                for addr, note, reason in results['failed']:
                                    st.write(f"• {addr} - {reason}")
                        
                        if results['success']:
                            st.rerun()
                    else:
                        st.warning("⚠️ Aucune adresse valide détectée.")
        
        st.divider()
        
        # Affichage des adresses existantes
        st.subheader("📋 Liste des adresses")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            # Afficher avec les coordonnées normalisées
            display_df = df.copy()
            display_df['Latitude'] = display_df['Latitude'].apply(lambda x: f"{x:.6f}")
            display_df['Longitude'] = display_df['Longitude'].apply(lambda x: f"{x:.6f}")
            
            # Réorganiser les colonnes
            display_df = display_df[['Adresse', 'Note', 'Latitude', 'Longitude']]
            
            st.dataframe(display_df, use_container_width=True, hide_index=False)
            st.write(f"**Total : {len(df)} adresse(s)**")
            
            # Option de suppression
            with st.expander("🗑️ Supprimer une adresse"):
                selected_idx = st.selectbox(
                    "Sélectionnez une adresse à supprimer",
                    options=range(len(df)),
                    format_func=lambda x: f"{x+1}. {df.iloc[x]['Adresse']}" + 
                                         (f" ({df.iloc[x]['Note']})" if df.iloc[x]['Note'] else "")
                )
                
                if st.button("🗑️ Supprimer cette adresse", type="secondary"):
                    if delete_address(sheet, selected_idx):
                        st.rerun()
        else:
            st.info("📭 Aucune adresse enregistrée pour le moment.")
            st.markdown("**Exemples d'adresses à ajouter :**")
            st.code("Tour Eiffel, 75007 Paris (vue imprenable), 10 rue de Rivoli, 75001 Paris, Arc de Triomphe (monument historique)")
    
    # PAGE 2 : Carte
    elif page == "🗺️ Carte interactive":
        st.header("🗺️ Visualisation sur carte")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            # Diagnostic des coordonnées
            valid_coords = df[
                df['Latitude'].between(FRANCE_LAT_MIN, FRANCE_LAT_MAX) &
                df['Longitude'].between(FRANCE_LON_MIN, FRANCE_LON_MAX)
            ]
            
            st.success(f"📍 {len(valid_coords)} adresse(s) affichée(s) sur {len(df)} totale(s)")
            
            if len(valid_coords) < len(df):
                st.warning(f"⚠️ {len(df) - len(valid_coords)} adresse(s) hors de France métropolitaine (non affichée(s))")
            
            display_map(df)
            
            with st.expander("📊 Détails des adresses"):
                display_df = df.copy()
                display_df['Latitude'] = display_df['Latitude'].apply(lambda x: f"{x:.6f}")
                display_df['Longitude'] = display_df['Longitude'].apply(lambda x: f"{x:.6f}")
                display_df = display_df[['Adresse', 'Note', 'Latitude', 'Longitude']]
                st.dataframe(display_df, use_container_width=True)
        else:
            st.info("📭 Aucune adresse à afficher. Ajoutez des adresses depuis la page 'Gestion des adresses'.")
            display_map(pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note']))

if __name__ == "__main__":
    main()
