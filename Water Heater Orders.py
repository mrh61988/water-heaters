import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Water Heater Bulk Ordering Dashboard")

# 1. Upload Usage Data
uploaded_file = st.file_uploader("Upload Water Heaters Sold/Installed CSV", type=["csv"])

if uploaded_file is not None:
    # Load and clean data
    df = pd.read_csv(uploaded_file)
    
    # Strip whitespace from column names so they are easy to reference
    df.columns = df.columns.str.strip()
    
    df['Model Number'] = df['Model Number'].astype(str).str.strip()
    df = df[df['Model Number'] != 'nan']
    df['Install Date'] = pd.to_datetime(df['Scheduled/ Completed Install Date'], errors='coerce')
    
    # Clean the price column (Remove $ and spaces, convert to float)
    df['Bulk Price'] = df['BULK PRICE ONLINE (with tax)'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip()
    df['Bulk Price'] = pd.to_numeric(df['Bulk Price'], errors='coerce').fillna(0)
    
    # Create a lookup dictionary for the latest price of each model
    price_lookup = df[df['Bulk Price'] > 0].drop_duplicates('Model Number', keep='first').set_index('Model Number')['Bulk Price'].to_dict()

    # Split data into Completed vs Upcoming
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

    # 3. Calculate Usages (Using only INSTALLED data)
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

    # Merge
    master_df = all_time_usage[['Model Number', 'Quantity', 'All Time Weekly Avg']]
    master_df = pd.merge(master_df, usage_30d[['Model Number', '30D Weekly Avg']], on='Model Number', how='left').fillna(0)
    master_df = pd.merge(master_df, usage_7d[['Model Number', '7D Weekly Avg']], on='Model Number', how='left').fillna(0)
    
    # Get Top 8
    master_df = master_df.sort_values(by='Quantity', ascending=False).head(8).reset_index(drop=True)

    # Apply Weights & Calculate Share
    w_7d, w_30d, w_all = weight_7d / 100.0, weight_30d / 100.0, weight_all / 100.0
    master_df['Weighted Weekly Avg'] = (master_df['7D Weekly Avg'] * w_7d) + (master_df['30D Weekly Avg'] * w_30d) + (master_df['All Time Weekly Avg'] * w_all)
    
    total_weighted_avg = master_df['Weighted Weekly Avg'].sum()
    master_df['Share %'] = master_df['Weighted Weekly Avg'] / total_weighted_avg
    master_df['Target Capacity'] = (master_df['Share %'] * target_total_inventory).round().astype(int)

    # Merge in the Reserved Estimates
    master_df = pd.merge(master_df, reserved_stock, on='Model Number', how='left').fillna(0)

    st.divider()

    # 4. Input Current Inventory & Calculate Order
    st.subheader("Inventory Check & Order Formulation")
    st.write("Enter the physical count of units currently sitting in the shop. The tool will automatically reserve units needed for accepted estimates.")
    
    inventory_data = []
    total_ordering = 0
    total_cost = 0.0
    
    cols = st.columns(4)
    for index, row in master_df.iterrows():
        model = row['Model Number']
        target_inv = row['Target Capacity']
        reserved = int(row['Reserved'])
        unit_price = price_lookup.get(model, 0.0)
        
        with cols[index % 4]:
            current_inv = st.number_input(f"Current Inv: Model {model}", min_value=0, value=0, key=model)
            
            if reserved > 0:
                st.caption(f"⚠️ {reserved} reserved for upcoming jobs")
                
            # Effective inventory is what you actually have available to use
            effective_inv = current_inv - reserved
            
            # Order amount fills the gap up to the target capacity
            order_amt = max(0, target_inv - effective_inv)
            
            line_cost = order_amt * unit_price
            
            total_ordering += order_amt
            total_cost += line_cost
            
            inventory_data.append({
                "Model Number": model,
                "Unit Bulk Price": f"${unit_price:,.2f}",
                "Physical Inventory": current_inv,
                "Reserved for Jobs": reserved,
                "Effective Inventory": effective_inv,
                "Target Capacity": target_inv,
                "ORDER QUANTITY": order_amt,
                "Line Total Cost": f"${line_cost:,.2f}"
            })
            
    # 5. Output Final Recommendation
    st.divider()
    st.subheader("Bulk Order Purchase Sheet")
    results_df = pd.DataFrame(inventory_data)
    
    # Highlight the final order column
    st.dataframe(results_df.style.apply(lambda x: ['background: lightgreen; font-weight: bold' if x.name == 'ORDER QUANTITY' else '' for i in x], axis=0), use_container_width=True)
    
    # Cost Summary Metrics
    col1, col2 = st.columns(2)
    col1.metric("Total Heaters to Order", total_ordering)
    col2.metric("Estimated Total Order Cost", f"${total_cost:,.2f}")