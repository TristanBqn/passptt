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
    """
    Corrige automatiquement les longitudes incorrectes pour Paris
    Retourne la longitude corrigée
    """
    # Vérifier si c'est une adresse parisienne avec longitude suspecte
    if lat and lon:
        if ("paris" in str(address).lower() or "75" in str(address)) and (0 < lon < 1):
            # Probable perte du chiffre '2' au début
            corrected_lon = lon + 2
            if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                return corrected_lon
    return lon

def validate_france_coordinates(lat, lon, address=""):
    """
    Valide les coordonnées pour la France avec détection d'anomalies
    Retourne (lat, lon, is_valid, error_message)
    """
    if lat is None or lon is None:
        return lat, lon, False, "Coordonnées nulles"
    
    # Vérifier latitude
    if not (FRANCE_LAT_MIN <= lat <= FRANCE_LAT_MAX):
        return lat, lon, False, f"Latitude {lat:.6f} hors de France"
    
    # Vérifier longitude avec correction automatique pour Paris
    if not (FRANCE_LON_MIN <= lon <= FRANCE_LON_MAX):
        # Détection spéciale: longitude trop faible pour Paris
        if "paris" in address.lower() or "75" in address:
            if 0 < lon < 1:
                # Probable perte du chiffre au début (ex: 0.227 au lieu de 2.227)
                corrected_lon = lon + 2
                if FRANCE_LON_MIN <= corrected_lon <= FRANCE_LON_MAX:
                    return lat, corrected_lon, True, f"⚠️ Correction longitude: {lon:.6f} → {corrected_lon:.6f}"
        
        return lat, lon, False, f"Longitude {lon:.6f} hors de France"
    
    return lat, lon, True, ""

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
    """Crée un marqueur Folium avec Street View"""
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
        
        try:
            headers = sheet.row_values(1)
            if not headers or headers != ['Adresse', 'Latitude', 'Longitude', 'Note']:
                sheet.update('A1:D1', [['Adresse', 'Latitude', 'Longitude', 'Note']])
        except:
            sheet.update('A1:D1', [['Adresse', 'Latitude', 'Longitude', 'Note']])
        
        return sheet
    except Exception as e:
        st.error(f"❌ Erreur de connexion au Google Sheet : {e}")
        st.info("Assurez-vous que les secrets sont correctement configurés.")
        return None

def get_all_addresses(sheet):
    """Récupère toutes les adresses depuis le Google Sheet avec correction automatique"""
    try:
        data = sheet.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if not df.empty:
                if 'Note' not in df.columns:
                    df['Note'] = ''
                
                # Normaliser les coordonnées
                df['Latitude'] = df['Latitude'].apply(normalize_coordinate)
                df['Longitude'] = df['Longitude'].apply(normalize_coordinate)
                df = df.dropna(subset=['Latitude', 'Longitude'])
                df['Note'] = df['Note'].fillna('')
                
                # NOUVEAU: Corriger automatiquement les longitudes parisiennes
                df['Longitude'] = df.apply(
                    lambda row: correct_paris_longitude(row['Latitude'], row['Longitude'], row['Adresse']),
                    axis=1
                )
                
                return df
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])
    except Exception as e:
        st.error(f"❌ Erreur lors de la récupération des données : {e}")
        return pd.DataFrame(columns=['Adresse', 'Latitude', 'Longitude', 'Note'])

# ============================================================================
# GÉOCODAGE
# ============================================================================

def try_api_adresse(address):
    """Tente de géocoder avec l'API Adresse officielle"""
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
                score = properties.get('score', 0)
                
                if not is_in_france(lat, lon):
                    return None
                
                if score >= GEOCODE_SCORE_MIN or score >= GEOCODE_SCORE_FALLBACK:
                    return lat, lon
    except Exception:
        pass
    
    return None

def try_photon_api(address):
    """Tente de géocoder avec Photon API"""
    try:
        params = {'q': address, 'limit': 1, 'lang': 'fr', 'location_bias_scale': 0.5}
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
    """Convertit une adresse française en coordonnées"""
    if not address.strip():
        return None, None
    
    result = try_api_adresse(address)
    if result:
        return result
    
    result = try_photon_api(address)
    if result:
        return result
    
    return None, None

# ============================================================================
# GESTION DES ADRESSES
# ============================================================================

