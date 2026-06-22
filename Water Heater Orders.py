import streamlit as st
import pandas as pd
import datetime
import urllib.parse

st.set_page_config(layout="wide")
st.title("Water Heater Auto-Ordering Dashboard")

# 🔗 Connected Live Google Sheet URL
GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1j96q7srUuKpBtI1QUVaSvNWfhmEmKb-0xuuslE5j944/edit"

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
        gsheet_id = "1j96q7srUuKpBtI1QUVaSvNWfhmEmKb-0xuuslE5j944"
except Exception:
    gsheet_id = "1j96q7srUuKpBtI1QUVaSvNWfhmEmKb-0xuuslE5j944"

# Direct multi-tab extraction paths
url_usage = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heaters+Sold_Intalled"
url_details = f"https://docs.google.com/spreadsheets/d/{gsheet_id}/gviz/tq?tqx=out:csv&sheet=Water+Heater+Details"

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

# --- MODEL NUMBER CLEANING ENGINE ---
def clean_model_ids(series):
    return series.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)

df.columns = df.columns.str.strip()
df['Model Number'] = clean_model_ids(df['Model Number'])
df = df[df['Model Number'] != 'nan']
df['Install Date'] = pd.to_datetime(df['Scheduled/ Completed Install Date'], errors='coerce')

df_details.columns = df_details.columns.str.strip()
df_details['Model'] = clean_model_ids(df_details['Model'])

def clean_price_col(dataframe, col_name):
    if col_name in dataframe.columns:
        return pd.to_numeric(dataframe[col_name].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip(), errors='coerce').fillna(0)
    return pd.Series([0.0] * len(dataframe))

df_details['Bulk Price'] = clean_price_col(df_details, 'BULK PRICE ONLINE')
df_details['Store Price'] = clean_price_col(df_details, 'NXLVL STORE PRICE')

df_details_unique = df_details.drop_duplicates('Model', keep='first')
inventory_lookup = df_details_unique.set_index('Model')['Counted Inventory'].fillna(0).to_dict()
bulk_lookup = df_details_unique.set_index('Model')['Bulk Price'].fillna(0).to_dict()
store_lookup = df_details_unique.set_index('Model')['Store Price'].fillna(0).to_dict()

# --- GLOBAL OPERATIONAL CALENDAR REGULATION ---
def get_operational_days_count(start_date, end_date):
    """Calculates true workdays by completely eliminating Sundays and major fixed holidays."""
    if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
        return 1
    total_days = (end_date - start_date).days + 1
    work_days = 0
    for i in range(total_days):
        check_date = start_date + pd.Timedelta(days=i)
        if check_date.weekday() == 6:  # Sunday Blackout
            continue
        if check_date.month == 12 and check_date.day == 25:  # Christmas
            continue
        if check_date.month == 11 and check_date.weekday() == 3 and (22 <= check_date.day <= 28):  # Thanksgiving
            continue
        work_days += 1
    return max(1, work_days)

# --- 2. FORECAST CALCULATIONS ENGINE ---
df_installed = df[df['WH Status'] == 'Installed'].copy()
df_estimates = df[df['WH Status'] == 'Estimate Accepted'].copy()

reserved_stock = df_estimates.groupby('Model Number')['Quantity'].sum().reset_index().rename(columns={'Quantity': 'Reserved'})

max_date = df_installed['Install Date'].max()
min_date = df_installed['Install Date'].min()

date_7_days_ago = max_date - pd.Timedelta(days=7)
date_14_days_ago = max_date - pd.Timedelta(days=14)
date_21_days_ago = max_date - pd.Timedelta(days=21)
date_30_days_ago = max_date - pd.Timedelta(days=30)

# True Operational Workday Denominators (Eliminating Sunday/Holiday biases)
op_days_all = get_operational_days_count(min_date, max_date)
op_days_7d = get_operational_days_count(date_7_days_ago, max_date)
op_days_30d = get_operational_days_count(date_30_days_ago, max_date)
op_days_trend_window = get_operational_days_count(date_21_days_ago, date_14_days_ago)

# --- SIDEBAR CONTROL ENGINE ---
st.sidebar.header("Warehouse & Order Settings")
tax_input = st.sidebar.number_input("Assumed Tax Rate (%)", min_value=0.0, max_value=50.0, value=8.5, step=0.1)
TAX_RATE = tax_input / 100.0

