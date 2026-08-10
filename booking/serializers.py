from rest_framework import serializers
from .models import BookingRequest, LSAProfile, Parent, Skill


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


class LSASearchResultSerializer(serializers.ModelSerializer):
    skills = SkillSerializer(many=True, read_only=True)

    class Meta:
        model = LSAProfile
        fields = ["id", "full_name", "email", "hourly_rate", "skills", "is_active"]


class BookingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingRequest
        fields = ["id", "parent", "lsa", "start_time", "end_time", "status"]
        read_only_fields = ["id", "status"]

    def validate(self, attrs):
        start_time = attrs["start_time"]
        end_time = attrs["end_time"]
        lsa = attrs["lsa"]

        if end_time <= start_time:
            raise serializers.ValidationError(
                {"end_time": "end_time must be after start_time."}
            )

        clash = BookingRequest.overlapping(lsa.id, start_time, end_time)
        if clash.exists():
            raise serializers.ValidationError(
                {"non_field_errors": f"LSA {lsa.full_name} is already booked for that time window."}
            )
        return attrs