def add_addresses_batch(sheet, addresses_with_notes):
    """Ajoute plusieurs adresses en mode batch"""
    results = {'success': [], 'failed': [], 'corrected': []}
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, (address, note) in enumerate(addresses_with_notes):
        progress = (i + 1) / len(addresses_with_notes)
        progress_bar.progress(progress)
        status_text.text(f"Géocodage en cours... ({i+1}/{len(addresses_with_notes)})")
        
        lat, lon = geocode_address_france(address)
        
        if lat is not None and lon is not None:
            # Valider et corriger si nécessaire
            lat, lon, is_valid, message = validate_france_coordinates(lat, lon, address)
            
            if is_valid:
                try:
                    sheet.append_row([address, float(lat), float(lon), note], value_input_option='USER_ENTERED')
                    if message:  # Correction appliquée
                        results['corrected'].append((address, note, message))
                    else:
                        results['success'].append((address, note))
                except Exception as e:
                    results['failed'].append((address, note, f"Erreur: {e}"))
            else:
                results['failed'].append((address, note, message))
        else:
            results['failed'].append((address, note, "Géocodage échoué"))
    
    progress_bar.empty()
    status_text.empty()
    
    return results

def add_address(sheet, address, note=""):
    """Ajoute une nouvelle adresse avec validation"""
    if not address.strip():
        st.warning("⚠️ Veuillez entrer une adresse valide.")
        return False
    
    with st.spinner("🔍 Géocodage en cours..."):
        lat, lon = geocode_address_france(address)
        
        if lat is None or lon is None:
            st.error(f"❌ Impossible de géocoder : {address}")
            return False
        
        # Validation et correction
        lat, lon, is_valid, message = validate_france_coordinates(lat, lon, address)
        
        if not is_valid:
            st.error(f"❌ Coordonnées invalides : {message}")
            return False
        
        if message:  # Correction appliquée
            st.warning(message)
        
        try:
            sheet.append_row([address, float(lat), float(lon), note], value_input_option='USER_ENTERED')
            if note:
                st.success(f"✅ Adresse ajoutée : {address} (📝 {note})")
            else:
                st.success(f"✅ Adresse ajoutée : {address}")
            st.info(f"📍 Coordonnées: Lat {lat:.6f}, Lon {lon:.6f}")
            return True
        except Exception as e:
            st.error(f"❌ Erreur : {e}")
            return False

def delete_address(sheet, index):
    """Supprime une adresse"""
    try:
        sheet.delete_rows(index + 2)
        st.success("✅ Adresse supprimée !")
        return True
    except Exception as e:
        st.error(f"❌ Erreur : {e}")
        return False

# ============================================================================
# VISUALISATION CARTE
# ============================================================================

def display_map(df):
    """Affiche les adresses sur une carte"""
    if df.empty:
        st.info("📭 Aucune adresse à afficher.")
        m = create_empty_france_map()
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    france_coords = df[
        df['Latitude'].between(FRANCE_LAT_MIN, FRANCE_LAT_MAX) &
        df['Longitude'].between(FRANCE_LON_MIN, FRANCE_LON_MAX)
    ].copy()
    
    if france_coords.empty:
        st.warning("⚠️ Aucune coordonnée valide en France métropolitaine.")
        st.info("💡 Les coordonnées sont automatiquement corrigées à l'affichage.")
        
        with st.expander("🔍 Diagnostic des coordonnées"):
            diag_df = df[['Adresse', 'Latitude', 'Longitude', 'Note']].copy()
            st.dataframe(diag_df, use_container_width=True)
        
        m = create_empty_france_map()
        st_folium(m, width=1400, height=600, returned_objects=[])
        return
    
    if len(france_coords) == 1:
        row = france_coords.iloc[0]
        lat, lon = float(row['Latitude']), float(row['Longitude'])
        note = row.get('Note', '')
        
        m = folium.Map(location=[lat, lon], zoom_start=14, tiles='OpenStreetMap')
        create_marker(lat, lon, row['Adresse'], note).add_to(m)
    else:
        center_lat = france_coords['Latitude'].mean()
        center_lon = france_coords['Longitude'].mean()
        
        m = folium.Map(location=[center_lat, center_lon], zoom_start=8, tiles='OpenStreetMap')
        
        for _, row in france_coords.iterrows():
            note = row.get('Note', '')
            create_marker(float(row['Latitude']), float(row['Longitude']), row['Adresse'], note).add_to(m)
        
        sw = france_coords[['Latitude', 'Longitude']].min().values.tolist()
        ne = france_coords[['Latitude', 'Longitude']].max().values.tolist()
        m.fit_bounds([sw, ne], padding=[30, 30])
    
    st_folium(m, width=1400, height=600, returned_objects=[])

# ============================================================================
# INTERFACE PRINCIPALE
# ============================================================================

