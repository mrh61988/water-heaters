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

# Clean Current Inventory Data from Details Sheet
df_details.columns = df_details.columns.str.strip()
df_details['Model'] = clean_model_ids(df_details['Model'])

# Price Extraction Helper Function
def clean_price_col(dataframe, col_name):
    if col_name in dataframe.columns:
        return pd.to_numeric(dataframe[col_name].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip(), errors='coerce').fillna(0)
    return pd.Series([0.0] * len(dataframe))

# Extract pricing metrics from the Water Heater Details dataframe
df_details['Bulk Price'] = clean_price_col(df_details, 'BULK PRICE ONLINE')
df_details['Store Price'] = clean_price_col(df_details, 'NXLVL STORE PRICE')

# Drop duplicate models in Details tab to build clean lookup tables
df_details_unique = df_details.drop_duplicates('Model', keep='first')
inventory_lookup = df_details_unique.set_index('Model')['Counted Inventory'].fillna(0).to_dict()
bulk_lookup = df_details_unique.set_index('Model')['Bulk Price'].fillna(0).to_dict()
store_lookup = df_details_unique.set_index('Model')['Store Price'].fillna(0).to_dict()

# --- 2. FORECAST CALCULATIONS ---
df_installed = df[df['WH Status'] == 'Installed']
df_estimates = df[df['WH Status'] == 'Estimate Accepted']

reserved_stock = df_estimates.groupby('Model Number')['Quantity'].sum().reset_index().rename(columns={'Quantity': 'Reserved'})

max_date = df_installed['Install Date'].max()
min_date = df_installed['Install Date'].min()
date_30_days_ago = max_date - pd.Timedelta(days=30)
date_7_days_ago = max_date - pd.Timedelta(days=7)
total_weeks = (max_date - min_date).days / 7 if (max_date - min_date).days > 0 else 1

# --- SIDEBAR SETTINGS ---
st.sidebar.header("Warehouse & Order Settings")

target_mode = st.sidebar.selectbox("Suggested Quantity Targeting Mode", ["💰 Budget Goal ($)", "📦 Warehouse Capacity (Units)"])

if target_mode == "💰 Budget Goal ($)":
    price_goal = st.sidebar.number_input("Total Order Price Goal (with tax)", min_value=500, max_value=50000, value=6500, step=500)
else:
    target_total_inventory = st.sidebar.slider("Target Total Warehouse Capacity", min_value=10, max_value=100, value=25)

st.sidebar.subheader("Usage Weighting (%)")
weight_7d = st.sidebar.slider("Last 7 Days Weight", 0, 100, 60) 
weight_30d = st.sidebar.slider("Last 30 Days Weight", 0, 100, 30) 
weight_all = st.sidebar.slider("All-Time Weight", 0, 100, 10)    

if (weight_7d + weight_30d + weight_all) != 100:
    st.sidebar.error("Weights must add up to 100%. Adjust to activate calculations.")
    st.stop()

# Timeframe Aggregations
all_time = df_installed.groupby('Model Number')['Quantity'].sum().reset_index()
all_time['All Time Weekly Avg'] = all_time['Quantity'] / total_weeks

usage_30d = df_installed[df_installed['Install Date'] >= date_30_days_ago].groupby('Model Number')['Quantity'].sum().reset_index()
usage_30d['30D Weekly Avg'] = usage_30d['Quantity'] / (30/7)

usage_7d = df_installed[df_installed['Install Date'] >= date_7_days_ago].groupby('Model Number')['Quantity'].sum().reset_index()
usage_7d['7D Weekly Avg'] = usage_7d['Quantity'] / 1  

# Merge into Master
master_df = all_time[['Model Number', 'Quantity', 'All Time Weekly Avg']]
master_df = pd.merge(master_df, usage_30d, on='Model Number', how='left').rename(columns={'Quantity_y': 'Sold 30D'}).fillna(0)
master_df = pd.merge(master_df, usage_7d, on='Model Number', how='left').rename(columns={'Quantity': 'Sold 7D'}).fillna(0)
master_df = master_df.sort_values(by='Quantity_x', ascending=False).head(8).reset_index(drop=True)

# Weights & Allocation Calculations
w_7d, w_30d, w_all = weight_7d / 100.0, weight_30d / 100.0, weight_all / 100.0
master_df['Weighted Weekly Avg'] = (master_df['7D Weekly Avg'] * w_7d) + (master_df['30D Weekly Avg'] * w_30d) + (master_df['All Time Weekly Avg'] * w_all)

total_weighted_avg = master_df['Weighted Weekly Avg'].sum() if master_df['Weighted Weekly Avg'].sum() > 0 else 1
master_df['Share %'] = master_df['Weighted Weekly Avg'] / total_weighted_avg

master_df = pd.merge(master_df, reserved_stock, on='Model Number', how='left').fillna(0)
master_df['In Shop'] = master_df['Model Number'].map(inventory_lookup).fillna(0).astype(int)
master_df['Bulk Price'] = master_df['Model Number'].map(bulk_lookup).fillna(0)

TAX_RATE = 0.08

# --- AUTOMATED BUDGET SOLVER LOOP ---
if target_mode == "💰 Budget Goal ($)":
    best_capacity = 0
    closest_diff = float('inf')
    
    for test_capacity in range(0, 500):
        test_targets = (master_df['Share %'] * test_capacity).round().astype(int)
        test_effective = master_df['In Shop'] - master_df['Reserved']
        test_orders = (test_targets - test_effective).clip(lower=0)
        test_cost = (test_orders * master_df['Bulk Price']).sum() * (1 + TAX_RATE)
        
        diff = abs(test_cost - price_goal)
        if diff < closest_diff:
            closest_diff = diff
            best_capacity = test_capacity
            
    master_df['Target Capacity'] = (master_df['Share %'] * best_capacity).round().astype(int)
else:
    master_df['Target Capacity'] = (master_df['Share %'] * target_total_inventory).round().astype(int)


# --- 3. UI TAB PANELS ---
tab1, tab2 = st.tabs(["📋 Interactive Order Sheet", "📊 Forecasting Breakdown"])

with tab2:
    st.subheader("Data & Forecast Breakdown")
    st.dataframe(master_df[['Model Number', 'Weighted Weekly Avg', 'Share %', 'Target Capacity', 'In Shop', 'Reserved']])

with tab1:
    st.subheader("Master Weekly Bulk Order Sheet")
    st.write("**Click directly on any number in the `ORDER QTY` column** to reveal the **+/- buttons** or type to manually adjust.")

    order_sheet_data = []
    
    for index, row in master_df.iterrows():
        model = row['Model Number']
        target_inv = row['Target Capacity']
        reserved = int(row['Reserved'])
        sold_7d