target_mode = st.sidebar.selectbox("Suggested Quantity Targeting Mode", ["💰 Budget Goal ($)", "📦 Warehouse Capacity (Units)"])

if target_mode == "💰 Budget Goal ($)":
    price_goal = st.sidebar.number_input("Total Order Price Goal (with tax)", min_value=500, max_value=50000, value=6500, step=500)
else:
    target_total_inventory = st.sidebar.slider("Target Total Warehouse Capacity", min_value=10, max_value=100, value=25)

st.sidebar.subheader("Usage Weighting (%)")
weight_7d = st.sidebar.number_input("Last 7 Days Weight", min_value=0, max_value=100, value=65, step=1) 
weight_30d = st.sidebar.number_input("Last 30 Days Weight", min_value=0, max_value=100, value=30, step=1) 
weight_all = st.sidebar.number_input("All-Time Weight", min_value=0, max_value=100, value=5, step=1)    

if (weight_7d + weight_30d + weight_all) != 100:
    st.sidebar.error("Weights must equal 100% total.")
    st.stop()

# Build Timeframe Aggregations
all_time = df_installed.groupby('Model Number')['Quantity'].sum().reset_index()
all_time['All Time Weekly Avg'] = (all_time['Quantity'] / op_days_all) * 6  # Normalized standard 6-day operational week

last_install_df = df_installed.groupby('Model Number')['Install Date'].max().reset_index()
last_install_df.rename(columns={'Install Date': 'Last Install Date'}, inplace=True)
last_install_df['Last Install Date'] = last_install_df['Last Install Date'].dt.strftime('%m/%d/%Y').fillna('No Record')

usage_30d = df_installed[df_installed['Install Date'] >= date_30_days_ago].groupby('Model Number')['Quantity'].sum().reset_index()
usage_30d['30D Weekly Avg'] = (usage_30d['Quantity'] / op_days_30d) * 6

usage_7d = df_installed[df_installed['Install Date'] >= date_7_days_ago].groupby('Model Number')['Quantity'].sum().reset_index()
usage_7d['7D Weekly Avg'] = (usage_7d['Quantity'] / op_days_7d) * 6

# Velocity Momentum Analytics Evaluation (7-Day vs Previous 14-21 Day Baseline Gap)
usage_trend_baseline = df_installed[(df_installed['Install Date'] >= date_21_days_ago) & (df_installed['Install Date'] <= date_14_days_ago)].groupby('Model Number')['Quantity'].sum().reset_index()
usage_trend_baseline['Historical Weekly Baseline'] = (usage_trend_baseline['Quantity'] / op_days_trend_window) * 6

# Assemble Base Dataset Frame
master_df = all_time[['Model Number', 'Quantity', 'All Time Weekly Avg']]
master_df = pd.merge(master_df, usage_30d, on='Model Number', how='left').rename(columns={'Quantity_y': 'Sold 30D'}).fillna(0)
master_df = pd.merge(master_df, usage_7d, on='Model Number', how='left').rename(columns={'Quantity': 'Sold 7D'}).fillna(0)
master_df = pd.merge(master_df, last_install_df, on='Model Number', how='left').fillna({'Last Install Date': 'No Record'})
master_df = pd.merge(master_df, usage_trend_baseline[['Model Number', 'Historical Weekly Baseline']], on='Model Number', how='left').fillna(0)

# Sort by active 30-day catalog distribution and restrict to top 12 primary units
master_df = master_df.sort_values(by='Sold 30D', ascending=False).head(12).reset_index(drop=True)

# Compute Modulated Weighted Velocity Averages
w_7d, w_30d, w_all = weight_7d / 100.0, weight_30d / 100.0, weight_all / 100.0
master_df['Weighted Weekly Avg'] = (master_df['7D Weekly Avg'] * w_7d) + (master_df['30D Weekly Avg'] * w_30d) + (master_df['All Time Weekly Avg'] * w_all)

total_weighted_avg = master_df['Weighted Weekly Avg'].sum() if master_df['Weighted Weekly Avg'].sum() > 0 else 1
master_df['Share %'] = master_df['Weighted Weekly Avg'] / total_weighted_avg

master_df = pd.merge(master_df, reserved_stock, on='Model Number', how='left').fillna(0)
master_df['In Shop'] = master_df['Model Number'].map(inventory_lookup).fillna(0).astype(int)
master_df['Bulk Price'] = master_df['Model Number'].map(bulk_lookup).fillna(0)

