import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")

# 🔗 Connected Live Google Sheet URL
GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1j96q7srUuKpBtI1QUVaSvNWfhmEmKb-0xuuslE5j944/edit?usp=sharing"

# --- 1. TOP-OF-PAGE CACHE REFRESH ENGINE ---
st.write("If you just updated numbers on your Google Sheet, click below to force an immediate cloud sync:")
if st.button("🔄 Refresh Data From Google Sheets", type="primary"):
    st.cache_data.clear()
    st.rerun()

st.divider()

try:
    if "/d/" in GOOGLE_SHEET_URL:
        gsheet_id = GOOGLE_SHEET_URL.split("/d/")[1].split("/")[0]
    else:
        gsheet_id = GOOGLE_SHEET_URL
except Exception:
    st.error("Invalid Google Sheet URL format. Make sure it looks like a standard browser link.")
    st.stop()

# Build direct multi-tab extraction streams
url_usage = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heaters+Sold_Intalled"
url_details = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heater+Details"

# Data extraction engine
@st.cache_data(ttl=60)
def load_live_data():
    try:
        usage_data = pd.read_csv(url_usage)
    except Exception:
        url_usage_alt = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heaters+SoldIntalled"
        usage_data = pd.read_csv(url_usage_alt)
        
    details_data = pd.read_csv(url_details)
    return usage_data, details_data

df, df_details = load_live_data()

# --- MODEL NUMBER CLEANING FUNCTION (Removes .0 decimals) ---
def clean_model_ids(series):
    return series.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)

# Clean Usage Data Columns
df.columns = df.columns.str.strip()
df['Model Number'] = clean_model_ids(df['Model Number'])
df = df[df['Model Number'] != 'nan']
df['Install Date'] = pd.to_datetime(df['Scheduled/ Completed Install Date'], errors='coerce')

# Price Extraction
def clean_price_col(col_name):
    if col_name in df.columns:
        return pd.to_numeric(df[col_name].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip(), errors='coerce').fillna(0)
    return pd.Series([0.0] * len(df))

df['Bulk Price'] = clean_price_col('BULK PRICE ONLINE (with tax)')
df['Store Price'] = clean_price_col('NXLVL STORE PRICE')

bulk_lookup = df[df['Bulk Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Bulk Price'].to_dict()
store_lookup = df[df['Store Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Store Price'].to_dict()

# Clean Current Inventory Data
df_details.columns
