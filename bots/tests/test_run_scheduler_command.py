import signal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone as django_timezone

from accounts.models import Organization
from bots.management.commands.run_scheduler import Command
from bots.models import Bot, BotStates, Project


class RunSchedulerCommandTestCase(TestCase):
    def setUp(self):
        """Set up test data"""
        self.organization = Organization.objects.create(
            name="Test Organization",
            centicredits=10000,  # 100 credits
        )
        self.project = Project.objects.create(name="Test Project", organization=self.organization)

        # Create test times
        self.now = django_timezone.now().replace(microsecond=0, second=0)
        self.join_at_within_threshold = self.now + django_timezone.timedelta(minutes=3)
        self.join_at_too_early = self.now + django_timezone.timedelta(minutes=7)  # Outside threshold
        self.join_at_too_late = self.now - django_timezone.timedelta(minutes=7)  # Outside threshold

    def test_run_scheduled_bots_launches_eligible_bots(self):
        """Test that _run_scheduled_bots finds and launches bots within the time threshold"""
        # Create bots with different states and times
        eligible_bot = Bot.objects.create(project=self.project, name="Eligible Bot", meeting_url="https://example.zoom.us/j/123456789", state=BotStates.SCHEDULED, join_at=self.join_at_within_threshold)

        # Bot that's too early (outside threshold)
        Bot.objects.create(project=self.project, name="Too Early Bot", meeting_url="https://example.zoom.us/j/987654321", state=BotStates.SCHEDULED, join_at=self.join_at_too_early)

        # Bot that's not in SCHEDULED state
        Bot.objects.create(project=self.project, name="Wrong State Bot", meeting_url="https://example.zoom.us/j/111222333", state=BotStates.READY, join_at=self.join_at_within_threshold)

        command = Command()

        with patch("bots.tasks.launch_scheduled_bot_task.launch_scheduled_bot.delay") as mock_delay:
            with patch("django.utils.timezone.now", return_value=self.now):
                command._run_scheduled_bots()

            # Verify only the eligible bot was launched
            mock_delay.assert_called_once_with(eligible_bot.id, self.join_at_within_threshold.isoformat())

    def test_graceful_shutdown_signal_handling(self):
        """Test that the signal handler properly sets the shutdown flag"""
        command = Command()

        # Verify initial state
        self.assertTrue(command._keep_running)

        # Simulate receiving SIGTERM
        command._graceful_exit(signal.SIGTERM, None)

        # Verify the shutdown flag was set
        self.assertFalse(command._keep_running)

    def test_run_scheduled_bots_ignores_bots_outside_time_threshold(self):
        """Test that bots outside the 5-minute time window are ignored"""
        # Create a bot that's too late (missed by more than 5 minutes)
        Bot.objects.create(project=self.project, name="Too Late Bot", meeting_url="https://example.zoom.us/j/444555666", state=BotStates.SCHEDULED, join_at=self.join_at_too_late)

        # Create a bot that's too early (more than 5 minutes in the future)
        Bot.objects.create(project=self.project, name="Too Early Bot", meeting_url="https://example.zoom.us/j/777888999", state=BotStates.SCHEDULED, join_at=self.join_at_too_early)

        command = Command()

        with patch("bots.tasks.launch_scheduled_bot_task.launch_scheduled_bot.delay") as mock_delay:
            with patch("django.utils.timezone.now", return_value=self.now):
                command._run_scheduled_bots()

            # Verify no bots were launched since they're all outside the time threshold
            mock_delay.assert_not_called()