# Velocity Momentum Evaluator Logic Array
def assign_velocity_trend(row):
    current = row['7D Weekly Avg']
    prior = row['Historical Weekly Baseline']
    diff = current - prior
    if diff > 0.3: return "▲ Speeding Up"
    elif diff < -0.3: return "▼ Slowing Down"
    return "■ Stable"

master_df['Velocity Trend Indicator'] = master_df.apply(assign_velocity_trend, axis=1)

# Linear Allocation Capital Solver Logic block
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

# --- 3. UI PLATFORM TAB CODES ---
tab1, tab2, tab3 = st.tabs(["📋 Interactive Order Sheet", "📊 Forecasting Breakdown", "🧪 Feature Sandbox"])

# ----------------------------------------------------
# TAB 1: OPERATIONAL ORDER GENERATOR
# ----------------------------------------------------
with tab1:
    st.subheader("Master Weekly Bulk Order Sheet")
    st.write("**Click directly on any number in the `ORDER QTY` column** to manually override configuration targets.")

    order_sheet_data = []
    for index, row in master_df.iterrows():
        model = row['Model Number']
        target_inv = row['Target Capacity']
        reserved = int(row['Reserved'])
        current_inv = int(row['In Shop'])
        
        bulk_price = bulk_lookup.get(model, 0.0)
        store_price = store_lookup.get(model, 0.0)
        savings = max(0, store_price - bulk_price)
        
        effective_inv = current_inv - reserved
        order_amt = max(0, target_inv - effective_inv)
        status = "🟢 ORDER" if order_amt > 0 else "✔️ OK"
        
        order_sheet_data.append({
            "STATUS": status, "MODEL": model, "LAST INSTALL DATE": row['Last Install Date'],
            "WAREHOUSE STOCK": current_inv, "PENDING INSTALLS": reserved, "ORDER QTY": order_amt, 
            "INSTALLED/SOLD IN PAST 7 DAYS": int(row['Sold 7D']), "SOLD IN LAST 30 DAYS": int(row['Sold 30D']),
            "BULK PRICE ONLINE": bulk_price, "NXLVL STORE PRICE": store_price, "SAVINGS": savings
        })

    order_df = pd.DataFrame(order_sheet_data).sort_values(by="SOLD IN LAST 30 DAYS", ascending=False)
    reordered_cols = ["STATUS", "MODEL", "LAST INSTALL DATE", "WAREHOUSE STOCK", "PENDING INSTALLS", "ORDER QTY", "INSTALLED/SOLD IN PAST 7 DAYS", "SOLD IN LAST 30 DAYS", "BULK PRICE ONLINE", "NXLVL STORE PRICE", "SAVINGS"]
    order_df = order_df[reordered_cols]

    def highlight_ordered_models(df_input):
        style_df = pd.DataFrame('', index=df_input.index, columns=df_input.columns)
        mask = df_input['ORDER QTY'] > 0
        style_df.loc[mask, 'MODEL'] = 'background-color: #d4edda; font-weight: bold; color: #155724;'
        return style_df

    edited_df = st.data_editor(
        order_df.style.apply(highlight_ordered_models, axis=None),
        column_config={
            "ORDER QTY": st.column_config.NumberColumn("ORDER QTY ✏️", min_value=0, step=1),
            "BULK PRICE ONLINE": st.column_config.NumberColumn(format="$%.2f"),
            "NXLVL STORE PRICE": st.column_config.NumberColumn(format="$%.2f"),
            "SAVINGS": st.column_config.NumberColumn(format="$%.2f"),
        },
        disabled=["STATUS", "MODEL", "LAST INSTALL DATE", "WAREHOUSE STOCK", "PENDING INSTALLS", "INSTALLED/SOLD IN PAST 7 DAYS", "SOLD IN LAST 30 DAYS", "BULK PRICE ONLINE", "NXLVL STORE PRICE", "SAVINGS"],
        hide_index=True, use_container_width=True
    )

    # Financial Totaling Block Calculations
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
        st.write(f"**Estimated Tax ({tax_input}%):** ${bulk_tax:,.2f}")
        st.markdown(f"**TOTAL BULK COST:** `${total_bulk_cost_with_tax:,.2f}`")
    with col_store:
        st.markdown("### 🏢 Regular Store Price")
        st.write(f"**Subtotal:** ${base_store_cost:,.2f}")
        st.write(f"**Estimated Tax ({tax_input}%):** ${store_tax:,.2f}")
        st.markdown(f"**TOTAL STORE COST:** `${total_store_cost_with_tax:,.2f}`")
    with col_summary:
        st.markdown("### 📈 Order Volume Metrics")
        st.metric("Total Heaters Selected", int(total_units))
        st.metric("Net Financial Savings", f"${max(0.0, net_savings):,.2f}")

    st.divider()

    st.subheader("✉️ Copy & Paste Rich Text Email Draft")
    quick_copy_base = edited_df[edited_df["ORDER QTY"] > 0].copy()
    if not quick_copy_base.empty:
        table_markdown_rows = ""
        for _, r in quick_copy_base.iterrows():
            table_markdown_rows += f"| {r['MODEL']} | {int(r['ORDER QTY'])} | ${r['BULK PRICE ONLINE']:,.2f} | ${r['NXLVL STORE PRICE']:,.2f} |\n"

        email_rich_template = f"Please see the water heater order below. Let me know how soon these can be delivered and if you have any questions. Thanks!\n\nPlease send payment request to my cell. 804-536-4748\n\n| MODEL | ORDER QTY | BULK PRICE | STORE PRICE |\n| :--- | :--- | :--- | :--- |\n{table_markdown_rows}\n**Total Quantity Ordered:** {int(total_units)} unit(s)\n**Subtotal:** ${base_bulk_cost:,.2f}\n**Estimated Tax ({tax_input}%):** ${bulk_tax:,.2f}\n**TOTAL BULK COST:** ${total_bulk_cost_with_tax:,.2f}"
        st.markdown(f'<div style="background-color: #fcfcfc; padding: 25px; border-radius: 8px; border: 1px solid #eaeaea; line-height: 1.6;">{email_rich_template}</div>', unsafe_allow_html=True)
    else:
        st.info("No items currently marked for order matching the current configuration.")

