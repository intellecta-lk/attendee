from unittest.mock import patch

from django.test import TestCase

from accounts.models import Organization
from bots.models import Project, ZoomOAuthApp, ZoomOAuthConnection, ZoomOAuthConnectionStates
from bots.zoom_oauth_connections_api_utils import create_zoom_oauth_connection


class TestCreateZoomOAuthConnection(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Organization")
        self.project = Project.objects.create(name="Test Project", organization=self.organization)

        # Create a ZoomOAuthApp for testing
        self.zoom_oauth_app = ZoomOAuthApp.objects.create(
            project=self.project,
            client_id="test_client_id_123",
        )
        # Set credentials including client_secret
        self.zoom_oauth_app.set_credentials({"client_secret": "test_client_secret_456", "webhook_secret": "test_webhook_secret"})

    @patch("bots.zoom_oauth_connections_api_utils._get_user_info")
    @patch("bots.zoom_oauth_connections_api_utils._exchange_access_code_for_tokens")
    def test_create_zoom_oauth_connection_success(self, mock_exchange_tokens, mock_get_user_info):
        """Test successful zoom oauth connection creation with all valid data."""
        # Mock the external API calls
        mock_exchange_tokens.return_value = {
            "access_token": "test_access_token",
            "refresh_token": "test_refresh_token_789",
            "expires_in": 3600,
            "scope": "user:read:user user:read:zak meeting:read:list_meetings meeting:read:local_recording_token",
        }
        mock_get_user_info.return_value = {
            "id": "test_user_id_123",
            "account_id": "test_account_id_456",
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "status": "active",
        }

        connection_data = {
            "zoom_oauth_app_id": self.zoom_oauth_app.object_id,
            "authorization_code": "test_authorization_code",
            "redirect_uri": "https://example.com/oauth/callback",
            "metadata": {"department": "engineering", "team": "backend"},
        }

        zoom_oauth_connection, error = create_zoom_oauth_connection(connection_data, self.project)

        # Verify successful creation
        self.assertIsNotNone(zoom_oauth_connection)
        self.assertIsNone(error)

        # Verify zoom oauth connection properties
        self.assertEqual(zoom_oauth_connection.zoom_oauth_app, self.zoom_oauth_app)
        self.assertEqual(zoom_oauth_connection.user_id, "test_user_id_123")
        self.assertEqual(zoom_oauth_connection.account_id, "test_account_id_456")
        self.assertEqual(zoom_oauth_connection.state, ZoomOAuthConnectionStates.CONNECTED)
        self.assertEqual(zoom_oauth_connection.metadata, {"department": "engineering", "team": "backend"})

        # Verify credentials are encrypted and stored
        credentials = zoom_oauth_connection.get_credentials()
        self.assertIsNotNone(credentials)
        self.assertEqual(credentials["refresh_token"], "test_refresh_token_789")

        # Verify object_id is generated
        self.assertIsNotNone(zoom_oauth_connection.object_id)
        self.assertTrue(zoom_oauth_connection.object_id.startswith("zoc_"))

        # Verify the external API calls were made with correct parameters
        mock_exchange_tokens.assert_called_once_with(
            code="test_authorization_code",
            redirect_uri="https://example.com/oauth/callback",
            client_id="test_client_id_123",
            client_secret="test_client_secret_456",
        )
        mock_get_user_info.assert_called_once_with("test_access_token")

    @patch("bots.zoom_oauth_connections_api_utils._exchange_access_code_for_tokens")
    def test_create_zoom_oauth_connection_invalid_access_code(self, mock_exchange_tokens):
        """Test zoom oauth connection creation fails with invalid access code."""
        # Mock the token exchange to raise an exception (simulating invalid code)
        mock_exchange_tokens.side_effect = Exception("Invalid authorization code")

        connection_data = {
            "zoom_oauth_app_id": self.zoom_oauth_app.object_id,
            "authorization_code": "invalid_authorization_code",
            "redirect_uri": "https://example.com/oauth/callback",
        }

        zoom_oauth_connection, error = create_zoom_oauth_connection(connection_data, self.project)

        # Verify creation failed
        self.assertIsNone(zoom_oauth_connection)
        self.assertIsNotNone(error)
        self.assertIn("error", error)
        self.assertIn("Error exchanging access code for tokens", error["error"])

        # Verify no connection was created in the database
        self.assertEqual(ZoomOAuthConnection.objects.count(), 0)
