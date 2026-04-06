import uuid
from builtins import property as _property
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    invite_token = models.CharField(max_length=64, blank=True, unique=True, null=True)
    password_set = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name', 'phone', 'role']

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class Organisation(models.Model):
    PLAN_CHOICES = [("starter", "Starter"), ("growth", "Growth"), ("pro", "Pro")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=80)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="starter")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="owned_organisations"
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.name


class Property(models.Model):
    PROPERTY_TYPE_CHOICES = [
        ("residential", "Residential"),
        ("commercial", "Commercial"),
        ("mixed", "Mixed-use"),
        ("bedsitter", "Bedsitter"),
        ("1bedroom", "1 Bedroom"),
        ("2bedroom", "2 Bedroom"),
        ("officeroom", "Office room"),
    ]

    landlord = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="properties"
    )
    organisation = models.ForeignKey(
        "Organisation", on_delete=models.CASCADE, null=True, blank=True, related_name="properties"
    )
    name = models.CharField(max_length=255)
    property_type = models.CharField(max_length=32, choices=PROPERTY_TYPE_CHOICES, default="residential")
    address = models.CharField(max_length=255)
    monthly_rent = models.PositiveIntegerField(default=0)
    rooms_total = models.PositiveIntegerField(default=1)
    image = models.FileField(upload_to="property_images/", blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.name} ({self.address})"


class Unit(models.Model):
    STATUS_CHOICES = [("vacant", "Vacant"), ("occupied", "Occupied"), ("maintenance", "Under maintenance")]

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="units")
    unit_number = models.CharField(max_length=64)
    floor = models.PositiveIntegerField(default=0)
    size_sqft = models.PositiveIntegerField(null=True, blank=True)
    monthly_rent = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="vacant", db_index=True)
    furnished = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [["property", "unit_number"]]

    def __str__(self) -> str:
        return f"{self.property.name} - {self.unit_number}"


class Lease(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending_signature", "Pending Signature"),
        ("active", "Active"),
        ("expiring", "Expiring"),
        ("expired", "Expired"),
        ("terminated", "Terminated"),
    ]

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="leases")
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="leases")
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leases", null=True, blank=True
    )
    room_label = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="draft", db_index=True)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(blank=True, null=True)
    monthly_rent = models.PositiveIntegerField(null=True, blank=True)
    security_deposit = models.PositiveIntegerField(default=0)
    rent_due_day = models.PositiveIntegerField(default=1)
    grace_period_days = models.PositiveIntegerField(default=5)
    late_fee_amount = models.PositiveIntegerField(default=0)
    agreement_file = models.FileField(upload_to="lease_agreements/", blank=True, null=True)
    notes = models.TextField(blank=True, help_text="Additional terms or notes for this lease.")
    landlord_signed_at = models.DateTimeField(null=True, blank=True)
    tenant_signed_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    @_property
    def is_active(self) -> bool:
        return self.status == "active"

    def __str__(self) -> str:
        return f"Lease #{self.id} ({self.status})"


class Payment(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("failed", "Failed"),
        ("overdue", "Overdue"),
    ]
    METHOD_CHOICES = [
        ("mpesa", "Mpesa"),
        ("cash", "Cash"),
        ("bank", "Bank transfer"),
        ("card", "Card"),
    ]

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="payments")
    amount = models.PositiveIntegerField()
    due_date = models.DateField(null=True, blank=True)
    late_fee = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES, blank=True)
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Payment {self.amount} ({self.status})"


class MaintenanceRequest(models.Model):
    CATEGORY_CHOICES = [
        ("plumbing", "Plumbing"),
        ("electrical", "Electrical"),
        ("hvac", "HVAC"),
        ("structural", "Structural"),
        ("genmaintenance", "General maintenance"),
    ]
    URGENCY_CHOICES = [("Critical", "Critical"), ("High", "High"), ("Medium", "Medium"), ("Low", "Low")]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("assigned", "Assigned"),
        ("in_progress", "In progress"),
        ("pending_inspection", "Pending inspection"),
        ("resolved", "Completed"),
        ("closed", "Closed"),
    ]

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="maintenance_requests")
    title = models.CharField(max_length=255, blank=True)
    issue_category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)
    urgency = models.CharField(max_length=16, choices=URGENCY_CHOICES, db_index=True)
    body = models.TextField()
    issue_image = models.FileField(upload_to="maintenance_images/", blank=True, null=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="open")
    assigned_to = models.CharField(max_length=255, blank=True)
    internal_notes = models.TextField(blank=True)
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.title or 'Maintenance'} #{self.id} ({self.status})"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Notification({self.user_id}) read={self.is_read}"


class Conversation(models.Model):
    """A chat thread – private (2 members) or group (3+)."""
    CONV_TYPE_CHOICES = [
        ("private", "Private"),
        ("group", "Group"),
    ]
    conv_type = models.CharField(max_length=10, choices=CONV_TYPE_CHOICES, default="private")
    title = models.CharField(max_length=255, blank=True, help_text="Display name for group chats.")
    # Tie to property so we can auto‑create per‑property group chats.
    property = models.ForeignKey(
        "Property", on_delete=models.CASCADE, null=True, blank=True, related_name="conversations"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or f"Conversation #{self.id}"


class ConversationMember(models.Model):
    """Who belongs to a conversation + per‑user unread tracking."""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_memberships"
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [["conversation", "user"]]

    def __str__(self) -> str:
        return f"{self.user} in {self.conversation}"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, null=True, blank=True, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_messages",
        null=True, blank=True,
    )
    body = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.sender_id} → conv={self.conversation_id}"


