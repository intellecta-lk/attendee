from django.urls import path

from . import bot_sso_views

app_name = "bot_sso"

urlpatterns = [
    path(
        "google_meet_sign_in",
        bot_sso_views.GoogleMeetSignInView.as_view(),
        name="google_meet_sign_in",
    ),
    path(
        "google_meet_create_session",
        bot_sso_views.GoogleMeetCreateSessionView.as_view(),
        name="google_meet_create_session",
    ),
]
