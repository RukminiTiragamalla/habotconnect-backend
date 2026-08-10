from django.urls import path
from .views import LSASearchView,BookingCreateView,PaymentWebhookView

urlpatterns = [
    path("lsas/search/", LSASearchView.as_view(), name="lsa-search"),
    path("bookings/", BookingCreateView.as_view(), name="booking-create"),
    path("payments/webhook/", PaymentWebhookView.as_view(), name="payment-webhook"),
]