class Document(models.Model):
    DOC_TYPE_CHOICES = [
        ("lease", "Lease agreement"),
        ("receipt", "Payment receipt"),
        ("inspection", "Inspection report"),
        ("other", "Other"),
    ]
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    doc_type = models.CharField(max_length=24, choices=DOC_TYPE_CHOICES, default="other")
    file = models.FileField(upload_to="documents/")
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.title or f"Document #{self.id}"


class TenantInvite(models.Model):
    STATUS_CHOICES = [("sent", "Invite sent"), ("accepted", "Accepted"), ("expired", "Expired")]
    token = models.CharField(max_length=64, unique=True)
    tenant_email = models.EmailField()
    tenant_name = models.CharField(max_length=255)
    tenant_phone = models.CharField(max_length=20, blank=True)
    landlord = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_invites"
    )
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="invites")
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, null=True, blank=True, related_name="invites")
    lease = models.OneToOneField(Lease, on_delete=models.CASCADE, null=True, blank=True, related_name="invite")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="sent")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.tenant_email} → {self.property}"


class Listing(models.Model):
    STATUS_CHOICES = [("active", "Active"), ("paused", "Paused"), ("filled", "Filled")]
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="listings")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    rent_amount = models.PositiveIntegerField()
    deposit_amount = models.PositiveIntegerField(default=0)
    available_from = models.DateField(null=True, blank=True)
    property_location = models.CharField(max_length=255)
    amenities = models.JSONField(default=list)
    photos = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)
    contact_preference = models.CharField(max_length=32, default="message")
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.title


class Enquiry(models.Model):
    STATUS_CHOICES = [("new", "New"), ("replied", "Replied"), ("declined", "Declined"), ("converted", "Converted")]
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="enquiries")
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.name} - {self.listing}"


class ViewingSlot(models.Model):
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="slots")
    proposed_at = models.DateTimeField()
    confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Viewing {self.proposed_at} - {self.enquiry}"


class Application(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]
    enquiry = models.OneToOneField(Enquiry, on_delete=models.CASCADE, null=True, blank=True, related_name="application")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="applications")
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    employer = models.CharField(max_length=255, blank=True)
    monthly_income = models.PositiveIntegerField(null=True, blank=True)
    previous_address = models.CharField(max_length=255, blank=True)
    expected_move_in_date = models.DateField(null=True, blank=True)
    documents = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    screening_consent = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.name} - {self.listing} ({self.status})"


class LandlordPaymentMethod(models.Model):
    """How a landlord wants to receive rent payments from tenants."""

    METHOD_TYPE_CHOICES = [
        ("mpesa_paybill", "M-Pesa Paybill"),
        ("mpesa_till", "M-Pesa Till (Buy Goods)"),
        ("mpesa_send_money", "M-Pesa Send Money (Personal)"),
        ("mpesa_pochi", "M-Pesa Pochi La Biashara"),
        ("bank_transfer", "Bank Transfer"),
    ]

    landlord = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_methods"
    )
    method_type = models.CharField(max_length=24, choices=METHOD_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)

    # M-Pesa fields
    mpesa_number = models.CharField(
        max_length=20, blank=True,
        help_text="Phone number for Send Money / Pochi, or Paybill/Till number.",
    )
    mpesa_account_number = models.CharField(
        max_length=64, blank=True,
        help_text="Account number (for Paybill only).",
    )

    # Bank fields
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_name = models.CharField(max_length=255, blank=True)
    bank_account_number = models.CharField(max_length=64, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)

    display_name = models.CharField(
        max_length=120, blank=True,
        help_text="Friendly label shown to tenants, e.g. 'Landlord M-Pesa'.",
    )

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-is_active", "-created_at"]

    def __str__(self) -> str:
        return f"{self.get_method_type_display()} – {self.display_name or self.mpesa_number or self.bank_account_number}"

    @property
    def summary_for_tenant(self) -> str:
        """One-line display shown on the tenant payment page."""
        if self.method_type == "mpesa_paybill":
            return f"Paybill: {self.mpesa_number}  Account: {self.mpesa_account_number}"
        if self.method_type == "mpesa_till":
            return f"Till Number: {self.mpesa_number}"
        if self.method_type in ("mpesa_send_money", "mpesa_pochi"):
            return f"Send to: {self.mpesa_number}"
        if self.method_type == "bank_transfer":
            return f"{self.bank_name} – {self.bank_account_name} – {self.bank_account_number} ({self.bank_branch})"
        return str(self)


class Review(models.Model):
    """Tenant review / rating on a listing (or property)."""
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [["listing", "reviewer"]]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Review by {self.reviewer} – {self.rating}★ on {self.listing}"


class MpesaTransaction(models.Model):
    """Tracks every STK-Push request and its Safaricom callback."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    payment = models.OneToOneField(
        Payment, on_delete=models.CASCADE, null=True, blank=True, related_name="mpesa_tx"
    )
    phone = models.CharField(max_length=20)
    amount = models.PositiveIntegerField()
    checkout_request_id = models.CharField(max_length=100, unique=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    mpesa_receipt_number = models.CharField(max_length=30, blank=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Mpesa {self.checkout_request_id} ({self.status})"
