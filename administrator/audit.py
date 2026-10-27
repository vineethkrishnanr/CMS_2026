from .models import AuditLog


def log_action(user, action):
    """Write one line to the AuditLog table."""
    AuditLog.objects.create(user=user, action=action)
