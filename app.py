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
            st.warning(f"⚠️ {message}")
        
        try:
            sheet.append_row([address, float(lat), float(lon), note], value_input_option='USER_ENTERED')
            if note:
                st.success(f"✅ Adresse ajoutée : {address} (📝 {note})")
            else:
                st.success(f"✅ Adresse ajoutée : {address}")
            st.info(f"📍 Coordonnées: {lat:.6f}, {lon:.6f}")
            return True
        except Exception as e:
            st.error(f"❌ Erreur : {e}")
            return False
