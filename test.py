import unittest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from datetime import datetime, timedelta
import mysql.connector

from active_orders_api import app, get_transactions_today, parse_xml, get_active_accounts, get_activity_probability, get_active_carts, ActiveCart
from config import API_KEY

class TestParseXML(unittest.TestCase):

    def test_simple_xml(self):
        xml_string = '''<getTransactionListForCustomerRequest xmlns="AnetApi/xml/v1/schema/AnetApiSchema.xsd">
<name>John Doe</name>
<age>30</age>
</getTransactionListForCustomerRequest>
'''
        expected_result = {'name': 'John Doe', 'age': '30'}
        self.assertEqual(parse_xml(xml_string), expected_result)

    def test_nested_xml(self):
        xml_string = '''<getTransactionListForCustomerRequest xmlns="AnetApi/xml/v1/schema/AnetApiSchema.xsd">
<person>
<name>John Doe</name>
<age>30</age>
</person>
</getTransactionListForCustomerRequest>
'''
        expected_result = {'person': {'name': 'John Doe', 'age': '30'}}
        self.assertEqual(parse_xml(xml_string), expected_result)

    def test_repeated_tags(self):
        xml_string = '''<getTransactionListForCustomerRequest xmlns="AnetApi/xml/v1/schema/AnetApiSchema.xsd">
<name>John Doe</name>
<name>Jane Doe</name>
</getTransactionListForCustomerRequest>
'''
        expected_result = {'name': ['John Doe', 'Jane Doe']}
        self.assertEqual(parse_xml(xml_string), expected_result)

    def test_empty_xml(self):
        xml_string = '''<getTransactionListForCustomerRequest xmlns="AnetApi/xml/v1/schema/AnetApiSchema.xsd">
</getTransactionListForCustomerRequest>
'''
        expected_result = {}
        self.assertEqual(parse_xml(xml_string), expected_result)

    def test_malformed_xml(self):
        xml_string = '''<getTransactionListForCustomerRequest xmlns="AnetApi/xml/v1/schema/AnetApiSchema.xsd">
<name>John Doe</name>
<start>
<age>30</age>
</getTransactionListForCustomerRequest>
'''
        with self.assertRaises(ValueError):
            parse_xml(xml_string)


class TestGetTransactionsToday(unittest.TestCase):

    @patch('active_orders_api.apicontractsv1.merchantAuthenticationType')
    @patch('active_orders_api.apicontractsv1.getTransactionListForCustomerRequest')
    @patch('active_orders_api.getTransactionListForCustomerController')
    @patch('active_orders_api.etree.tostring')
    @patch('active_orders_api.parse_xml')
    def test_get_transactions_today_success(self, mock_parse_xml, mock_etree_tostring, mock_controller, mock_request, mock_merchant_auth):
        # Mock the merchant authentication
        mock_merchant_auth_instance = mock_merchant_auth.return_value
        mock_merchant_auth_instance.name = "mock_api_id"
        mock_merchant_auth_instance.transactionKey = "mock_transaction_key"

        # Mock the request
        mock_request_instance = mock_request.return_value
        mock_request_instance.merchantAuthentication = mock_merchant_auth_instance
        mock_request_instance.customerProfileId = "12345"

        # Mock the controller
        mock_controller_instance = mock_controller.return_value
        mock_controller_instance.getresponse.return_value = MagicMock()
        mock_controller_instance.getresponse.return_value.messages.resultCode = "Ok"

        # Mock the XML response
        mock_etree_tostring.return_value = b'<transactions><transaction><submitTimeUTC>2024-07-03T12:00:00.000Z</submitTimeUTC></transaction></transactions>'
        mock_parse_xml.return_value = {
            'transactions': {
                'transaction': [
                    {'submitTimeUTC': '2024-07-03T12:00:00.000Z'}
                ]
            }
        }

        # Call the function
        response = get_transactions_today("12345")

        # Assert the result
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]['submitTimeUTC'], '2024-07-03T12:00:00.000Z')

    @patch('active_orders_api.apicontractsv1.merchantAuthenticationType')
    @patch('active_orders_api.apicontractsv1.getTransactionListForCustomerRequest')
    @patch('active_orders_api.getTransactionListForCustomerController')
    def test_get_transactions_today_error_fetching(self, mock_controller, mock_request, mock_merchant_auth):
        # Mock the merchant authentication
        mock_merchant_auth_instance = mock_merchant_auth.return_value

        # Mock the request
        mock_request_instance = mock_request.return_value

        # Mock the controller
        mock_controller_instance = mock_controller.return_value
        mock_controller_instance.getresponse.return_value = MagicMock()
        mock_controller_instance.getresponse.return_value.messages.resultCode = "Error"

        # Call the function and assert it raises an HTTPException
        with self.assertRaises(HTTPException) as context:
            get_transactions_today("12345")

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "500: Error fetching transactions")

    @patch('active_orders_api.apicontractsv1.merchantAuthenticationType')
    @patch('active_orders_api.apicontractsv1.getTransactionListForCustomerRequest')
    @patch('active_orders_api.getTransactionListForCustomerController')
    def test_get_transactions_today_exception(self, mock_controller, mock_request, mock_merchant_auth):
        # Mock the merchant authentication
        mock_merchant_auth_instance = mock_merchant_auth.return_value

        # Mock the request
        mock_request_instance = mock_request.return_value

        # Mock the controller to raise an exception
        mock_controller.side_effect = Exception("Some error")

        # Call the function and assert it raises an HTTPException
        with self.assertRaises(HTTPException) as context:
            get_transactions_today("12345")

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "Some error")


