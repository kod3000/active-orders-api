import unittest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from datetime import datetime
from active_orders_api import app, get_transactions_today, parse_xml

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


if __name__ == '__main__':
    unittest.main()