# ----------------------------------------------------
# TAB 2: FORECASTING & EXTENDED ANALYTICS
# ----------------------------------------------------
with tab2:
    st.header("📈 Inventory Velocity & Extended Forecasting Engine")
    
    # 1. Standard Calculations Dataframe Table Output
    st.subheader("Core Inventory Allocation Matrix")
    st.dataframe(master_df[['Model Number', 'Last Install Date', 'Weighted Weekly Avg', 'Share %', 'Velocity Trend Indicator', 'Target Capacity', 'In Shop', 'Reserved']], use_container_width=True, hide_index=True)
    st.write("---")

    col_left, col_right = st.columns([1, 1])
    
    with col_left:
        # 2. "Estimate Accepted" Stagnant Bottleneck Tracker Module
        st.subheader("⏳ 'Estimate Accepted' Pipeline Bottleneck Tracker")
        st.write("Chronological tracking of pending pipeline allocations currently consuming warehouse safety stock:")
        if not df_estimates.empty:
            df_est_clean = df_estimates.sort_values(by='Install Date', ascending=True).copy()
            df_est_clean['Days Stagnant'] = (datetime.datetime.now() - df_est_clean['Install Date']).dt.days.fillna(0).astype(int)
            
            bottleneck_display = df_est_clean[['Model Number', 'Quantity', 'Scheduled/ Completed Install Date', 'Days Stagnant']].rename(
                columns={'Scheduled/ Completed Install Date': 'Accepted Estimate Setup Date', 'Quantity': 'Units Staged Reserved'}
            )
            st.dataframe(bottleneck_display, use_container_width=True, hide_index=True)
        else:
            st.info("Excellent! No pipeline backup records or accepted estimates currently stalled in logistics records.")

    with col_right:
        # 3. Catalog Market Volume Saturation Pie Chart Visualizer Module
        st.subheader("🍕 Catalog Saturation Market Share Volume")
        st.write("Percentage share breakdown of top catalog items relative to comprehensive service footprints:")
        pie_data = master_df[['Model Number', 'Quantity']].copy()
        pie_data.columns = ['Model Number', 'Total Historical Installed Units']
        st.pie_chart(pie_data, values='Total Historical Installed Units', names='Model Number')

    st.write("---")
    
    # 4. Historical Matrix Grid Mapping Module (Pivot Presentation Breakdown)
    st.subheader("📅 Long-Horizon Historical Monthly Installation Matrix")
    st.write("Year-over-Year inventory consumption profiles tracking seasonal fluctuations and operational scaling metrics:")
    if not df_installed.empty:
        df_installed['Year'] = df_installed['Install Date'].dt.year.fillna(0).astype(int)
        df_installed['Month'] = df_installed['Install Date'].dt.strftime('%B')
        df_installed['Month_Num'] = df_installed['Install Date'].dt.month.fillna(0).astype(int)
        
        # Build strict model boundary isolation filter rules
        valid_models = master_df['Model Number'].tolist()
        matrix_filtered = df_installed[df_installed['Model Number'].isin(valid_models)].copy()
        
        if not matrix_filtered.empty:
            pivot_matrix = matrix_filtered.pivot_table(
                index=['Model Number'],
                columns=['Year', 'Month_Num', 'Month'],
                values='Quantity',
                aggfunc='sum'
            ).fillna(0).astype(int)
            
            # Formatting layout configurations to simplify raw MultiIndex outputs
            pivot_matrix.columns = [f"{yr} - {m_name}" for yr, m_num, m_name in pivot_matrix.columns]
            st.dataframe(pivot_matrix, use_container_width=True)
        else:
            st.info("Insufficient historical date metrics available to compile long-horizon summary tables.")
    else:
        st.info("Historical tracking log is currently empty.")

