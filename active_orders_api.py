import mysql.connector
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import APIKeyHeader
from datetime import datetime, timedelta
from ratelimit import limits, sleep_and_retry
from pydantic import BaseModel
from config import DB_CONFIG, API_KEY

app = FastAPI()

api_key_header = APIKeyHeader(name="X-API-Key")

class ActiveCart(BaseModel):
    profileId: int
    createdAt: datetime
    updatedAt: datetime

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

@app.get("/active_carts")
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