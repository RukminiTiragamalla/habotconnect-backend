from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from booking.models import BookingRequest, LSAProfile, Parent, Payment, Skill

# Create your tests here.
class BookingFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.parent = Parent.objects.create(
            full_name="Jane Doe", email="jane@example.com", phone="123456"
        )
        self.skill = Skill.objects.create(name="Dyslexia Support")
        self.lsa = LSAProfile.objects.create(
            full_name="John LSA", email="john.lsa@example.com", hourly_rate="25.00"
        )
        self.lsa.skills.add(self.skill)
        self.start = timezone.now() + timedelta(days=1)
        self.end = self.start + timedelta(hours=1)
    @patch("booking.views.initiate_payment")
    def test_create_booking_success(self, mock_gateway):
        mock_gateway.return_value = {"transaction_ref": "txn_test_1", "status": "pending"}

        url = reverse("booking-create")
        payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(BookingRequest.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(BookingRequest.objects.first().status, BookingRequest.Status.PENDING)


    @patch("booking.views.initiate_payment")
    def test_reject_overlapping_booking(self, mock_gateway):
        mock_gateway.return_value = {"transaction_ref": "txn_test_2", "status": "pending"}
        url = reverse("booking-create")
        first_payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
        }
        self.client.post(url, first_payload, format="json")
        overlap_start = self.start + timedelta(minutes=30)
        overlap_end = overlap_start + timedelta(hours=1)
        second_payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": overlap_start.isoformat(),
            "end_time": overlap_end.isoformat(),
        }
        response = self.client.post(url, second_payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BookingRequest.objects.count(), 1)


    @patch("booking.views.initiate_payment")
    def test_adjacent_booking_is_allowed(self, mock_gateway):
        mock_gateway.side_effect = [
            {"transaction_ref": "txn_test_3a", "status": "pending"},
            {"transaction_ref": "txn_test_3b", "status": "pending"},
        ]
        url = reverse("booking-create")

        first_payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
        }
        self.client.post(url, first_payload, format="json")

        adjacent_payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.end.isoformat(),
            "end_time": (self.end + timedelta(hours=1)).isoformat(),
        }
        response = self.client.post(url, adjacent_payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(BookingRequest.objects.count(), 2)


    @patch("booking.views.initiate_payment")
    def test_invalid_time_range_rejected(self, mock_gateway):
        url = reverse("booking-create")
        payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.end.isoformat(),
            "end_time": self.start.isoformat(),
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, 400)


    def test_lsa_search_query_count_stays_low(self):
        other_lsa = LSAProfile.objects.create(
            full_name="Other LSA", email="other.lsa@example.com", hourly_rate="30.00"
        )
        other_lsa.skills.add(self.skill)

        url = reverse("lsa-search")
        with self.assertNumQueries(2):
            response = self.client.get(url, {"skill": "Dyslexia Support"})

        self.assertEqual(response.status_code, 200)


    @patch("booking.views.initiate_payment")
    def test_webhook_confirms_booking_on_success(self, mock_gateway):
        mock_gateway.return_value = {"transaction_ref": "txn_webhook_success", "status": "pending"}

        create_url = reverse("booking-create")
        payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
        }
        self.client.post(create_url, payload, format="json")
        payment = Payment.objects.get(transaction_ref="txn_webhook_success")

        webhook_url = reverse("payment-webhook")
        response = self.client.post(
            webhook_url,
            {
                "transaction_ref": "txn_webhook_success",
                "event": "payment.success",
                "secret": "dev-webhook-secret",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "success")
        self.assertEqual(payment.booking.status, BookingRequest.Status.CONFIRMED)



    @patch("booking.views.initiate_payment")
    def test_webhook_fails_booking_on_failure(self, mock_gateway):
        mock_gateway.return_value = {"transaction_ref": "txn_webhook_fail", "status": "pending"}

        create_url = reverse("booking-create")
        payload = {
            "parent": self.parent.id,
            "lsa": self.lsa.id,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
        }
        self.client.post(create_url, payload, format="json")
        payment = Payment.objects.get(transaction_ref="txn_webhook_fail")

        webhook_url = reverse("payment-webhook")
        response = self.client.post(
            webhook_url,
            {
                "transaction_ref": "txn_webhook_fail",
                "event": "payment.failed",
                "secret": "dev-webhook-secret",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.booking.status, BookingRequest.Status.FAILED)