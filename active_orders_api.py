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
@limits(calls=2, period=60) 
def get_active_accounts():
# def get_active_accounts(api_key: str = Depends(api_key_header)):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT profileId, MAX(updatedAt) AS updatedAt
            FROM ylift_api.carts
            WHERE DATE(updatedAt) = CURDATE()
            GROUP BY profileId
            ORDER BY MAX(updatedAt) DESC
        """
        cursor.execute(query)

        profile_ids = [row[0] for row in cursor.fetchall()]

        if len(profile_ids) < 8:
            query = """
                SELECT profileId, MAX(updatedAt) AS updatedAt
                FROM ylift_api.carts
                WHERE DATE(updatedAt) < CURDATE()
                    AND profileId NOT IN (%s)
                GROUP BY profileId
                ORDER BY MAX(updatedAt) DESC
                LIMIT %s
            """
            placeholders = ','.join(['%s'] * len(profile_ids))
            limit = 8 - len(profile_ids)
            cursor.execute(query % (placeholders, limit), tuple(profile_ids))
            profile_ids.extend([row[0] for row in cursor.fetchall()])

        active_accounts = []

        for profile_id in profile_ids:
            query = """
                SELECT email, name
                FROM ylift_api.profiles
                WHERE id = %s
            """
            cursor.execute(query, (profile_id,))
            result = cursor.fetchone()

            if result:
                email, name = result
                recently_ordered = False

                query = """
                    SELECT COUNT(*)
                    FROM ylift_api.orders
                    WHERE profileId = %s
                        AND DATE_FORMAT(createdAt, '%%Y-%%m-%%d %%H:%%i') = DATE_FORMAT((
                            SELECT updatedAt
                            FROM ylift_api.carts
                            WHERE profileId = %s
                            ORDER BY updatedAt DESC
                            LIMIT 1
                        ), '%%Y-%%m-%%d %%H:%%i')
                """
                cursor.execute(query, (profile_id, profile_id))
                order_count = cursor.fetchone()[0]

                if order_count > 0:
                    recently_ordered = True

                active_accounts.append({
                    "profileId": profile_id,
                    "email": email,
                    "name": name,
                    "recentlyOrdered": recently_ordered
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
def get_activity_probability():
# def get_activity_probability(api_key: str = Depends(api_key_header)):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

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

    activity_data["Current"] = current_day_data

    return activity_data


@app.get("/activity")
@sleep_and_retry
@limits(calls=10, period=30) 
def get_store_activity():
# def get_store_activity(api_key: str = Depends(api_key_header)):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT MAX(updatedAt) AS last_active
            FROM ylift_api.carts
        """
        cursor.execute(query)

        last_active = cursor.fetchone()[0]

        query = """
            SELECT COUNT(*) AS active_orders
            FROM ylift_api.carts
            WHERE updatedAt >= %s
        """
        one_hour_ago = datetime.now() - timedelta(hours=1)
        cursor.execute(query, (one_hour_ago,))

        active_orders = cursor.fetchone()[0]

        cursor.close()
        connection.close()

        elapsed_idle = "00:00:00"
        active_idle = "00:00:00"
        is_active = False

        if active_orders > 0:
            active_idle = str(datetime.now() - one_hour_ago)
            is_active = True
        else:
            elapsed_idle = str(datetime.now() - last_active)

        store_activity_data = {
            "last_active": last_active.strftime("%Y-%m-%d %H:%M:%S"),
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
@limits(calls=2, period=60)
def get_sales(prior: bool = False, month: bool = False, lastmonth: bool = False, quarter: bool = False, priorquarter: bool = False):
# def get_sales(api_key: str = Depends(api_key_header), prior: bool = False, month: bool = False, lastmonth: bool = False, quarter: bool = False, priorquarter: bool = False):
    # if api_key != API_KEY:
    #     raise HTTPException(status_code=400, detail="Invalid API key")

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
            prior_quarter = current_quarter - 1
            start_month = (prior_quarter - 1) * 3 + 1
            end_month = start_month + 2
            start_date = current_date.replace(month=start_month, day=1)
            end_date = current_date.replace(month=end_month, day=calendar.monthrange(current_date.year, end_month)[1])
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

        total_sales = cursor.fetchone()[0]

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
        print(f'Backup directory created: {output_dir}')

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
            print(dump_cmd)
            os.system(dump_cmd)

        print(f'Backup completed at {now}')

        # Remove the login path file
        os.remove('mysql_login.cnf')

        last_backup_time = now
    else:
        print(f'Backup already exists for {date_str}. Skipping backup.')

    os.chdir(current_dir)