class TestGetActiveAccounts(unittest.TestCase):

    @patch('active_orders_api.get_db_connection')
    def test_invalid_api_key(self, mock_get_db_connection):
        from fastapi import HTTPException
        
        mock_get_db_connection.return_value = MagicMock()
        
        with self.assertRaises(HTTPException) as context:
            get_active_accounts(api_key="invalid_key")
        
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, "Invalid API key")
    
    @patch('active_orders_api.get_db_connection')
    def test_db_connection_error(self, mock_get_db_connection):
        from fastapi import HTTPException
        import mysql.connector
        
        mock_get_db_connection.side_effect = mysql.connector.Error("DB Connection Error")
        
        with self.assertRaises(HTTPException) as context:
            get_active_accounts(api_key=API_KEY)
        
        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "Internal server error")
    
    @patch('active_orders_api.get_db_connection')
    def test_get_active_accounts(self, mock_get_db_connection):
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        
        # Mock datetime
        current_date = datetime(2023, 7, 1).date()
        yesterday = current_date - timedelta(days=1)
        
        # Mock data for cartItems, carts, profiles, orders
        cart_ids_from_items = [(1,), (2,)]
        profile_ids_from_carts = [(1,), (3,)]
        profile_ids_from_items = [(2,), (3,)]
        
        profile_data = {
            1: ("user1@example.com", "User One", "cust1"),
            2: ("user2@example.com", "User Two", "cust2"),
            3: ("user3@example.com", "User Three", "cust3")
        }
        
        order_data = {
            1: (1, 0),
            2: (0, 1),
            3: (2, 0)
        }
        
        cart_item_data = {
            1: (2,),
            2: (1,),
            3: (0,)
        }
        
        yesterday_order_data = [
            (4, "user4@example.com", "User Four", "cust4", 1)
        ]
        
        # Mock cursor execute and fetchall behavior
        def mock_execute(query, params=None):
            if "FROM ylift_api.cartItems" in query:
                mock_cursor.fetchall.return_value = cart_ids_from_items
            elif "FROM ylift_api.carts" in query and "cartItems" not in query:
                mock_cursor.fetchall.return_value = profile_ids_from_carts
            elif "FROM ylift_api.carts" in query and "cartItems" in query:
                mock_cursor.fetchall.return_value = profile_ids_from_items
            elif "FROM ylift_api.profiles" in query:
                profile_id = params[0]
                mock_cursor.fetchone.return_value = profile_data.get(profile_id)
            elif "FROM ylift_api.orders" in query and "JOIN ylift_api.profiles" not in query:
                profile_id = params[0]
                mock_cursor.fetchone.return_value = order_data.get(profile_id)
            elif "FROM ylift_api.cartItems ci" in query:
                profile_id = params[0]
                mock_cursor.fetchone.return_value = cart_item_data.get(profile_id)
            elif "FROM ylift_api.orders o" in query:
                mock_cursor.fetchall.return_value = yesterday_order_data
        
        mock_cursor.execute.side_effect = mock_execute
        
        result = get_active_accounts(api_key=API_KEY)
        
        expected_result = [
            {
                "id": 1,
                "email": "user1@example.com",
                "name": "User One",
                "customerId": "cust1",
                "numPurchases": 1,
                "recentlyOrdered": True,
                "hasCartItems": True
            },
            {
                "id": 3,
                "email": "user3@example.com",
                "name": "User Three",
                "customerId": "cust3",
                "numPurchases": 2,
                "recentlyOrdered": True,
                "hasCartItems": True
            },
            {
                "id": 4,
                "email": "user4@example.com",
                "name": "User Four",
                "customerId": "cust4",
                "numPurchases": 1,
                "recentlyOrdered": True,
                "hasCartItems": False
            }
        ]
        
        self.assertEqual(result, expected_result)


