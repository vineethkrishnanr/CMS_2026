from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    AuditLog,
    DoctorProfile,
    LabTechnicianProfile,
    PharmacistProfile,
    ReceptionistProfile,
    User,
)

# role -> profile table that must exist for that role
PROFILE_MODELS = {
    User.Role.RECEPTIONIST: ReceptionistProfile,
    User.Role.DOCTOR: DoctorProfile,
    User.Role.LAB_TECHNICIAN: LabTechnicianProfile,
    User.Role.PHARMACIST: PharmacistProfile,
}


class LoginSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"] = user.role
        return token


class UserSerializer(serializers.ModelSerializer):
    """Read-only view of a staff account (never exposes the password)."""

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name", "email",
            "role", "phone", "address", "is_active", "date_joined", "last_login",
        ]
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = [
            "id", "username", "password", "first_name", "last_name",
            "email", "role", "phone", "address",
        ]

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)  # hashes the password
        user.save()
        profile_model = PROFILE_MODELS.get(user.role)
        if profile_model:  # doctor/receptionist/... need a profile row
            profile_model.objects.create(user=user)
        return user

    def to_representation(self, instance):
        return UserSerializer(instance).data


class UserUpdateSerializer(serializers.ModelSerializer):
    """Role and username are NOT editable (role is tied to the profile table)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "address", "is_active"]

    def validate_email(self, value):
        if value and User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def to_representation(self, instance):
        return UserSerializer(instance).data


class ResetPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value


class AuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    role = serializers.CharField(source="user.role", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "user", "username", "role", "action", "log_time"]
        read_only_fields = fields
