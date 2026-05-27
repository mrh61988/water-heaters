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
        sold_7d = int(row['Sold 7D'])
        sold_30d = int(row['Sold 30D'])
        current_inv = int(row['In Shop'])
        
        bulk_price = bulk_lookup.get(model, 0.0)
        store_price = store_lookup.get(model, 0.0)
        savings = max(0, store_price - bulk_price)
        
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
    order_df = order_df.sort_values(by="SOLD IN LAST 30 DAYS", ascending=False)

    reordered_columns = [
        "STATUS",
        "MODEL",
        "WAREHOUSE STOCK",
        "PENDING INSTALLS",
        "ORDER QTY",  
        "INSTALLED/SOLD IN PAST 7 DAYS",
        "SOLD IN LAST 30 DAYS",
        "BULK PRICE ONLINE",
        "NXLVL STORE PRICE",
        "SAVINGS"
    ]
    order_df = order_df[reordered_columns]

    def highlight_ordered_models(df_input):
        style_df = pd.DataFrame('', index=df_input.index, columns=df_input.columns)
        mask = df_input['ORDER QTY'] > 0
        style_df.loc[mask, 'MODEL'] = 'background-color: #d4edda; font-weight: bold; color: #155724;'
        return style_df

    styled_order_df = order_df.style.apply(highlight_ordered_models, axis=None)

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

    # --- 4. EXPANDED FINANCIAL TOTALS WITH ITEMIZED 8% TAX ---
    total_units = edited_df["ORDER QTY"].sum()
    
    base_bulk_cost = (edited_df["ORDER QTY"] * edited_df["BULK PRICE ONLINE"]).sum()
    bulk_tax = base_bulk_cost * TAX_RATE
    total_bulk_cost_with_tax = base_bulk_cost + bulk_tax
    
    base_store_cost = (edited_df["ORDER QTY"] * edited_df["NXLVL STORE PRICE"]).sum()
    store_tax = base_store_cost * TAX_RATE
    total_store_cost_with_tax = base_store_cost + store_tax
    
    net_savings = total_store_cost_with_tax - total_bulk_cost_with_tax

    st.subheader("Order Financial Summary")
    
    col_bulk, col_store, col_summary = st.columns(3)
    
    with col_bulk:
        st.markdown("### 🏪 Bulk Ordering Price")
        st.write(f"**Subtotal:** ${base_bulk_cost:,.2f}")
        st.write(f"**Estimated Tax (8.0%):** ${bulk_tax:,.2f}")
        st.markdown(f"**TOTAL BULK COST:** `${total_bulk_cost_with_tax:,.2f}`")
        
    with col_store:
        st.markdown("### 🏢 Regular Store Price")
        st.write(f"**Subtotal:** ${base_store_cost:,.2f}")
        st.write(f"**Estimated Tax (8.0%):** ${store_tax:,.2f}")
        st.markdown(f"**TOTAL STORE COST:** `${total_store_cost_with_tax:,.2f}`")
        
    with col_summary:
        st.markdown("### 📈 Order Volume Metrics")
        st.metric("Total Heaters Selected", int(total_units))
        st.metric("Net Financial Savings", f"${max(0.0, net_savings):,.2f}")

    st.divider()

    # --- 📋 RICH TEXT EMAIL DRAFT GENERATION ENGINE ---
    st.subheader("✉️ Copy & Paste Rich Text Email Draft")
    st.write("💡 **How to copy:** Use your mouse cursor to highlight the text block and clean table below together, copy, and paste straight into your email composer window.")
    
    quick_copy_base = edited_df[edited_df["ORDER QTY"] > 0].copy()
    
    if not quick_copy_base.empty:
        table_markdown_rows = ""
        for _, r in quick_copy_base.iterrows():
            table_markdown_rows += f"| {r['MODEL']} | {int(r['ORDER QTY'])} | ${r['BULK PRICE ONLINE']:,.2f} | ${r['NXLVL STORE PRICE']:,.2f} |\n"

        # --- 🔄 ADJUSTED LOGIC: Added explicit triple newline line space between greeting paragraphs ---
        email_rich_template = f"""
Please see the water heater order below. Let me know how soon these can be delivered and if you have any questions. Thanks!


Please send payment request to my cell. 804-536-4748

Thank you

| MODEL | ORDER QTY | BULK PRICE | STORE PRICE |
| :--- | :--- | :--- | :--- |
{table_markdown_rows}

**Total Quantity Ordered:** {int(total_units)} unit(s)

**Subtotal:** ${base_bulk_cost:,.2f}

**Estimated Tax (8.0%):** ${bulk_tax:,.2f}

**TOTAL BULK COST:** ${total_bulk_cost_with_tax:,.2f}
        """
        
        st.markdown(
            f'<div style="background-color: #fcfcfc; padding: 25px; border-radius: 8px; border: 1px solid #eaeaea; line-height: 1.6;">'
            f'{email_rich_template}'
            f'</div>', 
            unsafe_allow_html=True
        )
    else:
        st.info("No items currently marked for order matching the current configuration.")
