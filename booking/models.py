from django.db import models

# Create your models here.
class Parent(models.Model):
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
class Skill(models.Model):
    name = models.CharField(max_length=100, unique=True)
class LSAProfile(models.Model):
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    skills = models.ManyToManyField(Skill, related_name="lsas", blank=True)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
class BookingRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending Payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Payment Failed"

    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, related_name="bookings")
    lsa = models.ForeignKey(LSAProfile, on_delete=models.CASCADE, related_name="bookings")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["lsa", "start_time", "end_time"], name="idx_lsa_time_range"),
        ]
    @classmethod
    def overlapping(cls, lsa_id, start_time, end_time, exclude_id=None):
        qs = cls.objects.filter(
            lsa_id=lsa_id,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exclude(status=cls.Status.CANCELLED)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)
        return qs
class Payment(models.Model):
    booking = models.OneToOneField(BookingRequest, on_delete=models.CASCADE, related_name="payment")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=16, default="pending")
    transaction_ref = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)