from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Unused legacy user model - the active model is frontend_views.User."""

    class Meta:
        app_label = 'accounts'

    phone = models.CharField(max_length=20, blank=True)
    is_landlord = models.BooleanField(default=False)
    is_tenant = models.BooleanField(default=False)

    def __str__(self):
        return self.username