import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")

# 🔗 CHANGE THIS TO YOUR ACTUAL GOOGLE SHEET URL:
GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/YOUR_REAL_SHEET_ID_HERE/edit#gid=0"

# Automatically extract the unique Sheet ID from your pasted link
if "YOUR_REAL_SHEET_ID_HERE" in GOOGLE_SHEET_URL:
    st.warning("⚠️ Please open `app.py` and replace the placeholder URL with your real Google Sheet link to see your live data!")
    st.stop()

try:
    if "/d/" in GOOGLE_SHEET_URL:
        gsheet_id = GOOGLE_SHEET_URL.split("/d/")[1].split("/")[0]
    else:
        gsheet_id = GOOGLE_SHEET_URL
except Exception:
    st.error("Invalid Google Sheet URL format. Make sure it looks like a standard browser link.")
    st.stop()

# Build the direct multi-tab extraction streams
url_usage = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heaters+Sold_Intalled"
url_details = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heater+Details"

# --- 1. DATA EXTRACTION WITH REFRESH FEATURE ---
@st.cache_data(ttl=60)  # Caches data for 60 seconds unless the refresh button is clicked
def load_live_data():
    try:
        usage_data = pd.read_csv(url_usage)
    except Exception:
        url_usage_alt = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heaters+SoldIntalled"
        usage_data = pd.read_csv(url_usage_alt)
        
    details_data = pd.read_csv(url_details)
    return usage_data, details_data

df, df_details = load_live_data()

# Clean Usage Data Columns
df.columns = df.columns.str.strip()
df['Model Number'] = df['Model Number'].astype(str).str.strip()
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
df_details.columns = df_details.columns.str.strip()
df_details['Model'] = df_details['Model'].astype(str).str.strip()
inventory_lookup = df_details.set_index('Model')['Counted Inventory'].fillna(0).to_dict()

# --- 2. FORECAST CALCULATIONS ---
df_installed = df[df['WH Status'] == 'Installed']
df_estimates = df[df['WH Status'] == 'Estimate Accepted']

reserved_stock = df_estimates.groupby('Model Number')['Quantity'].sum().reset_index().rename(columns={'Quantity': 'Reserved'})

max_date = df_installed['Install Date'].max()
min_date = df_installed['Install Date'].min()
date_30_days_ago = max_date - pd.Timedelta(days=30)
date_7_days_ago = max_date - pd.Timedelta(days=7)
total_weeks = (max_date - min_date).days / 7 if (max_date - min_date).days > 0 else 1

# Sidebar Settings
st.sidebar.header("Warehouse & Order Settings")
target_total_inventory = st.sidebar.slider("Target Total Warehouse Capacity", min_value=10, max_value=50, value=25)

st.sidebar.subheader("Usage Weighting (%)")
weight_7d = st.sidebar.slider("Last 7 Days Weight", 0, 100, 50)
weight_30d = st.sidebar.slider("Last 30 Days Weight", 0, 100, 30)
weight_all = st.sidebar.slider("All-Time Weight", 0, 100, 20)

if (weight_7d + weight_30d + weight_all) != 100:
    st.sidebar.error("Weights must add up to 100%.")

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

# Weights & Capacity Target
w_7d, w_30d, w_all = weight_7d / 100.0, weight_30d / 100.0, weight_all / 100.0
master_df['Weighted Weekly Avg'] = (master_df['7D Weekly Avg'] * w_7d) + (master_df['30D Weekly Avg'] * w_30d) + (master_df['All Time Weekly Avg'] * w_all)

total_weighted_avg = master_df['Weighted Weekly Avg'].sum()
master_df['Share %'] = master_df['Weighted Weekly Avg'] / total_weighted_avg
master_df['Target Capacity'] = (master_df['Share %'] * target_total_inventory).round().astype(int)

master_df = pd.merge(master_df, reserved_stock, on='Model Number', how='left').fillna(0)

# --- 3. UI TABS ---
tab1, tab2 = st.tabs(["📋 Interactive Order Sheet", "📊 Forecasting Breakdown"])

with tab2:
    st.subheader("Data & Forecast Breakdown")
    st.dataframe(master_df[['Model Number', 'Weighted Weekly Avg', 'Target Capacity', 'Reserved']])

