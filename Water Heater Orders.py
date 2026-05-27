import streamlit as st
import pandas as pd
import datetime

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")
@@ -53,22 +52,25 @@ def clean_model_ids(series):
df = df[df['Model Number'] != 'nan']
df['Install Date'] = pd.to_datetime(df['Scheduled/ Completed Install Date'], errors='coerce')

# Price Extraction
def clean_price_col(col_name):
    if col_name in df.columns:
        return pd.to_numeric(df[col_name].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip(), errors='coerce').fillna(0)
    return pd.Series([0.0] * len(df))
# Clean Current Inventory Data from Details Sheet
df_details.columns = df_details.columns.str.strip()
df_details['Model'] = clean_model_ids(df_details['Model'])

df['Bulk Price'] = clean_price_col('BULK PRICE ONLINE (with tax)')
df['Store Price'] = clean_price_col('NXLVL STORE PRICE')
# Price Extraction Helper Function
def clean_price_col(dataframe, col_name):
    if col_name in dataframe.columns:
        return pd.to_numeric(dataframe[col_name].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip(), errors='coerce').fillna(0)
    return pd.Series([0.0] * len(dataframe))

bulk_lookup = df[df['Bulk Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Bulk Price'].to_dict()
store_lookup = df[df['Store Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Store Price'].to_dict()
# Extract pricing metrics from the Water Heater Details dataframe
df_details['Bulk Price'] = clean_price_col(df_details, 'BULK PRICE ONLINE')
df_details['Store Price'] = clean_price_col(df_details, 'NXLVL STORE PRICE')

# Clean Current Inventory Data
df_details.columns = df_details.columns.str.strip()
df_details['Model'] = clean_model_ids(df_details['Model'])
inventory_lookup = df_details.set_index('Model')['Counted Inventory'].fillna(0).to_dict()
# Drop duplicate models in Details tab to build clean lookup tables
df_details_unique = df_details.drop_duplicates('Model', keep='first')
inventory_lookup = df_details_unique.set_index('Model')['Counted Inventory'].fillna(0).to_dict()
bulk_lookup = df_details_unique.set_index('Model')['Bulk Price'].fillna(0).to_dict()
store_lookup = df_details_unique.set_index('Model')['Store Price'].fillna(0).to_dict()

# --- 2. FORECAST CALCULATIONS ---
df_installed = df[df['WH Status'] == 'Installed']
@@ -85,7 +87,6 @@ def clean_price_col(col_name):
# --- SIDEBAR SETTINGS ---
st.sidebar.header("Warehouse & Order Settings")

# Targeting mode choice
target_mode = st.sidebar.selectbox("Suggested Quantity Targeting Mode", ["💰 Budget Goal ($)", "📦 Warehouse Capacity (Units)"])

if target_mode == "💰 Budget Goal ($)":
