import mysql.connector
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import APIKeyHeader
from datetime import datetime, timedelta
from ratelimit import limits, sleep_and_retry
from pydantic import BaseModel
from config import DB_CONFIG, API_KEY, BACK_UP_LOC
import os
import asyncio
import json
import threading
import time
from typing import Optional
import calendar
from pytz import timezone, utc 

last_backup_time = None

app = FastAPI()

api_key_header = APIKeyHeader(name="X-API-Key")

class ActiveCart(BaseModel):
    profileId: int
    createdAt: datetime
    updatedAt: datetime

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


activity_data = {}
last_calculation_date = None

def calculate_activity_probability():
    global activity_data, last_calculation_date



    current_date = datetime.now().date()

    if last_calculation_date == current_date:
        return

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT DAYNAME(updatedAt) AS day_of_week, HOUR(updatedAt) AS hour_of_day
            FROM ylift_api.carts
        """
        cursor.execute(query)

        activity_data = {}
        for row in cursor.fetchall():
            day_of_week = row[0]
            hour_of_day = row[1]

            if day_of_week not in activity_data:
                activity_data[day_of_week] = {
                    "probability": 0,
                    "busy_hours": {}
                }

            activity_data[day_of_week]["probability"] += 1

            hour_label = f"{hour_of_day:02d}:00 - {hour_of_day+1:02d}:00"
            if hour_label not in activity_data[day_of_week]["busy_hours"]:
                activity_data[day_of_week]["busy_hours"][hour_label] = 0

            activity_data[day_of_week]["busy_hours"][hour_label] += 1

        cursor.close()
        connection.close()

        max_activity = max(data["probability"] for data in activity_data.values())

        for day_of_week in activity_data:
            activity_data[day_of_week]["probability"] = round(activity_data[day_of_week]["probability"] / max_activity, 4)

            max_hours = max(activity_data[day_of_week]["busy_hours"].values())
            for hour_label in activity_data[day_of_week]["busy_hours"]:
                activity_data[day_of_week]["busy_hours"][hour_label] = round(activity_data[day_of_week]["busy_hours"][hour_label] / max_hours, 4)

            # Sort the 'busy_hours' dictionary based on hour labels
            sorted_busy_hours = dict(sorted(activity_data[day_of_week]["busy_hours"].items(), key=lambda x: x[0]))
            activity_data[day_of_week]["busy_hours"] = sorted_busy_hours

        last_calculation_date = current_date

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
@limits(calls=10, period=60) 
def health_check():
    try:
        connection = get_db_connection()
        if connection.is_connected():
            return {"status": "OK", "database": "Connected"}
        else:
            return {"status": "Error", "database": "Not Connected"}
    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        return {"status": "Error", "database": "Not Connected"}

@app.get("/carts")
@sleep_and_retry
@limits(calls=2, period=60) 
def get_active_carts(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=400, detail="Invalid API key")

    current_date = datetime.now().date()

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT profileId, createdAt, updatedAt
            FROM ylift_api.carts
            WHERE DATE(updatedAt) = %s
        """
        cursor.execute(query, (current_date,))

        active_carts = []
        for row in cursor.fetchall():
            active_cart = ActiveCart(
                profileId=row[0],
                createdAt=row[1],
                updatedAt=row[2]
            )
            active_carts.append(active_cart)

        cursor.close()
        connection.close()

        return active_carts

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/accounts")
@sleep_and_retry
@limits(calls=50, period=60) 
def get_active_accounts():
# def get_active_accounts(api_key: str = Depends(api_key_header)):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        current_date = datetime.now().date()
        yesterday = current_date - timedelta(days=1)

        # Get cartIds from cartItems for the current date
        query_cart_items = """
            SELECT DISTINCT cartId
            FROM ylift_api.cartItems
            WHERE DATE(updatedAt) = %s
        """
        cursor.execute(query_cart_items, (current_date,))
        cart_ids_from_items = [row[0] for row in cursor.fetchall()]

        # Get profileIds from active carts for the current date
        query_active_carts = """
            SELECT DISTINCT profileId
            FROM ylift_api.carts
            WHERE DATE(updatedAt) = %s
        """
        cursor.execute(query_active_carts, (current_date,))
        profile_ids_from_carts = [row[0] for row in cursor.fetchall()]

        # Get profileIds from carts associated with active cartItems
        if cart_ids_from_items:
            query_profiles_from_items = """
                SELECT DISTINCT profileId
                FROM ylift_api.carts
                WHERE id IN ({})
            """.format(','.join(['%s'] * len(cart_ids_from_items)))
            cursor.execute(query_profiles_from_items, cart_ids_from_items)
            profile_ids_from_items = [row[0] for row in cursor.fetchall()]
        else:
            profile_ids_from_items = []

        # Merge and deduplicate profile IDs
        profile_ids = list(set(profile_ids_from_carts + profile_ids_from_items))

        active_accounts = []

        for profile_id in profile_ids:
            query_profile = """
                SELECT email, name
                FROM ylift_api.profiles
                WHERE id = %s
            """
            cursor.execute(query_profile, (profile_id,))
            result = cursor.fetchone()

            if result:
                email, name = result

                # Check for purchases and open orders
                query_orders = """
                    SELECT COUNT(*) as num_purchases, 
                           SUM(CASE WHEN status != 'COMPLETED' THEN 1 ELSE 0 END) as open_orders
                    FROM ylift_api.orders
                    WHERE profileId = %s AND DATE(createdAt) = %s
                """
                cursor.execute(query_orders, (profile_id, current_date))
                order_result = cursor.fetchone()
                num_purchases = order_result[0] if order_result else 0

                # Check for cart items
                query_cart_items = """
                    SELECT COUNT(*) 
                    FROM ylift_api.cartItems ci
                    JOIN ylift_api.carts c ON ci.cartId = c.id
                    WHERE c.profileId = %s AND DATE(ci.updatedAt) = %s
                """
                cursor.execute(query_cart_items, (profile_id, current_date))
                has_cart_items = cursor.fetchone()[0] > 0

                active_accounts.append({
                    "id": profile_id,
                    "email": email,
                    "name": name,
                    "numPurchases": num_purchases,
                    "recentlyOrdered": num_purchases > 0,
                    "hasCartItems": has_cart_items
                })

        # If we have less than 5 accounts with purchases today, add accounts with purchases from yesterday
        if sum(account['numPurchases'] > 0 for account in active_accounts) < 5:
            query_yesterday_purchases = """
                SELECT DISTINCT o.profileId, p.email, p.name, 
                       COUNT(*) as num_purchases
                FROM ylift_api.orders o
                JOIN ylift_api.profiles p ON o.profileId = p.id
                WHERE DATE(o.createdAt) = %s AND o.profileId NOT IN ({})
                GROUP BY o.profileId
            """.format(','.join(['%s'] * len(profile_ids)))
            cursor.execute(query_yesterday_purchases, (yesterday, *profile_ids))
            
            for row in cursor.fetchall():
                profile_id, email, name, num_purchases = row
                active_accounts.append({
                    "id": profile_id,
                    "email": email,
                    "name": name,
                    "numPurchases": num_purchases,
                    "recentlyOrdered": num_purchases > 0,
                    "hasCartItems": False  # Assuming no cart items for yesterday's purchases
                })

        cursor.close()
        connection.close()

        return active_accounts

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/probability")
@sleep_and_retry
@limits(calls=2, period=60) 
def get_activity_probability( current: Optional[bool] = None, ):
# def get_activity_probability(api_key: str = Depends(api_key_header)):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")


    current = current or False

    calculate_activity_probability()

    current_date = datetime.now().date()
    current_day_of_week = current_date.strftime("%A")

    current_day_data = {
        "actual_day": current_day_of_week,
        "actual_probability": 0,
        "expected_probability": 0,
        "actual_busy_hours": {hour: 0 for hour in activity_data[current_day_of_week]["busy_hours"]}
    }

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT COUNT(*) AS order_count, HOUR(updatedAt) AS hour_of_day
            FROM ylift_api.carts
            WHERE DATE(updatedAt) = %s
            GROUP BY HOUR(updatedAt)
        """
        cursor.execute(query, (current_date,))

        total_orders = 0
        for row in cursor.fetchall():
            order_count = row[0]
            hour_of_day = row[1]
            hour_label = f"{hour_of_day:02d}:00 - {hour_of_day+1:02d}:00"
            current_day_data["actual_busy_hours"][hour_label] = order_count
            total_orders += order_count

        cursor.close()
        connection.close()

        current_day_data["actual_probability"] = round(total_orders / 24, 4)

        if current_day_of_week in activity_data:
            current_day_data["expected_probability"] = activity_data[current_day_of_week]["probability"]

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")

    if current :
        return current_day_data

    return activity_data


@app.get("/activity")
@sleep_and_retry
@limits(calls=10, period=30)
def get_store_activity():
    # def get_store_activity(api_key: str = Depends(api_key_header)):
    #     if api_key != API_KEY:
    #         raise HTTPException(status_code=400, detail="Invalid API key")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        current_date = datetime.utcnow().date()
        one_hour_ago_utc = datetime.utcnow() - timedelta(hours=1)

        using_carts_activity_date = False

        # get the latest updatedAt from carts
        query_carts = """
            SELECT MAX(updatedAt) AS last_active_cart
            FROM ylift_api.carts
        """
        cursor.execute(query_carts)
        last_active_cart_utc = cursor.fetchone()[0]

        # get the latest updatedAt from cartItems for the current date
        query_cart_items = """
            SELECT MAX(ci.updatedAt) AS last_active_item
            FROM ylift_api.cartItems ci
            JOIN ylift_api.carts c ON ci.cartId = c.id
            WHERE DATE(ci.updatedAt) = %s
        """
        cursor.execute(query_cart_items, (current_date,))
        last_active_item_utc = cursor.fetchone()[0]

        # Determine the most recent activity
        last_active_utc = max(last_active_cart_utc, last_active_item_utc) if last_active_item_utc else last_active_cart_utc

        # count active orders in the last hour
        query_active_orders = """
            SELECT COUNT(DISTINCT c.id) AS active_orders
            FROM ylift_api.carts c
            LEFT JOIN ylift_api.cartItems ci ON c.id = ci.cartId
            WHERE GREATEST(c.updatedAt, COALESCE(ci.updatedAt, '1970-01-01')) >= %s
        """
        cursor.execute(query_active_orders, (one_hour_ago_utc,))
        active_orders = cursor.fetchone()[0]

        cursor.close()
        connection.close()

        elapsed_idle = "00:00:00"
        active_idle = "00:00:00"
        is_active = False

        if active_orders > 0:
            active_idle = str(datetime.utcnow() - last_active_utc)
            is_active = True
        else:
            elapsed_idle = str(datetime.utcnow() - last_active_utc)
            # Check if the last activity was from cartItems and if it's been more than 20 mins
            if last_active_utc == last_active_item_utc and (datetime.utcnow() - last_active_utc) > timedelta(minutes=20):
                is_active = False
            else:
                is_active = (datetime.utcnow() - last_active_utc) <= timedelta(hours=1)

        # Convert last_active from UTC to New York timezone
        ny_tz = timezone('America/New_York')
        last_active_ny = utc.localize(last_active_utc).astimezone(ny_tz)

        store_activity_data = {
            "last_active": last_active_ny.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_idle": elapsed_idle,
            "active_idle": active_idle,
            "is_active": is_active
        }

        return store_activity_data

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/backup")
@sleep_and_retry
@limits(calls=2, period=3600)
def backup_database():
    global last_backup_time

    current_time = datetime.now()

    if last_backup_time is None or (current_time - last_backup_time) >= timedelta(hours=2):
        perform_backup_sync()
        return {"message": "Backup process started"}
    else:
        return {"message": "Backup skipped. Already performed within the last 2 hours."}



@app.get("/sales")
@sleep_and_retry
@limits(calls=2, period=60)  # Rate limit: 2 requests per minute
def get_sales( prior: Optional[bool] = None, month: Optional[bool] = None, lastmonth: Optional[bool] = None, quarter: Optional[bool] = None, priorquarter: Optional[bool] = None, year: Optional[bool] = None, prioryear: Optional[bool] = None):
# def get_sales(api_key: str = Depends(api_key_header), prior: Optional[bool] = None, month: Optional[bool] = None, lastmonth: Optional[bool] = None, quarter: Optional[bool] = None, priorquarter: Optional[bool] = None, year: Optional[bool] = None, prioryear: Optional[bool] = None):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

    # Set default values
    prior = prior or False
    month = month or False
    lastmonth = lastmonth or False
    quarter = quarter or False
    priorquarter = priorquarter or False
    year = year or False
    prioryear = prioryear or False

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        current_date = datetime.now().date()
        start_date = None
        end_date = None

        if prior:
            end_date = current_date - timedelta(days=current_date.weekday() + 1)
            start_date = end_date - timedelta(days=6)
        elif month:
            start_date = current_date.replace(day=1)
            end_date = current_date
        elif lastmonth:
            last_month = current_date.replace(day=1) - timedelta(days=1)
            start_date = last_month.replace(day=1)
            end_date = last_month
        elif quarter:
            current_quarter = (current_date.month - 1) // 3 + 1
            start_month = (current_quarter - 1) * 3 + 1
            end_month = start_month + 2
            start_date = current_date.replace(month=start_month, day=1)
            end_date = current_date.replace(month=end_month, day=calendar.monthrange(current_date.year, end_month)[1])
        elif priorquarter:
            current_quarter = (current_date.month - 1) // 3 + 1
            prior_year = current_date.year - 1
            start_month = (current_quarter - 1) * 3 + 1
            end_month = start_month + 2
            start_date = current_date.replace(year=prior_year, month=start_month, day=1)
            end_date = current_date.replace(year=prior_year, month=end_month, day=calendar.monthrange(prior_year, end_month)[1])
        elif year:
            start_date = current_date.replace(month=1, day=1)
            end_date = current_date.replace(month=12, day=31)
        elif prioryear:
            prior_year = current_date.year - 1
            start_date = current_date.replace(year=prior_year, month=1, day=1)
            end_date = current_date.replace(year=prior_year, month=12, day=31)
        else:
            start_date = current_date - timedelta(days=current_date.weekday())
            end_date = start_date + timedelta(days=6)


        query = """
            SELECT COALESCE(SUM(amount), 0) AS total_sales
            FROM ylift_api.orders
            WHERE status = 'COMPLETED'
                AND DATE(completedAt) BETWEEN %s AND %s
        """
        cursor.execute(query, (start_date, end_date))

        total_sales_pennies = cursor.fetchone()[0]
        total_sales_dollars = total_sales_pennies / 100

        cursor.close()
        connection.close()

        sales_data = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "totalSales": "${:,.2f}".format(total_sales_dollars)
        }

        return sales_data

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")



def perform_backup_sync():
    global last_backup_time

    current_dir = os.getcwd()
    os.chdir(BACK_UP_LOC)

    # Get the current date and format it as "Monday"
    now = datetime.now()
    date_str = now.strftime('%b%d_%-I%p')

    # Get the current year
    year = now.strftime('%Y')

    # Create the output directory with the year if it doesn't exist
    output_dir = f'{year}/{date_str}/'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f'\tBackup directory created: {output_dir}')

        # Create a login path file with the username and password
        with open('mysql_login.cnf', 'w') as f:
            f.write(f'[client]\nuser={DB_CONFIG["user"]}\npassword={DB_CONFIG["password"]}\n')

        # Get a list of all tables in the database
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('SHOW TABLES')
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        connection.close()

        # Loop through each table and perform a mysqldump
        for table in tables:
            dump_file = output_dir + table + '.sql'
            dump_cmd = f'/usr/local/bin/mysqldump --defaults-file="mysql_login.cnf" -h {DB_CONFIG["host"]} -P {DB_CONFIG["port"]} --skip-column-statistics --no-tablespaces --routines --events --triggers {DB_CONFIG["database"]} {table} > {dump_file}'
            # print(dump_cmd)
            os.system(dump_cmd)

        print(f'\tBackup completed at {now}')

        # Remove the login path file
        os.remove('mysql_login.cnf')

        last_backup_time = now
    else:
        print(f'\tBackup already exists for {date_str}. Skipping backup.')

    os.chdir(current_dir)



def automated_backup():
    global last_backup_time
    while True:
        current_time = datetime.now()
        if last_backup_time is None or (current_time - last_backup_time) >= timedelta(hours=2):
            print("\tAutomated backup triggered")
            perform_backup_sync()
        time.sleep(600)  # sleep 10 mins 

# automated backups..
backup_thread = threading.Thread(target=automated_backup, daemon=True)
backup_thread.start()