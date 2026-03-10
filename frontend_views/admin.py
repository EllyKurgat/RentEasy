from django.contrib import admin

from .models import (
    Application,
    Conversation,
    ConversationMember,
    Document,
    Enquiry,
    LandlordPaymentMethod,
    Lease,
    Listing,
    MaintenanceRequest,
    Message,
    MpesaTransaction,
    Notification,
    Organisation,
    Payment,
    Property,
    TenantInvite,
    Unit,
    ViewingSlot,
)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "property_type", "address", "monthly_rent", "rooms_total", "landlord")
    list_filter = ("property_type",)
    search_fields = ("name", "address", "landlord__email")


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("id", "property", "tenant", "room_label", "start_date", "end_date", "status")
    list_filter = ("status",)
    search_fields = ("tenant__email", "property__name", "property__address", "room_label")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "amount", "method", "status", "paid_at")
    list_filter = ("status", "method")
    search_fields = ("reference", "lease__tenant__email", "lease__property__name")


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "issue_category", "urgency", "status", "created_at")
    list_filter = ("issue_category", "urgency", "status")
    search_fields = ("lease__tenant__email", "lease__property__name", "body")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("user__email", "message")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "sender", "conversation", "created_at")
    list_filter = ("created_at",)
    search_fields = ("body", "sender__email")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "conv_type", "title", "property", "created_at")
    list_filter = ("conv_type",)


@admin.register(ConversationMember)
class ConversationMemberAdmin(admin.ModelAdmin):
    list_display = ("conversation", "user", "last_read_at")


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "owner")
    search_fields = ("name", "slug")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("property", "unit_number", "floor", "monthly_rent", "status")
    list_filter = ("status",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "doc_type", "lease", "unit", "created_at")


@admin.register(TenantInvite)
class TenantInviteAdmin(admin.ModelAdmin):
    list_display = ("tenant_email", "property", "status", "expires_at")
    list_filter = ("status",)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("title", "unit", "rent_amount", "status")
    list_filter = ("status",)


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "listing", "status")
    list_filter = ("status",)


@admin.register(ViewingSlot)
class ViewingSlotAdmin(admin.ModelAdmin):
    list_display = ("enquiry", "proposed_at", "confirmed")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("name", "listing", "status")
    list_filter = ("status",)


@admin.register(LandlordPaymentMethod)
class LandlordPaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("landlord", "method_type", "display_name", "mpesa_number", "bank_name", "is_active")
    list_filter = ("method_type", "is_active")
    search_fields = ("landlord__email", "mpesa_number", "bank_account_number")


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = ("checkout_request_id", "phone", "amount", "status", "mpesa_receipt_number", "created_at")
    list_filter = ("status",)
    search_fields = ("checkout_request_id", "mpesa_receipt_number", "phone")
