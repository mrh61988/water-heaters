import streamlit as st
import pandas as pd
import datetime

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

# 🔄 UPDATED: Swapped out sliders for clean, scannable numeric text input boxes
st.sidebar.subheader("Usage Weighting (%)")
weight_7d = st.sidebar.number_input("Last 7 Days Weight", min_value=0, max_value=100, value=60, step=1) 
weight_30d = st.sidebar.number_input("Last 30 Days Weight", min_value=0, max_value=100, value=30, step=1) 
weight_all = st.sidebar.number_input("All-Time Weight", min_value=0, max_value=100, value=10, step=1)    

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
tab1, tab2, tab3 = st.tabs(["📋 Interactive Order Sheet", "📊 Forecasting Breakdown", "🧪 Feature Sandbox"])

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


# --- 🧪 TEST TAB FEATURE SANDBOX PANEL ---
with tab3:
    st.header("🧪 Advanced Logistics Feature Sandbox")
    st.write("Interact with prototypes of advanced logistical calculators using your live data metrics.")
    
    # Global Blackout Date Rule Engine (No Sundays, No Thanksgiving, No Christmas Day)
    def is_operational_day(check_date):
        if check_date.weekday() == 6:  # Sunday
            return False
        if check_date.month == 12 and check_date.day == 25:  # Christmas
            return False
        if check_date.month == 11 and check_date.weekday() == 3 and (22 <= check_date.day <= 28):  # Thanksgiving (4th Thursday)
            return False
        return True

    # ----------------------------------------------------
    # SANDBOX FEATURE 1: RUNOUT TRACKER
    # ----------------------------------------------------
    st.subheader("1. Runout Tracker (Days of Stock Left)")
    st.write("Calculates how many days until you run out using your current stock minus pending jobs, divided by sales velocity.")
    
    velocity_slider = st.slider("Simulated Sales Velocity Multiplier", min_value=0.5, max_value=3.0, value=1.0, step=0.1, help="Simulates spikes or dips in active installation trends.")
    
    runout_data = []
    for index, row in master_df.iterrows():
        model = row['Model Number']
        shop_stock = int(row['In Shop'])
        reserved = int(row['Reserved'])
        sold_7d = int(row['Sold 7D'])
        sold_30d = int(row['Sold 30D'])
        
        daily_velocity = (row['Weighted Weekly Avg'] / 7.0) * velocity_slider
        net_stock = shop_stock - reserved
        
        if daily_velocity <= 0:
            days_left = 999  
        else:
            days_left = max(0, int(net_stock / daily_velocity))
            
        if net_stock <= 0:
            days_left = 0
            
        if days_left <= 3:
            alert = "🔴 CRITICAL STOCK"
        elif days_left <= 8:
            alert = "Warning"
        else:
            alert = "🟢 HEALTHY"
            
        runout_data.append({
            "MODEL": model,
            "CURRENT PHYSICAL STOCK": shop_stock,
            "PENDING INSTALLS": reserved,
            "NET AVAILABLE STOCK": net_stock,
            "SOLD IN PAST 7 DAYS": sold_7d,
            "SOLD IN PAST 30 DAYS": sold_30d,
            "DAILY VELOCITY": daily_velocity,
            "EST. DAYS LEFT": "STOCKED OUT" if net_stock <= 0 else (f"{days_left} Days" if days_left < 365 else "Stable Stock"),
            "STATUS RUNOUT ALERT": alert,
            "_raw_sort_key": days_left
        })
    
    runout_df = pd.DataFrame(runout_data)
    runout_df = runout_df.sort_values(by="_raw_sort_key", ascending=True).drop(columns=["_raw_sort_key"])
    
    def style_runout(val):
        if "🔴" in str(val): return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
        if "Warning" in str(val): return 'background-color: #fff3cd; color: #856404;'
        if "🟢" in str(val): return 'background-color: #d4edda; color: #155724;'
        return ''
        
    st.dataframe(runout_df.style.map(style_runout, subset=["STATUS RUNOUT ALERT"]), hide_index=True, use_container_width=True)
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 2: VISUAL ANALYTICS AND CHARTS
    # ----------------------------------------------------
    st.subheader("2. Sales Visualizer (What's Hot)")
    st.write("An instant text grid showing your sales volume, sorted from highest to lowest volume automatically.")
    
    sales_table_df = master_df[['Model Number', 'Sold 30D']].copy()
    sales_table_df = sales_table_df.sort_values(by='Sold 30D', ascending=False)
    sales_table_df.columns = ["MODEL NUMBER", "UNITS SOLD (PAST 30 DAYS)"]
    
    st.dataframe(sales_table_df, hide_index=True, use_container_width=True)
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 3: DEAD STOCK DETECTOR
    # ----------------------------------------------------
    st.subheader("3. Dead Stock Finder (Dust Collectors)")
    st.write("Flags models that have items sitting on warehouse shelves but show absolute zero installation activity during selected time horizons.")
    
    dead_stock_days = st.selectbox("Inactivity Window Horizon", [7, 30], index=1)
    target_col = "Sold 7D" if dead_stock_days == 7 else "Sold 30D"
    
    dead_stock_list = []
    for model, stock in inventory_lookup.items():
        matched_sales = master_df[master_df['Model Number'] == model][target_col].sum()
        if stock > 0 and matched_sales == 0:
            dead_stock_list.append({
                "SLOW-MOVING MODEL": model,
                "WAREHOUSE UNITS SITTING": int(stock),
                "TOTAL SALES IN HORIZON": 0,
                "RECOMMENDED ACTION": "Clear Shelf Space / Transfer Capital"
            })
            
    if dead_stock_list:
        st.dataframe(pd.DataFrame(dead_stock_list), hide_index=True, use_container_width=True)
    else:
        st.success("Great job! No dead stock or unmoving inventory models located matching these parameters.")
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 4: SAFETY STOCK & REORDER POINTS (ROP)
    # ----------------------------------------------------
    st.subheader("4. Smart Reorder Trigger (When to Buy)")
    st.write("Calculates standard logistics points based on your specific turnaround windows.")
    
    col_lead, col_cushion = st.columns(2)
    with col_lead:
        param_lead_time = st.number_input("Supplier Delivery Lead Time (Days)", min_value=1, max_value=14, value=2)
    with col_cushion:
        param_safety_cushion = st.number_input("Mandatory Safety Stock Cushion (Days of Sales)", min_value=1, max_value=14, value=4)
        
    rop_data = []
    for index, row in master_df.iterrows():
        model = row['Model Number']
        daily_vel = row['Weighted Weekly Avg'] / 7.0
        current_inv = inventory_lookup.get(model, 0)
        
        reorder_point = (daily_vel * param_lead_time) + (daily_vel * param_safety_cushion)
        triggered = "⚠️ ORDER NOW" if current_inv <= reorder_point else "Stock Stable"
        
        rop_data.append({
            "MODEL ID": model,
            "DAILY SALES VELOCITY": daily_vel,
            "CALCULATED REORDER POINT (ROP)": reorder_point,
            "CURRENT SHOP STOCK": int(current_inv),
            "LOGISTICS TRIGGER ACTION": triggered
        })
        
    rop_df = pd.DataFrame(rop_data)
    
    def style_rop(val):
        if "⚠️" in str(val): return 'background-color: #fce8e6; color: #a83232; font-weight: bold;'
        return 'color: #2b7a4b;'
        
    st.dataframe(rop_df.style.map(style_rop, subset=["LOGISTICS TRIGGER ACTION"]).format({"DAILY SALES VELOCITY": "{:.2f}", "CALCULATED REORDER POINT (ROP)": "{:.2f}"}), hide_index=True, use_container_width=True)
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 5: CALENDAR DEADLINES
    # ----------------------------------------------------
    st.subheader("5. Exact Calendar Stockout Deadlines")
    st.write("Projects exact calendar dates when inventory drops to zero, skipping Sundays and major field holidays.")
    
    calendar_data = []
    base_date = datetime.date.today()
    
    for index, row in master_df.iterrows():
        model = row['Model Number']
        net_stock = int(row['In Shop']) - int(row['Reserved'])
        daily_vel = row['Weighted Weekly Avg'] / 7.0
        
        if net_stock <= 0:
            deadline_str = "❌ OUT OF STOCK NOW"
        elif daily_vel <= 0:
            deadline_str = "Stable Stock (No Active Demand)"
        else:
            sim_stock = float(net_stock)
            current_projected_date = base_date
            loop_safety = 0
            
            while sim_stock > 0 and loop_safety < 365:
                current_projected_date += datetime.timedelta(days=1)
                if is_operational_day(current_projected_date):
                    sim_stock -= daily_vel
                loop_safety += 1
                
            deadline_str = current_projected_date.strftime("%B %d, %Y")
            
        calendar_data.append({
            "MODEL NUMBER": model,
            "NET AVAILABLE STOCK": net_stock,
            "DAILY CONSUMPTION VELOCITY": daily_vel,
            "EXPECTED STOCKOUT DEADLINE": deadline_str,
            "_raw_days_sort": loop_safety if net_stock > 0 and daily_vel > 0 else (0 if net_stock <= 0 else 999)
        })
        
    calendar_df = pd.DataFrame(calendar_data).sort_values(by="_raw_days_sort", ascending=True).drop(columns=["_raw_days_sort"])
    st.dataframe(calendar_df.style.format({"DAILY CONSUMPTION VELOCITY": "{:.2f}"}), hide_index=True, use_container_width=True)
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 6: WEEKDAY RUSH PLANNER
    # ----------------------------------------------------
    st.subheader("6. Weekday Rush Planner (48-Hour Order Scheduling)")
    st.write("Maps your real installation volume from Monday to Saturday to identify volume spikes and schedule orders around your 48-hour delivery timeline.")
    
    if not df_installed.empty and 'Install Date' in df_installed.columns:
        df_installed['Weekday_Name'] = df_installed['Install Date'].dt.day_name()
        df_installed['Weekday_Index'] = df_installed['Install Date'].dt.weekday  
        
        weekly_distribution = df_installed.groupby(['Weekday_Name', 'Weekday_Index'])['Quantity'].sum().reset_index()
        weekly_distribution = weekly_distribution[weekly_distribution['Weekday_Index'] != 6]
        
        total_volume = weekly_distribution['Quantity'].sum() if weekly_distribution['Quantity'].sum() > 0 else 1
        weekly_distribution['% of Weekly Total'] = (weekly_distribution['Quantity'] / total_volume) * 100
        
        weekly_distribution = weekly_distribution.sort_values(by="Weekday_Index", ascending=True)
        max_volume = weekly_distribution['Quantity'].max()
        
        day_indexer = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday"}
        rush_planner_rows = []
        
        for _, r in weekly_distribution.iterrows():
            rush_day_idx = r['Weekday_Index']
            order_day_idx = (rush_day_idx - 2) % 7
            
            order_day_string = day_indexer.get(order_day_idx, "Monday")
            if order_day_idx == 6:  
                order_day_string = "🚨 Saturday Morning (Shifted due to Sunday off)"
                
            is_spike = r['Quantity'] == max_volume and max_volume > 0
            status_string = "🔥 PRIMARY INSTALL SPIKE" if is_spike else "Standard Flow"
            
            bar_scale = max(1, int(r['% of Weekly Total'] / 4)) if r['% of Weekly Total'] > 0 else 0
            visual_bar = "🟩" * bar_scale if not is_spike else "🔥" * bar_scale
            
            rush_planner_rows.append({
                "DAY OF WEEK": r['Weekday_Name'],
                "TOTAL INSTALLS": int(r['Quantity']),
                "% OF WEEKLY DISTRIBUTION": r['% of Weekly Total'],
                "VOLUME VISUALIZER": visual_bar,
                "48-HOUR ORDER DEADLINE": order_day_string,
                "WEEKDAY PROFILE": status_string
            })
            
        rush_planner_df = pd.DataFrame(rush_planner_rows)
        
        def style_rush(val):
            if "🔥" in str(val): return 'background-color: #fff3cd; color: #856404; font-weight: bold;'
            return ''
            
        st.dataframe(
            rush_planner_df.style.map(style_rush, subset=["WEEKDAY PROFILE"]).format({"% OF WEEKLY DISTRIBUTION": "{:.1f}%"}), 
            hide_index=True, 
            use_container_width=True
        )
    else:
        st.info("Insufficient historical text records located in sheet repository to construct operational tracking metrics.")
    st.write("---")

    # ----------------------------------------------------
    # SANDBOX FEATURE 7: JOB DEMAND PREDICTABILITY SCORE
    # ----------------------------------------------------
    st.subheader("7. Job Demand Predictability Score (Smooth vs. Wild)")
    st.write("Analyzes historical timing intervals between installations. High variance flags chaotic demand spikes; low variance signals predictable stability.")

    if not df_installed.empty:
        df_sorted = df_installed.sort_values(['Model Number', 'Install Date']).copy()
        df_sorted['Prev_Install_Date'] = df_sorted.groupby('Model Number')['Install Date'].shift(1)
        df_sorted['Days_Between'] = (df_sorted['Install Date'] - df_sorted['Prev_Install_Date']).dt.days

        gap_stats = df_sorted.groupby('Model Number')['Days_Between'].agg(['mean', 'std']).reset_index()
        
        predictability_rows = []
        for index, row in gap_stats.iterrows():
            model = row['Model Number']
            avg_gap = row['mean']
            std_gap = row['std']
            
            current_inv = inventory_lookup.get(model, 0)
            
            if pd.isna(avg_gap) or avg_gap == 0:
                score_label = "⚪ Insufficient Data"
                explanation = "Requires multiple unique historical job dates to evaluate patterns."
            else:
                cv = std_gap / avg_gap if std_gap > 0 else 0
                
                if cv > 1.2:
                    score_label = "🌶️ WILD / CHAOTIC"
                    explanation = "Sits dead for weeks, then sells in massive unpredictable clusters. Keep extra buffer."
                elif cv > 0.6:
                    score_label = "⚡ MODERATE FLOW"
                    explanation = "Standard job profile. Normal moving fluctuations."
                else:
                    score_label = "🟢 SMOOTH / CONSISTENT"
                    explanation = "Moves like clockwork at a steady, fixed cadence. Safe to put on auto-pilot."
            
            predictability_rows.append({
                "MODEL NUMBER": model,
                "CURRENT PHYSICAL STOCK": int(current_inv),
                "AVG DAYS BETWEEN INSTALLS": "N/A" if pd.isna(avg_gap) or avg_gap == 0 else f"{avg_gap:.1f} Days",
                "DEMAND PROFILE GRADE": score_label,
                "OPERATIONAL GUIDANCE": explanation,
                "_raw_sort_key": avg_gap if not pd.isna(avg_gap) and avg_gap > 0 else 9999
            })
            
        predictability_df = pd.DataFrame(predictability_rows)
        predictability_df = predictability_df.sort_values(by="_raw_sort_key", ascending=True).drop(columns=["_raw_sort_key"])
        
        def style_predictability(val):
            if "WILD" in str(val): return 'background-color: #fce8e6; color: #a83232; font-weight: bold;'
            if "SMOOTH" in str(val): return 'background-color: #d4edda; color: #155724; font-weight: bold;'
            if "MODERATE" in str(val): return 'background-color: #e2f0fd; color: #1a53a1;'
            return ''
            
        st.dataframe(
            predictability_df.style.map(style_predictability, subset=["DEMAND PROFILE GRADE"]), 
            hide_index=True, 
            use_container_width=True
        )
    else:
        st.info("Insufficient job history to process interval scoring models.")
