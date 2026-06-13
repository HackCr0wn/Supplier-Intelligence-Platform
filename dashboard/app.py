
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

##Page config (must be first st command)
st.set_page_config(
    page_title="Supplier Intelligence Platform",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

##Custom CSS
st.markdown("""
<style>
    /* Main header */
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        color: #1B2A4A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.0rem;
        color: #6B7280;
        margin-bottom: 2rem;
    }

    /* Sidebar styling */
    
    [data-testid="stSidebar"] {
        background-color: #1B2A4A;
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h5,
    [data-testid="stSidebar"] .stMarkdown small,
    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] span {
        color: #FFFFFF !important;
    }

    /* Fix dropdown text visibility on dark sidebar */
    [data-testid="stSidebar"] [data-baseweb="select"] {
        background-color: #FFFFFF !important;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] * {
        color: #1B2A4A !important;
    }
    [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {
        background-color: #3B82F6 !important;
        color: #FFFFFF !important;
    }
    [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] * {
        color: #FFFFFF !important;
    }
    
    [data-testid="stSidebar"] [role="radiogroup"] label p {
        color: #FFFFFF !important;
        font-size: 1rem !important;
        font-weight: 600 !important;
    }
    
    [data-testid="stSidebar"] button {
        background-color: #EF4444 !important;
        color: #FFFFFF !important;
        border: none !important;
        font-weight: 600 !important;
    }
    [data-testid="stSidebar"] button p,
    [data-testid="stSidebar"] button span {
        color: #FFFFFF !important;
    }



    /* Metric cards */
    [data-testid="stMetric"] {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    [data-testid="stMetric"] label {
        color: #6B7280 !important;
        font-size: 0.85rem !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #1B2A4A !important;
        font-weight: 700 !important;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #F1F5F9;
        padding: 4px;
        border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 20px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1B2A4A !important;
        color: white !important;
    }

    /* Dataframe styling */
    .stDataFrame {
        border-radius: 10px;
        overflow: hidden;
    }

    /* Expander */
    .streamlit-expanderHeader {
        font-weight: 600;
        color: #1B2A4A;
    }

    /* Hide default streamlit branding */
    
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

</style>
""", unsafe_allow_html=True)

##Sidebar Navigation
st.sidebar.markdown("## 🏭 SIP")
st.sidebar.markdown("##### Supplier Intelligence Platform")
#Alert in sidebar
try:
    from sqlalchemy import text as sql_text
    from utils.db import get_engine
    _engine = get_engine()

    with _engine.connect() as _conn:
        open_alerts = _conn.execute(sql_text(
            "SELECT COUNT(*) FROM price_alerts WHERE status = 'OPEN'"
        )).scalar()

    if open_alerts and open_alerts > 0:
        st.sidebar.error(f"**{open_alerts} Active Alert(s)**")
        if st.sidebar.button(f"View {open_alerts} Alert(s)", key="alert_btn"):
            st.session_state["nav_to_alerts"] = True
            st.rerun()
except Exception:
    pass

st.sidebar.markdown("---")



nav_options = ["Overview", "Market Index", "Material Analysis",
               "TLC Calculator", "Forecasting", "Regression",
               "Chatbot", "Upload", "Alerts"]

# Handle alert redirect
if st.session_state.get("nav_to_alerts"):
    st.session_state["nav_radio"] = "Alerts"
    del st.session_state["nav_to_alerts"]

page = st.sidebar.radio(
    "Navigation",
    nav_options,
    key="nav_radio",
    label_visibility="collapsed"
)


##Route to pages
if page == "Overview":
    from views import overview
    overview.render()

elif page == "Market Index":
    from views import market_index
    market_index.render()

elif page == "Material Analysis":
    from views import material_analysis
    material_analysis.render()

elif page == "TLC Calculator":
    from views import tlc_page
    tlc_page.render()

elif page == "Forecasting":
    from views import forecast_page
    forecast_page.render()

elif page == "Regression":
    from views import regression_page
    regression_page.render()

elif page == "Chatbot":
    from views import chatbot_page
    chatbot_page.render()

elif page == "Upload":
    from views import smart_upload
    smart_upload.render()

elif page == "Alerts":
    from views import alerts_page
    alerts_page.render()