# Mock activity data used in the function
activity_data = {
    "Monday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Tuesday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Wednesday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Thursday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Friday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Saturday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
    "Sunday": {
        "probability": 0.1,
        "busy_hours": [f"{hour:02d}:00 - {hour+1:02d}:00" for hour in range(24)]
    },
}


class TestGetActivityProbability(unittest.TestCase):

    @patch('active_orders_api.get_db_connection')
    @patch('active_orders_api.calculate_activity_probability')
    @patch('active_orders_api.activity_data', activity_data)
    def test_invalid_api_key(self, mock_calculate_activity_probability, mock_get_db_connection):
        from fastapi import HTTPException
        
        with self.assertRaises(HTTPException) as context:
            get_activity_probability(api_key="invalid_key")
        
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, "Invalid API key")
    
    @patch('active_orders_api.get_db_connection')
    @patch('active_orders_api.calculate_activity_probability')
    @patch('active_orders_api.activity_data', activity_data)
    def test_db_connection_error(self, mock_calculate_activity_probability, mock_get_db_connection):
        from fastapi import HTTPException
        import mysql.connector
        
        mock_get_db_connection.side_effect = mysql.connector.Error("DB Connection Error")
        
        with self.assertRaises(HTTPException) as context:
            get_activity_probability(api_key=API_KEY)
        
        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "Internal server error")

    @patch('active_orders_api.get_db_connection')
    @patch('active_orders_api.calculate_activity_probability')
    @patch('active_orders_api.perform_backup_sync')
    @patch('active_orders_api.activity_data', activity_data)
    def test_get_activity_probability(self, mock_perform_backup_sync, mock_calculate_activity_probability, mock_get_db_connection):
        # Mocking database connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        
        # Mock datetime
        current_date = datetime(2023, 7, 1).date()
        current_day_of_week = current_date.strftime("%A")
        
        # Mock data for cart orders
        orders_data = [
            (5, 0), (10, 1), (15, 2), (20, 3), (25, 4), (30, 5), (35, 6), 
            (40, 7), (45, 8), (50, 9), (55, 10), (60, 11), (65, 12), (70, 13), 
            (75, 14), (80, 15), (85, 16), (90, 17), (95, 18), (100, 19), 
            (105, 20), (110, 21), (115, 22), (120, 23)
        ]
        
        # Mock cursor execute and fetchall behavior
        def mock_execute(query, params=None):
            if "FROM ylift_api.carts" in query:
                mock_cursor.fetchall.return_value = orders_data
        
        mock_cursor.execute.side_effect = mock_execute


class TestGetActiveCarts(unittest.TestCase):

    @patch('active_orders_api.get_db_connection')
    def test_get_active_carts(self, mock_get_db_connection):
        # Mocking database connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        
        # Mock datetime
        current_date = datetime(2023, 7, 1).date()
        
        # Mock data for carts
        carts_data = [
            (1, datetime(2023, 7, 1, 10, 30, 0), datetime(2023, 7, 1, 15, 45, 0)),
            (2, datetime(2023, 7, 1, 12, 0, 0), datetime(2023, 7, 1, 16, 0, 0)),
            (3, datetime(2023, 7, 1, 14, 15, 0), datetime(2023, 7, 1, 17, 30, 0))
        ]
        
        # Mock cursor execute and fetchall behavior
        def mock_execute(query, params=None):
            if "FROM ylift_api.carts" in query:
                mock_cursor.fetchall.return_value = carts_data
        
        mock_cursor.execute.side_effect = mock_execute
        
        result = get_active_carts(api_key=API_KEY)
        
        expected_active_carts = [
            ActiveCart(profileId=1, createdAt=datetime(2023, 7, 1, 10, 30, 0), updatedAt=datetime(2023, 7, 1, 15, 45, 0)),
            ActiveCart(profileId=2, createdAt=datetime(2023, 7, 1, 12, 0, 0), updatedAt=datetime(2023, 7, 1, 16, 0, 0)),
            ActiveCart(profileId=3, createdAt=datetime(2023, 7, 1, 14, 15, 0), updatedAt=datetime(2023, 7, 1, 17, 30, 0))
        ]
        
        self.assertEqual(result, expected_active_carts)


if __name__ == '__main__':
    unittest.main()
