from django.shortcuts import render, get_object_or_404
from django.conf import settings
from rest_framework import generics, status
from django.db import IntegrityError, transaction
from rest_framework.response import Response
from .models import LSAProfile, BookingRequest, Payment
from .serializers import LSASearchResultSerializer, BookingCreateSerializer
from .services.payment_gateway import initiate_payment
class LSASearchView(generics.ListAPIView):
    serializer_class = LSASearchResultSerializer

    def get_queryset(self):
        qs = LSAProfile.objects.filter(is_active=True).prefetch_related("skills")
        skill = self.request.query_params.get("skill")
        if skill:
            qs = qs.filter(skills__name__iexact=skill)
        return qs.distinct()

class BookingCreateView(generics.CreateAPIView):
    serializer_class = BookingCreateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lsa = serializer.validated_data["lsa"]
        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        with transaction.atomic():
            # Lock this LSA's existing bookings for the duration of this
            # transaction, so a concurrent request has to wait its turn
            # instead of reading stale, not-yet-committed data.
            list(BookingRequest.objects.select_for_update().filter(lsa_id=lsa.id))

            if BookingRequest.overlapping(lsa.id, start_time, end_time).exists():
                return Response(
                    {"non_field_errors": ["This LSA is already booked for the requested time window."]},
                    status=status.HTTP_409_CONFLICT,
                )

            booking = serializer.save(status=BookingRequest.Status.PENDING)

            gateway_response = initiate_payment(booking.id, lsa.hourly_rate)
            Payment.objects.create(
                booking=booking,
                amount=lsa.hourly_rate,
                transaction_ref=gateway_response["transaction_ref"],
                status=Payment.Status.PENDING if hasattr(Payment, "Status") else "pending",
            )

        return Response(BookingCreateSerializer(booking).data, status=status.HTTP_201_CREATED)


class PaymentWebhookView(generics.GenericAPIView):
    def post(self, request, *args, **kwargs):
        data = request.data

        if data.get("secret") != settings.PAYMENT_WEBHOOK_SECRET:
            return Response({"detail": "Invalid webhook secret."}, status=status.HTTP_403_FORBIDDEN)

        transaction_ref = data.get("transaction_ref")
        event = data.get("event")

        if not transaction_ref or event not in ("payment.success", "payment.failed"):
            return Response({"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)

        payment = get_object_or_404(Payment, transaction_ref=transaction_ref)

        with transaction.atomic():
            if event == "payment.success":
                payment.status = "success"
                payment.booking.status = BookingRequest.Status.CONFIRMED
            else:
                payment.status = "failed"
                payment.booking.status = BookingRequest.Status.FAILED

            payment.save(update_fields=["status", "updated_at"])
            payment.booking.save(update_fields=["status", "updated_at"])

        return Response({"detail": "Webhook processed."}, status=status.HTTP_200_OK)