def main():
    st.title("🏠 Gestion de mes pass PTT et codes")
    st.caption("Propriété intellectuelle de Tristan BANNIER")
    
    sheet = connect_to_google_sheet()
    if sheet is None:
        st.stop()
    
    page = st.sidebar.radio("Navigation", ["📍 Gestion des adresses", "🗺️ Carte interactive"], index=0)
    
    if st.sidebar.button("🔄 Rafraîchir les données"):
        st.rerun()
    
    if page == "📍 Gestion des adresses":
        st.header("📍 Gestion des adresses")
        
        input_mode = st.radio("Mode de saisie", ["➕ Adresse simple", "📝 Adresses multiples"], horizontal=True)
        
        if input_mode == "➕ Adresse simple":
            with st.form("add_address_form", clear_on_submit=True):
                st.subheader("➕ Ajouter une nouvelle adresse")
                
                col1, col2 = st.columns([3, 1])
                with col1:
                    new_address = st.text_input("Adresse complète", placeholder="Ex: Tour Eiffel, 75007 Paris")
                with col2:
                    new_note = st.text_input("Note (optionnelle)", placeholder="Ex: beau balcon")
                
                st.caption("💡 Inclure le code postal et la ville pour de meilleurs résultats")
                submitted = st.form_submit_button("Ajouter l'adresse", use_container_width=True)
                
                if submitted:
                    if add_address(sheet, new_address, new_note):
                        st.rerun()
        else:
            with st.form("add_addresses_batch_form", clear_on_submit=True):
                st.subheader("📝 Ajouter plusieurs adresses")
                
                st.info("💡 Séparez les adresses par des virgules. Ajoutez des notes entre parenthèses.")
                st.caption("**Exemple :** Tour Eiffel (vue imprenable), Arc de Triomphe, Louvre (musée)")
                
                batch_input = st.text_area("Adresses", placeholder="Adresse 1 (note), Adresse 2...", height=150)
                
                submitted_batch = st.form_submit_button("Ajouter toutes les adresses", use_container_width=True)
                
                if submitted_batch and batch_input.strip():
                    addresses_with_notes = parse_addresses_with_notes(batch_input)
                    
                    if addresses_with_notes:
                        st.info(f"📊 {len(addresses_with_notes)} adresse(s) détectée(s)")
                        
                        with st.expander("👀 Aperçu des adresses"):
                            for i, (addr, note) in enumerate(addresses_with_notes, 1):
                                if note:
                                    st.write(f"{i}. **{addr}** 📝 _{note}_")
                                else:
                                    st.write(f"{i}. **{addr}**")
                        
                        results = add_addresses_batch(sheet, addresses_with_notes)
                        
                        if results['success']:
                            st.success(f"✅ {len(results['success'])} adresse(s) ajoutée(s) !")
                            with st.expander("✅ Adresses ajoutées"):
                                for addr, note in results['success']:
                                    st.write(f"• {addr}" + (f" 📝 _{note}_" if note else ""))
                        
                        if results['corrected']:
                            st.warning(f"⚠️ {len(results['corrected'])} adresse(s) corrigée(s)")
                            with st.expander("⚠️ Corrections appliquées"):
                                for addr, note, msg in results['corrected']:
                                    st.write(f"• {addr}: {msg}")
                        
                        if results['failed']:
                            st.error(f"❌ {len(results['failed'])} adresse(s) échouée(s)")
                            with st.expander("❌ Échecs"):
                                for addr, note, reason in results['failed']:
                                    st.write(f"• {addr} - {reason}")
                        
                        if results['success'] or results['corrected']:
                            st.rerun()
                    else:
                        st.warning("⚠️ Aucune adresse détectée.")
        
        st.divider()
        
        st.subheader("📋 Liste des adresses")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            display_df = df.copy()
            display_df['Latitude'] = display_df['Latitude'].apply(lambda x: f"{x:.6f}")
            display_df['Longitude'] = display_df['Longitude'].apply(lambda x: f"{x:.6f}")
            display_df = display_df[['Adresse', 'Note', 'Latitude', 'Longitude']]
            
            st.dataframe(display_df, use_container_width=True, hide_index=False)
            st.write(f"**Total : {len(df)} adresse(s)**")
            
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
            st.info("📭 Aucune adresse enregistrée.")
            st.markdown("**Exemples à essayer :**")
            st.code("Tour Eiffel (vue imprenable), Louvre (musée), Arc de Triomphe")
    
    elif page == "🗺️ Carte interactive":
        st.header("🗺️ Visualisation sur carte")
        df = get_all_addresses(sheet)
        
        if not df.empty:
            valid_coords = df[
                df['Latitude'].between(FRANCE_LAT_MIN, FRANCE_LAT_MAX) &
                df['Longitude'].between(FRANCE_LON_MIN, FRANCE_LON_MAX)
            ]
            
            st.success(f"📍 {len(valid_coords)} adresse(s) affichée(s) sur {len(df)} totale(s)")
            
            if len(valid_coords) < len(df):
                st.warning(f"⚠️ {len(df) - len(valid_coords)} adresse(s) hors France (coordonnées invalides)")
            
            st.info("💡 **Cliquer sur un marqueur** pour voir les détails et accéder à Street View. Les coordonnées sont automatiquement corrigées à l'affichage.")
            
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