with tab1:
    st.subheader("Weekly Bulk Order Sheet")
    st.write("**Click directly on any number in the `ORDER QTY` column** to reveal the **+/- buttons** and manually adjust your order.")

    order_sheet_data = []
    
    for index, row in master_df.iterrows():
        model = row['Model Number']
        target_inv = row['Target Capacity']
        reserved = int(row['Reserved'])
        sold_7d = int(row['Sold 7D'])
        sold_30d = int(row['Sold 30D'])
        
        bulk_price = bulk_lookup.get(model, 0.0)
        store_price = store_lookup.get(model, 0.0)
        savings = max(0, store_price - bulk_price)
        
        current_inv = int(inventory_lookup.get(model, 0))
        effective_inv = current_inv - reserved
        
        order_amt = max(0, target_inv - effective_inv)
        status = "🟢 ORDER" if order_amt > 0 else "✔️ OK"
        
        order_sheet_data.append({
            "STATUS": status,
            "MODEL": model,
            "WAREHOUSE STOCK": current_inv,
            "PENDING INSTALLS": reserved,
            "ORDER QTY": order_amt, 
            "INSTALLED/SOLD IN PAST 7 DAYS": sold_7d,
            "SOLD IN LAST 30 DAYS": sold_30d,
            "BULK PRICE ONLINE": bulk_price,
            "NXLVL STORE PRICE": store_price,
            "SAVINGS": savings
        })

    order_df = pd.DataFrame(order_sheet_data)

    # Highlighting the STATUS column since coloring the editable column locks the manual entry features.
    def highlight_status(val):
        if val == "🟢 ORDER":
            return 'background-color: #d4edda; font-weight: bold; color: #155724;' 
        return ''

    styled_order_df = order_df.style.map(highlight_status, subset=["STATUS"])

    # Interactive Data Editor with step=1 for plus/minus configuration
    edited_df = st.data_editor(
        styled_order_df,
        column_config={
            "ORDER QTY": st.column_config.NumberColumn("ORDER QTY ✏️", min_value=0, step=1),
            "BULK PRICE ONLINE": st.column_config.NumberColumn(format="$%.2f"),
            "NXLVL STORE PRICE": st.column_config.NumberColumn(format="$%.2f"),
            "SAVINGS": st.column_config.NumberColumn(format="$%.2f"),
        },
        disabled=["STATUS", "MODEL", "WAREHOUSE STOCK", "PENDING INSTALLS", "INSTALLED/SOLD IN PAST 7 DAYS", "SOLD IN LAST 30 DAYS", "BULK PRICE ONLINE", "NXLVL STORE PRICE", "SAVINGS"],
        hide_index=True,
        use_container_width=True
    )

    st.divider()

    # --- 4. FINANCIAL TOTALS (WITH 8% TAX ADDED) ---
    TAX_RATE = 0.08

    total_units = edited_df["ORDER QTY"].sum()
    
    # Calculate Base Costs
    base_bulk_cost = (edited_df["ORDER QTY"] * edited_df["BULK PRICE ONLINE"]).sum()
    base_store_cost = (edited_df["ORDER QTY"] * edited_df["NXLVL STORE PRICE"]).sum()
    
    # Apply 8% Tax Multipliers
    total_bulk_cost_with_tax = base_bulk_cost * (1 + TAX_RATE)
    total_store_cost_with_tax = base_store_cost * (1 + TAX_RATE)
    total_savings = total_store_cost_with_tax - total_bulk_cost_with_tax

    st.subheader("Order Financial Summary (Includes 8% Tax)")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Heaters Ordered", int(total_units))
    col2.metric("Bulk Order Total (+8% Tax)", f"${total_bulk_cost_with_tax:,.2f}")
    col3.metric("Store Price Total (+8% Tax)", f"${total_store_cost_with_tax:,.2f}")
    
    if total_savings > 0:
        col4.metric("Net Financial Savings", f"${total_savings:,.2f}")
    else:
        col4.metric("Net Financial Savings", "$0.00")

    st.divider()
    
    # --- 5. THE CACHE REFRESH BUTTON ENGINE ---
    # Placing it at the bottom makes it cleanly accessible after verifying the order data
    st.write("If you just updated numbers on your Google Sheet, click below to force an immediate sync:")
    if st.button("🔄 Refresh Data From Google Sheets", type="primary"):
        st.cache_data.clear() # Wipes out the app's internal snapshot memory
        st.rerun()            # Forces the entire script to execute fresh from the cloud URL
