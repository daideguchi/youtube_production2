#!/usr/bin/env python3
"""
Minimal Streamlit app to test tab duplication issue
"""
import streamlit as st

# Basic config
st.set_page_config(page_title="Test App", layout="wide")

# Title
st.title("🎬 Test App - Tab Duplication Debug")
st.markdown("**Testing for tab duplication issues**")

# Create tabs
tab1, tab2 = st.tabs(["🚀 Tab 1", "🎨 Tab 2"])

with tab1:
    st.header("Tab 1 Content")
    st.write("This is tab 1 content")
    
with tab2:
    st.header("Tab 2 Content")
    st.write("This is tab 2 content")

# End of file - no additional content outside tabs