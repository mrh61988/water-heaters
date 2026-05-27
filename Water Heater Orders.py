import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")
st.write("Upload your master `Shop Stock.xlsx` file. The tool will automatically pull your usage history, pricing, and current inventory counts.")

# 1. Upload Master Excel File
# Note: Ensure you have 'openpyxl' installed in your python environment (pip install openpyxl)
uploaded_file = st.file_uploader("Upload Master Excel File (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    # Read the Usage / Installed sheet
    try:
        df = pd.read_excel(uploaded_file, sheet_name="Water Heaters Sold_Intalled")
    except ValueError:
        # Fallback if the sheet name is slightly different in the actual file
        df = pd.read_excel(uploaded_file, sheet_name="Water Heaters SoldIntalled")
        
    df.columns = df.columns.str.strip()
    df['Model Number'] = df['Model Number'].astype(str).str.strip()
    df = df[df['Model Number'] != 'nan']
    df['Install Date'] = pd.to_datetime(df['Scheduled/ Completed Install Date'], errors='coerce')
    
    # Read Pricing from the usage sheet
    if 'BULK PRICE ONLINE (with tax)' in df.columns:
        df['Bulk Price'] = df['BULK PRICE ONLINE (with tax)'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip()
    else:
        # Fallback if column name changes
        df['Bulk Price'] = 0.0
        
    df['Bulk Price'] = pd.to_numeric(df['Bulk Price'], errors='coerce').fillna(0)
    price_lookup = df[df['Bulk Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Bulk Price'].to_dict()

    # Read the Current Inventory sheet
    df_details = pd.read_excel(uploaded_file, sheet_name="Water Heater Details")
    df_details['Model'] = df_details['Model'].astype(str).str.strip()
    
    # Create an automated dictionary for current inventory
    inventory_lookup = df_details.set_index('Model')['Counted Inventory'].fillna(0).to_dict()

    # Split data into Completed vs Upcoming Estimates
    df_installed = df[df['WH Status'] == 'Installed']
    df_estimates = df[df['WH Status'] == 'Estimate Accepted']

    # Calculate Reserved Stock (Units promised to jobs but not yet installed)
    reserved_stock = df_estimates.groupby('Model Number')['Quantity'].sum().reset_index()
    reserved_stock = reserved_stock.rename(columns={'Quantity': 'Reserved'})

    # Establish Timeline based on INSTALLED dates
    max_date = df_installed['Install Date'].max()
    min_date = df_installed['Install Date'].min()
    date_30_days_ago = max_date - pd.Timedelta(days=30)
    date_7_days_ago = max_date - pd.Timedelta(days=7)
    total_weeks = (max_date - min_date).days / 7

    # 2. Settings (Sidebar)
    st.sidebar.header("Warehouse & Order Settings")
    target_total_inventory = st.sidebar.slider("Target Total Warehouse Capacity", min_value=10, max_value=50, value=25)
    
    st.sidebar.subheader("Usage Weighting (%)")
    weight_7d = st.sidebar.slider("Last 7 Days Weight", 0, 100, 50)
    weight_30d = st.sidebar.slider("Last 30 Days Weight", 0, 100, 30)
    weight_all = st.sidebar.slider("All-Time Weight", 0, 100, 20)
    
    if (weight_7d + weight_30d + weight_all) != 100:
        st.sidebar.error("Weights must add up to 100%.")

    # 3. Calculate Usages
    # All Time
    all_time_usage = df_installed.groupby('Model Number')['Quantity'].sum().reset_index()
    all_time_usage['All Time Weekly Avg'] = all_time_usage['Quantity'] / total_weeks if total_weeks > 0 else 0
    
    # Last 30 Days
    df_30d = df_installed[df_installed['Install Date'] >= date_30_days_ago]
    usage_30d = df_30d.groupby('Model Number')['Quantity'].sum().reset_index()
    usage_30d['30D Weekly Avg'] = usage_30d['Quantity'] / (30/7)
    
    # Last 7 Days
    df_7d = df_installed[df_installed['Install Date'] >= date_7_days_ago]
    usage_7d = df_7d.groupby('Model Number')['Quantity'].sum().reset_index()
    usage_7d['7D Weekly Avg'] = usage_7d['Quantity'] / 1  

    # Merge averages
    master_df = all_time_usage[['Model Number', 'Quantity', 'All Time Weekly Avg']]
    master_df = pd.merge(master_df, usage_30d[['Model Number', '30D Weekly Avg']], on='Model Number', how='left').fillna(0)
    master_df = pd.merge(master_df, usage_7d[['Model Number', '7D Weekly Avg']], on='Model Number', how='left').fillna(0)
    
    # Get Top 8 Models
    master_df = master_df.sort_values(by='Quantity', ascending=False).head(8).reset_index(drop=True)

    # Apply Weights & Calculate Capacity Target
    w_7d, w_30d, w_all = weight_7d / 100.0, weight_30d / 100.0, weight_all / 100.0
    master_df['Weighted Weekly Avg'] = (master_df['7D Weekly Avg'] * w_7d) + (master_df['30D Weekly Avg'] * w_30d) + (master_df['All Time Weekly Avg'] * w_all)
    
    total_weighted_avg = master_df['Weighted Weekly Avg'].sum()
    master_df['Share %'] = master_df['Weighted Weekly Avg'] / total_weighted_avg
    master_df['Target Capacity'] = (master_df['Share %'] * target_total_inventory).round().astype(int)

    # Merge in the Reserved Estimates
    master_df = pd.merge(master_df, reserved_stock, on='Model Number', how='left').fillna(0)

    # 4. Generate Order Calculations 
    st.subheader("Bulk Order Purchase Sheet")
    
    inventory_data = []
    total_ordering = 0
    total_cost = 0.0
    
    for index, row in master_df.iterrows():
        model = row['Model Number']
        target_inv = row['Target Capacity']
        reserved = int(row['Reserved'])
        unit_price = price_lookup.get(model, 0.0)
        
        # Automatically lookup inventory instead of asking user
        current_inv = int(inventory_lookup.get(model, 0))
        effective_inv = current_inv - reserved
        
        order_amt = max(0, target_inv - effective_inv)
        line_cost = order_amt * unit_price
        
        total_ordering += order_amt
        total_cost += line_cost
        
        inventory_data.append({
            "Model Number": model,
            "Target Stock": target_inv,
            "In Shop": current_inv,
            "Reserved": reserved,
            "Effective Stock": effective_inv,
            "RECOMMENDED ORDER": order_amt,
            "Unit Bulk Price": f"${unit_price:,.2f}" if unit_price > 0 else "Unknown",
            "Line Total Cost": f"${line_cost:,.2f}"
        })
            
    # Output Recommendation
    results_df = pd.DataFrame(inventory_data)
    
    # Highlight the final order column
    st.dataframe(results_df.style.apply(lambda x: ['background: lightgreen; font-weight: bold' if x.name == 'RECOMMENDED ORDER' else '' for i in x], axis=0), use_container_width=True)
    
    st.divider()
    
    # Cost Summary Metrics
    col1, col2 = st.columns(2)
    col1.metric("Total Heaters to Order", total_ordering)
    col2.metric("Estimated Total Order Cost", f"${total_cost:,.2f}")
