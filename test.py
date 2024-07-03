import unittest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from datetime import datetime
import mysql.connector

from active_orders_api import app, get_transactions_today, parse_xml
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
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/accounts", headers={"api_key": "invalid_key"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Not authenticated"})

    @patch('active_orders_api.get_db_connection')
    def test_db_connection_error(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        mock_get_db_connection.side_effect = mysql.connector.Error("DB Connection Error")

        client = TestClient(app)
        response = client.get("/accounts", headers={"api_key": API_KEY})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error"})

    @patch('active_orders_api.get_db_connection')
    def test_get_active_accounts_success(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        # Mock the database connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # Mock current date
        current_date = datetime.now().date()

        # Mock database queries
        mock_cursor.fetchall.side_effect = [
            [(1,)],  # cart_ids_from_items
            [(2,)],  # profile_ids_from_carts
            [(3,)],  # profile_ids_from_items
            [(4,)],  # query_profiles_from_items
            [(5,)],  # query_profile
            [(6,)],  # query_orders
            [(7,)],  # query_cart_items
            [(8,)]   # query_yesterday_purchases
        ]

        client = TestClient(app)
        response = client.get("/accounts", headers={"api_key": API_KEY})

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)


class TestGetActiveCarts(unittest.TestCase):

    @patch('active_orders_api.get_db_connection')
    def test_invalid_api_key(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/active_carts", headers={"api_key": "invalid_key"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not Found"})

    @patch('active_orders_api.get_db_connection')
    def test_db_connection_error(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        mock_get_db_connection.side_effect = mysql.connector.Error("DB Connection Error")

        client = TestClient(app)
        response = client.get("/active_carts", headers={"api_key": API_KEY})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error"})

    @patch('active_orders_api.get_db_connection')
    def test_get_active_carts_success(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        # Mock the database connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # Mock current date
        current_date = datetime.now().date()

        # Mock database queries
        mock_cursor.fetchall.return_value = [
            (1, datetime(2024, 7, 2, 12, 0), datetime(2024, 7, 3, 12, 0)),
            (2, datetime(2024, 7, 2, 13, 0), datetime(2024, 7, 3, 13, 0))
        ]

        client = TestClient(app)
        response = client.get("/active_carts", headers={"api_key": API_KEY})

        self.assertEqual(response.status_code, 200)
        active_carts = response.json()
        self.assertIsInstance(active_carts, list)
        self.assertEqual(len(active_carts), 2)
        self.assertEqual(active_carts[0]['profileId'], 1)
        self.assertEqual(active_carts[1]['profileId'], 2)


class TestGetActivityProbability(unittest.TestCase):

    @patch('active_orders_api.get_db_connection')
    def test_invalid_api_key(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/activity_probability", headers={"api_key": "invalid_key"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not Found"})

    @patch('active_orders_api.get_db_connection')
    def test_db_connection_error(self, mock_get_db_connection):
        from fastapi.testclient import TestClient

        mock_get_db_connection.side_effect = mysql.connector.Error("DB Connection Error")

        client = TestClient(app)
        response = client.get("/activity_probability", headers={"api_key": API_KEY})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error"})

    @patch('active_orders_api.get_db_connection')
    @patch('active_orders_api.calculate_activity_probability')
    def test_get_activity_probability_success(self, mock_calculate_activity_probability, mock_get_db_connection):
        from fastapi.testclient import TestClient

        # Mock the database connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_get_db_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # Mock current date
        current_date = datetime.now().date()
        current_day_of_week = current_date.strftime("%A")

        # Mock database queries
        mock_cursor.fetchall.return_value = [
            (5, 8),  # 5 orders at 08:00 - 09:00
            (3, 12), # 3 orders at 12:00 - 13:00
            (7, 17)  # 7 orders at 17:00 - 18:00
        ]

        client = TestClient(app)
        response = client.get("/activity_probability", headers={"api_key": API_KEY}, params={"current": "true"})

        self.assertEqual(response.status_code, 200)
        current_day_data = response.json()
        self.assertEqual(current_day_data['actual_day'], current_day_of_week)
        self.assertEqual(current_day_data['actual_probability'], round((5 + 3 + 7) / 24, 4))
        self.assertEqual(current_day_data['expected_probability'], activity_data[current_day_of_week]["probability"])
        self.assertEqual(current_day_data['actual_busy_hours']["08:00 - 09:00"], 5)
        self.assertEqual(current_day_data['actual_busy_hours']["12:00 - 13:00"], 3)
        self.assertEqual(current_day_data['actual_busy_hours']["17:00 - 18:00"], 7)


if __name__ == '__main__':
    unittest.main()