# ----------------------------------------------------
# TAB 3: LOGISTICS FEATURE SANDBOX
# ----------------------------------------------------
with tab3:
    st.header("🧪 Advanced Logistics Feature Sandbox")
    st.write("Interact with functional prototypes of advanced logistical calculators using your live data metrics.")
    st.write("---")

    # SANDBOX FEATURE 1: DIRECT VENDOR PORTAL EMAIL ENGINE
    st.subheader("📬 1. Direct Vendor Portal Email Routing Engine")
    col_to, col_cc, col_sub = st.columns(3)
    with col_to: email_to = st.text_input("To (Distributor Order Desk Recipient):", value="orders@distributor.com")
    with col_cc: email_cc = st.text_input("CC (Internal Records / Management):", value="management@nexlvlservices.com")
    with col_sub: email_subject = st.text_input("Preferred Subject Line:", value="Weekly Bulk Water Heater Warehouse Stock Order")

    if not quick_copy_sandbox.empty:
        plain_text_body = f"Please see the water heater order below. Let me know how soon these can be delivered and if you have any questions. Thanks!\n\nPlease send payment request to my cell. 804-536-4748\n\nTotal Quantity Ordered: {int(total_units)} unit(s)\nSubtotal: ${base_bulk_cost:,.2f}\nEstimated Tax ({tax_input}%): ${bulk_tax:,.2f}\nTOTAL BULK COST: ${total_bulk_cost_with_tax:,.2f}\n\nORDER DETAILS:\n----------------------------------------\n"
        for _, r in quick_copy_base.iterrows():
            plain_text_body += f"• Model: {r['MODEL']} | Qty: {int(r['ORDER QTY'])} | Bulk Unit Price: ${r['BULK PRICE ONLINE']:,.2f}\n"
        plain_text_body += "----------------------------------------\n"
        
        safe_to, safe_cc, safe_sub, safe_body = urllib.parse.quote(email_to), urllib.parse.quote(email_cc), urllib.parse.quote(email_subject), urllib.parse.quote(plain_text_body)
        mailto_url = f"mailto:{safe_to}?cc={safe_cc}&subject={safe_sub}&body={safe_body}"
        st.markdown(f'<a href="{mailto_url}" target="_blank" style="text-decoration:none;"><button style="background-color:#007bff; color:white; border:none; padding:12px 24px; border-radius:6px; cursor:pointer; font-weight:bold; font-size:15px;">📧 Open Desktop Email Client & Pre-Fill Order</button></a>', unsafe_allow_html=True)
    else:
        st.info("Mark models for order on Tab 1 to activate the email engine preview.")
    st.write("---")

    # SANDBOX FEATURE 2: SEASONAL DEMAND WEATHER FORECASTING
    st.subheader("☀️ 2. Seasonal Demand Forecasting (Weather Correlation)")
    heat_wave_active = st.toggle("Simulate Regional Summer Heat Wave Strain (Extreme Ambient Tracking)", value=False)
    multiplier_buffer = 1.25 if heat_wave_active else 1.00

    # SANDBOX FEATURE 3: RUNOUT TRACKER
    st.subheader("3. Runout Tracker (Days of Stock Left)")
    velocity_slider = st.slider("Simulated Sales Velocity Multiplier", min_value=0.5, max_value=3.0, value=1.0, step=0.1)
    
    runout_data = []
    for index, row in master_df.iterrows():
        model = row['Model Number']
        shop_stock = int(row['In Shop'])
        reserved = int(row['Reserved'])
        
        # Integrates structural Sunday/Holiday filters combined with simulated heat adjustments
        daily_velocity = ((row['Weighted Weekly Avg'] / 6.0) * velocity_slider * multiplier_buffer)
        net_stock = shop_stock - reserved
        
        days_left = 999 if daily_velocity <= 0 else max(0, int(net_stock / daily_velocity))
        if net_stock <= 0: days_left = 0
        alert = "🔴 CRITICAL STOCK" if days_left <= 3 else ("Warning" if days_left <= 8 else "🟢 HEALTHY")
            
        runout_data.append({
            "MODEL": model, "CURRENT PHYSICAL STOCK": shop_stock, "PENDING INSTALLS": reserved,
            "NET AVAILABLE STOCK": net_stock, "SOLD IN PAST 7 DAYS": int(row['Sold 7D']),
            "SOLD IN PAST 30 DAYS": int(row['Sold 30D']), "DAILY VELOCITY": daily_velocity,
            "EST. DAYS LEFT": "STOCKED OUT" if net_stock <= 0 else (f"{days_left} Days" if days_left < 365 else "Stable Stock"),
            "STATUS RUNOUT ALERT": alert, "_raw_sort_key": days_left
        })
    
    runout_df = pd.DataFrame(runout_data).sort_values(by="_raw_sort_key", ascending=True).drop(columns=["_raw_sort_key"])
    def style_runout(val):
        if "🔴" in str(val): return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
        if "Warning" in str(val): return 'background-color: #fff3cd; color: #856404;'
        return 'background-color: #d4edda; color: #155724;' if "🟢" in str(val) else ''
    st.dataframe(runout_df.style.map(style_runout, subset=["STATUS RUNOUT ALERT"]), hide_index=True, use_container_width=True)
    st.write("---")

    # SANDBOX FEATURE 4: SALES VISUALIZER
    st.subheader("4. Sales Visualizer (What's Hot)")
    sales_table_df = master_df[['Model Number', 'Sold 30D']].copy().sort_values(by='Sold 30D', ascending=False)
    sales_table_df.columns = ["MODEL NUMBER", "UNITS SOLD (PAST 30 DAYS)"]
    st.dataframe(sales_table_df, hide_index=True, use_container_width=True)
    st.write("---")

    # ----------------------------------------------------
    # UPGRADED SANDBOX FEATURE 5: DEAD STOCK DETECTOR WITH HORIZON INPUT CONTROLLER
    # ----------------------------------------------------
    st.subheader("🕷️ 5. Dead Stock Finder (Inactivity Horizon Threshold Controller)")
    st.write("Trace low-velocity or stagnant warehouse inventory that has recorded zero sales activity across custom lookback parameters:")
    
    # Precise numerical lookback horizon input controller widget config
    dead_stock_days_horizon = st.number_input("Enter Custom Inactivity Horizon Window Threshold (Days):", min_value=1, max_value=180, value=45, step=1)
    
    horizon_cutoff_date = max_date - pd.Timedelta(days=dead_stock_days_horizon)
    active_sales_in_horizon = df_installed[df_installed['Install Date'] >= horizon_cutoff_date].groupby('Model Number')['Quantity'].sum().to_dict()
    
    dead_stock_list = []
    for model, stock in inventory_lookup.items():
        if stock > 0 and active_sales_in_horizon.get(model, 0) == 0:
            dead_stock_list.append({
                "STAGNANT CATLOG SKU MODEL": model,
                "PHYSICAL UNITS SITTING ON RACKS": int(stock),
                "RECORDED INSTALL CONSUMPTION": 0,
                "LOGISTICAL RECOMMENDATION": f"Flag as Dead Capital / Review Floor Space Allocation (Zero movement in {dead_stock_days_horizon} Days)"
            })
            
    if dead_stock_list:
        st.dataframe(pd.DataFrame(dead_stock_list), hide_index=True, use_container_width=True)
    else:
        st.success("Phenomenal configuration footprint! No inactive or dead storage equipment matches your custom lookback threshold.")
    st.write("---")

    # SANDBOX FEATURE 6: SAFETY STOCK & REORDER POINTS (ROP)
    st.subheader("6. Smart Reorder Trigger (When to Buy)")
    col_lead, col_cushion = st.columns(2)
    with col_lead: param_lead_time = st.number_input("Supplier Delivery Lead Time (Days)", min_value=1, max_value=14, value=2)
    with col_cushion: param_safety_cushion = st.number_input("Mandatory Safety Stock Cushion (Days of Sales)", min_value=1, max_value=14, value=4)
        
    rop_data = []
    for index, row in master_df.iterrows():
        model = row['Model Number']
        daily_vel = (row['Weighted Weekly Avg'] / 6.0) * multiplier_buffer
        current_inv = inventory_lookup.get(model, 0)
        reorder_point = (daily_vel * param_lead_time) + (daily_vel * param_safety_cushion)
        triggered = "⚠️ ORDER NOW" if current_inv <= reorder_point else "Stock Stable"
        
        rop_data.append({"MODEL ID": model, "DAILY SALES VELOCITY": daily_vel, "CALCULATED REORDER POINT (ROP)": reorder_point, "CURRENT SHOP STOCK": int(current_inv), "LOGISTICS TRIGGER ACTION": triggered})
        
    rop_df = pd.DataFrame(rop_data)
    def style_rop(val):
        return 'background-color: #fce8e6; color: #a83232; font-weight: bold;' if "⚠️" in str(val) else 'color: #2b7a4b;'
    st.dataframe(rop_df.style.map(style_rop, subset=["LOGISTICS TRIGGER ACTION"]).format({"DAILY SALES VELOCITY": "{:.2f}", "CALCULATED REORDER POINT (ROP)": "{:.2f}"}), hide_index=True, use_container_width=True)
    st.write("---")

    # SANDBOX FEATURE 7: CALENDAR DEADLINES
    st.subheader("7. Exact Calendar Stockout Deadlines")
    calendar_data = []
    base_date = datetime.date.today()
    
    for index, row in master_df.iterrows():
        model = row['Model Number']
        net_stock = int(row['In Shop']) - int(row['Reserved'])
        daily_vel = (row['Weighted Weekly Avg'] / 6.0) * multiplier_buffer
        
        if net_stock <= 0: deadline_str, loop_safety = "❌ OUT OF STOCK NOW", 0
        elif daily_vel <= 0: deadline_str, loop_safety = "Stable Stock (No Active Demand)", 999
        else:
            sim_stock, current_projected_date, loop_safety = float(net_stock), base_date, 0
            while sim_stock > 0 and loop_safety < 365:
                current_projected_date += datetime.timedelta(days=1)
                
                # Check calendar deadlines using explicit blackout workday logic rules
                # Checks structural validation conditions (skipping operational days)
                is_op = True
                if current_projected_date.weekday() == 6: is_op = False
                elif current_projected_date.month == 12 and current_projected_date.day == 25: is_op = False
                elif current_projected_date.month == 11 and current_projected_date.weekday() == 3 and (22 <= current_projected_date.day <= 28): is_op = False
                
                if is_op:
                    sim_stock -= daily_vel
                loop_safety += 1
            deadline_str = current_projected_date.strftime("%B %d, %Y")
            
        calendar_data.append({"MODEL NUMBER": model, "NET AVAILABLE STOCK": net_stock, "DAILY CONSUMPTION VELOCITY": daily_vel, "EXPECTED STOCKOUT DEADLINE": deadline_str, "_raw_days_sort": loop_safety})
        
    calendar_df = pd.DataFrame(calendar_data).sort_values(by="_raw_days_sort", ascending=True)
    st.dataframe(calendar_df.style.format({"DAILY CONSUMPTION VELOCITY": "{:.2f}"}).drop(columns=["_raw_days_sort"]), hide_index=True, use_container_width=True)
    st.write("---")

    # SANDBOX FEATURE 8: WEEKDAY RUSH PLANNER
    st.subheader("8. Weekday Rush Planner (48-Hour Order Scheduling)")
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
            order_day_string = "🚨 Saturday Morning (Shifted due to Sunday off)" if order_day_idx == 6 else day_indexer.get(order_day_idx, "Monday")
            is_spike = r['Quantity'] == max_volume and max_volume > 0
            status_string = "🔥 PRIMARY INSTALL SPIKE" if is_spike else "Standard Flow"
            
            bar_scale = max(1, int(r['% of Weekly Total'] / 4)) if r['% of Weekly Total'] > 0 else 0
            visual_bar = "🟩" * bar_scale if not is_spike else "🔥" * bar_scale
            
            rush_planner_rows.append({"DAY OF WEEK": r['Weekday_Name'], "TOTAL INSTALLS": int(r['Quantity']), "% OF WEEKLY DISTRIBUTION": r['% of Weekly Total'], "VOLUME VISUALIZER": visual_bar, "48-HOUR ORDER DEADLINE": order_day_string, "WEEKDAY PROFILE": status_string})
            
        rush_planner_df = pd.DataFrame(rush_planner_rows)
        def style_rush(val):
            return 'background-color: #fff3cd; color: #856404; font-weight: bold;' if "🔥" in str(val) else ''
        st.dataframe(rush_planner_df.style.map(style_rush, subset=["WEEKDAY PROFILE"]).format({"% OF WEEKLY DISTRIBUTION": "{:.1f}%"}), hide_index=True, use_container_width=True)
    else:
        st.info("Insufficient historical records to map distribution metrics.")
    st.write("---")

    # SANDBOX FEATURE 9: JOB DEMAND PREDICTABILITY SCORE
    st.subheader("9. Job Demand Predictability Score (Smooth vs. Wild)")
    if not df_installed.empty:
        df_sorted = df_installed.sort_values(['Model Number', 'Install Date']).copy()
        df_sorted['Prev_Install_Date'] = df_sorted.groupby('Model Number')['Install Date'].shift(1)
        df_sorted['Days_Between'] = (df_sorted['Install Date'] - df_sorted['Prev_Install_Date']).dt.days
        gap_stats = df_sorted.groupby('Model Number')['Days_Between'].agg(['mean', 'std']).reset_index()
        
        predictability_rows = []
        for index, row in gap_stats.iterrows():
            model = row['Model Number']
            avg_gap, std_gap = row['mean'], row['std']
            current_inv = inventory_lookup.get(model, 0)
            
            if pd.isna(avg_gap) or avg_gap == 0:
                score_label, explanation = "⚪ Insufficient Data", "Requires multiple unique historical job dates to evaluate patterns."
            else:
                cv = std_gap / avg_gap if std_gap > 0 else 0
                if cv > 1.2: score_label, explanation = "🌶️ WILD / CHAOTIC", "Sits dead for weeks, then sells in massive unpredictable clusters. Keep extra buffer."
                elif cv > 0.6: score_label, explanation = "⚡ MODERATE FLOW", "Standard job profile. Normal moving fluctuations."
                else: score_label, explanation = "🟢 SMOOTH / CONSISTENT", "Moves like clockwork at a steady, fixed cadence. Safe to put on auto-pilot."
            
            predictability_rows.append({"MODEL NUMBER": model, "CURRENT PHYSICAL STOCK": int(current_inv), "AVG DAYS BETWEEN INSTALLS": "N/A" if pd.isna(avg_gap) or avg_gap == 0 else f"{avg_gap:.1f} Days", "DEMAND PROFILE GRADE": score_label, "OPERATIONAL GUIDANCE": explanation, "_raw_sort_key": avg_gap if not pd.isna(avg_gap) and avg_gap > 0 else 9999})
            
        predictability_df = pd.DataFrame(predictability_rows).sort_values(by="_raw_sort_key", ascending=True).drop(columns=["_raw_sort_key"])
        def style_predictability(val):
            if "WILD" in str(val): return 'background-color: #fce8e6; color: #a83232; font-weight: bold;'
            if "SMOOTH" in str(val): return 'background-color: #d4edda; color: #155724; font-weight: bold;'
            return 'background-color: #e2f0fd; color: #1a53a1;' if "MODERATE" in str(val) else ''
        st.dataframe(predictability_df.style.map(style_predictability, subset=["DEMAND PROFILE GRADE"]), hide_index=True, use_container_width=True)
    else:
        st.info("Insufficient job history to process interval scoring models.")
