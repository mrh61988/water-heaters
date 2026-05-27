import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")
st.write("Upload your master `Shop Stock.xlsx` file.")

uploaded_file = st.file_uploader("Upload Master Excel File (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    # --- 1. DATA EXTRACTION ---
    try:
        df = pd.read_excel(uploaded_file, sheet_name="Water Heaters Sold_Intalled")
    except ValueError:
        df = pd.read_excel(uploaded_file, sheet_name="Water Heaters SoldIntalled")
        
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

    # Current Inventory Extraction
    df_details = pd.read_excel(uploaded_file, sheet_name="Water Heater Details")
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
        st.write("The `ORDER QTY` column contains the recommended amount. Use the **+/- buttons** or click the cell to manually adjust.")

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
            
            # Recommended Order Amount
            order_amt = max(0, target_inv - effective_inv)
            
            order_sheet_data.append({
                "MODEL": model,
                "WAREHOUSE STOCK": current_inv,
                "PENDING INSTALLS": reserved,
                "ORDER QTY": order_amt, 
                "SOLD PAST 7 DAYS": sold_7d,
                "SOLD LAST 30 DAYS": sold_30d,
                "BULK PRICE ONLINE": bulk_price,
                "NXLVL STORE PRICE": store_price,
                "SAVINGS (PER UNIT)": savings
            })

        order_df = pd.DataFrame(order_sheet_data)

        # Style Function to highlight cells > 0
        def highlight_positive_orders(val):
            if isinstance(val, (int, float)) and val > 0:
                return 'background-color: #d4edda; font-weight: bold; color: #155724;' # Light green background
            return ''

        # Apply style only to the 'ORDER QTY' column
        styled_order_df = order_df.style.map(highlight_positive_orders, subset=["ORDER QTY"])

        # Create the Interactive Data Editor
        edited_df = st.data_editor(
            styled_order_df,
            column_config={
                # Step=1 adds the plus/minus buttons!
                "ORDER QTY": st.column_config.NumberColumn("ORDER QTY ✏️", min_value=0, step=1),
                "BULK PRICE ONLINE": st.column_config.NumberColumn(format="$%.2f"),
                "NXLVL STORE PRICE": st.column_config.NumberColumn(format="$%.2f"),
                "SAVINGS (PER UNIT)": st.column_config.NumberColumn(format="$%.2f"),
            },
            disabled=["MODEL", "WAREHOUSE STOCK", "PENDING INSTALLS", "SOLD PAST 7 DAYS", "SOLD LAST 30 DAYS", "BULK PRICE ONLINE", "NXLVL STORE PRICE", "SAVINGS (PER UNIT)"],
            hide_index=True,
            use_container_width=True
        )

        st.divider()

        # --- 4. FINANCIAL TOTALS (WITH TAX) ---
        TAX_RATE = 0.08  # 8% Tax

        total_units = edited_df["ORDER QTY"].sum()
        
        # Calculate Base Totals
        base_bulk_cost = (edited_df["ORDER QTY"] * edited_df["BULK PRICE ONLINE"]).sum()
        base_store_cost = (edited_df["ORDER QTY"] * edited_df["NXLVL STORE PRICE"]).sum()
        
        # Apply 8% Tax
        total_bulk_cost_with_tax = base_bulk_cost * (1 + TAX_RATE)
        total_store_cost_with_tax = base_store_cost * (1 + TAX_RATE)
        
        # Total Realized Savings
        total_savings = total_store_cost_with_tax - total_bulk_cost_with_tax

        st.subheader("Order Financial Summary (Includes 8% Tax)")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Heaters", int(total_units))
        col2.metric("Bulk Order Cost (+Tax)", f"${total_bulk_cost_with_tax:,.2f}")
        col3.metric("Store Price Cost (+Tax)", f"${total_store_cost_with_tax:,.2f}")
        
        # Highlight savings in green if > 0
        if total_savings > 0:
            col4.metric("Money Saved vs Store", f"${total_savings:,.2f}")
        else:
            col4.metric("Money Saved vs Store", "$0.00")
