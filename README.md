# Active Orders


This FastAPI app reads MySQL data from the `ylift_api` database and provides endpoints to retrieve active order information on the current day.

The endpoints usaually have a rate limit of 2 requests per minute and requires an API key for authentication.



## Prerequisites

Before running the app, make sure you have the following:

- Python 3.7 or higher
- MySQL database with the `ylift_api.carts` table
- FastAPI and its dependencies


## Installation

1. Clone the repository:
```
  git clone https://github.com/kod3000/active-orders.git
```
2. Navigate to the project directory:
```
  cd active-orders
````
3. Create a virtual environment (optional but recommended):
```
  python -m venv venv
  source venv/bin/activate
```
5. Install the required dependencies:
```
  pip3 install fastapi uvicorn mysql-connector-python ratelimit pydantic
```

## Configuration

1. Create a `config.py` file in the project directory. (or copy and rename sample_config.py)

    ```
    touch config.py
    ```
    or 
    ```
    cp sample_config.py config.py
    ```

2. Open the `config.py` file and add the following configuration:

    ```python
    DB_CONFIG = {
        "host": "your_host",
        "user": "your_username",
        "password": "your_password",
        "database": "your_database"
    }

    API_KEY = "your_api_key"
    ```

## Usage
1. Start the FastAPI server:
   uvicorn main:app --reload
2. Access the API endpoints
  - Open your web browser or use an API testing tool like cURL or Postman.
  - Make a GET request to http://localhost:8000/active_carts.
  - Include the X-API-Key header with your API key.

    ie : 
    ```
      curl -X GET -H "X-API-Key: your_api_key" http://localhost:8000/active_carts
    ```
3. The API will return a JSON response containing the active carts modified on the current day, including the profileId, createdAt, and updatedAt fields.

## Contributing
Contributions are welcome! If you find any issues or have suggestions for improvements, please open an issue or submit a pull request.


## License

This project is licensed under the MIT License.


