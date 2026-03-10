import calendar
import json
import logging
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db.models import Avg, Count, F, Max, Q, Subquery, OuterRef, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from .models import (
    Application,
    Conversation,
    ConversationMember,
    Enquiry,
    LandlordPaymentMethod,
    Lease,
    Listing,
    MaintenanceRequest,
    Message,
    MpesaTransaction,
    Notification,
    Payment,
    Property,
    Review,
    TenantInvite,
    Unit,
    User,
)

logger = logging.getLogger(__name__)

# ── File upload validation ──────────────────────────────────────────────────
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB


def validate_uploaded_file(uploaded_file, allowed_extensions=None, max_size=None):
    """Validate an uploaded file's extension and size.
    Returns (is_valid, error_message)."""
    if not uploaded_file:
        return True, ""
    if allowed_extensions is None:
        allowed_extensions = ALLOWED_IMAGE_EXTENSIONS
    if max_size is None:
        max_size = MAX_UPLOAD_SIZE

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in allowed_extensions:
        return False, f"File type '{ext}' is not allowed. Accepted: {', '.join(sorted(allowed_extensions))}"

    if uploaded_file.size > max_size:
        mb = max_size / (1024 * 1024)
        return False, f"File too large ({uploaded_file.size / (1024*1024):.1f} MB). Maximum is {mb:.0f} MB."

    return True, ""


# ── Safaricom IP whitelist for M-Pesa callbacks ────────────────────────────
SAFARICOM_IPS = {
    "196.201.214.200", "196.201.214.206", "196.201.213.114",
    "196.201.214.207", "196.201.214.208", "196.201.213.44",
    "196.201.212.127", "196.201.212.128", "196.201.212.129",
    "196.201.212.132", "196.201.212.136", "196.201.212.138",
}


def _ensure_active_listing_for_unit(unit: Unit) -> None:
    """Create/restore an active public listing for a vacant unit."""
    if not unit or unit.status != "vacant":
        return
    if Listing.objects.filter(unit=unit, status="active").exists():
        return
    existing = Listing.objects.filter(unit=unit).order_by("-created_at").first()
    if existing:
        existing.status = "active"
        existing.save(update_fields=["status"])
        return
    prop = unit.property
    try:
        title = f"{prop.get_property_type_display()} - {unit.unit_number}"
    except Exception:
        title = f"{getattr(prop, 'property_type', 'Rental')} - {unit.unit_number}"
    Listing.objects.create(
        unit=unit,
        title=title,
        description="",
        rent_amount=unit.monthly_rent or getattr(prop, "monthly_rent", 0) or 0,
        deposit_amount=0,
        property_location=getattr(prop, "address", "") or "",
        status="active",
    )


def role_required(role: str):
    role_normalized = (role or "").strip().lower()

    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url="user_login")
        def _wrapped(request, *args, **kwargs):
            current_role = getattr(request.user, "role", "") or ""
            if current_role.strip().lower() != role_normalized:
                # Send user back to their correct dashboard.
                if current_role.strip().lower() == "landlord":
                    return redirect("landdash")
                return redirect("userdash")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator

def home(request):
    """Landing page – same for everyone (tenants and landlords)."""
    if request.user.is_authenticated:
        user = request.user
        if getattr(user, 'role', '').lower() == 'landlord':
            return redirect('landdash')
        return redirect('userdash')
    featured_listings = list(
        Listing.objects.filter(status="active")
        .select_related("unit", "unit__property")
        .order_by("-created_at")[:3]
    )
    # Stats for animated counters (floor values for social proof)
    stat_properties = max(Property.objects.count(), 250)
    stat_tenants = max(User.objects.filter(role="tenant").count(), 1200)
    stat_landlords = max(User.objects.filter(role="landlord").count(), 85)
    stat_listings = max(Listing.objects.filter(status="active").count(), 140)
    # Reviews with good ratings for testimonials
    reviews = list(
        Review.objects.filter(rating__gte=4)
        .select_related("reviewer", "listing")
        .order_by("-created_at")[:10]
    )
    return render(request, 'renteasyweb/index.html', {
        "featured_listings": featured_listings,
        "stat_properties": stat_properties,
        "stat_tenants": stat_tenants,
        "stat_landlords": stat_landlords,
        "stat_listings": stat_listings,
        "reviews": reviews,
    })

@login_required(login_url='user_login')
@role_required("landlord")
def LandLord_dash(request):
    """Landlord dashboard with stats, alerts, and activity feed."""
    # Auto-publish vacant rooms/units so they show publicly.
    for u in Unit.objects.filter(property__landlord=request.user, status="vacant").select_related("property"):
        _ensure_active_listing_for_unit(u)

    properties = Property.objects.filter(landlord=request.user)
    total_properties = properties.count()
    total_rooms = properties.aggregate(total=Sum("rooms_total"))["total"] or 0
    occupied_rooms = Lease.objects.filter(property__in=properties, status="active").count()
    vacant_rooms = max(total_rooms - occupied_rooms, 0)
    total_tenants = Lease.objects.filter(property__in=properties, status="active").values("tenant_id").distinct().count()
    total_earnings = (
        Payment.objects.filter(lease__property__in=properties, status="confirmed")
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    pending_payments = (
        Payment.objects.filter(lease__property__in=properties, status="pending")
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    in_30_days = timezone.now().date() + timedelta(days=30)
    expiring_leases = list(
        Lease.objects.filter(property__in=properties, status="active", end_date__lte=in_30_days, end_date__gte=timezone.now().date())
        .select_related("tenant", "property")[:10]
    )
    overdue_payments = list(
        Payment.objects.filter(
            lease__property__in=properties, status__in=["pending", "overdue"], due_date__lt=timezone.now().date()
        )
        .select_related("lease", "lease__tenant")[:10]
    )

    # Recent activity feed — last 10 items across payments, maintenance, messages
    recent_payments = list(
        Payment.objects.filter(lease__property__in=properties)
        .select_related("lease", "lease__tenant", "lease__property")
        .order_by("-paid_at")[:5]
    )
    recent_maintenance = list(
        MaintenanceRequest.objects.filter(lease__property__in=properties)
        .select_related("lease", "lease__tenant", "lease__property")
        .order_by("-created_at")[:5]
    )
    open_maintenance = MaintenanceRequest.objects.filter(
        lease__property__in=properties, status__in=["open", "assigned", "in_progress"]
    ).count()
    unread_messages = Notification.objects.filter(user=request.user, is_read=False).count()

    # Build unified activity feed
    activity_feed = []
    for p in recent_payments:
        activity_feed.append({
            "icon": "fa-coins",
            "icon_color": "green" if p.status == "confirmed" else "orange",
            "text": f"{p.lease.tenant.name if p.lease.tenant else 'Tenant'} paid KSh {p.amount}" if p.status == "confirmed" else f"Pending payment of KSh {p.amount} from {p.lease.tenant.name if p.lease.tenant else 'Tenant'}",
            "time": p.paid_at or p.lease.created_at,
            "badge": p.get_status_display(),
            "badge_class": "green" if p.status == "confirmed" else "yellow" if p.status == "pending" else "red",
        })
    for mr in recent_maintenance:
        activity_feed.append({
            "icon": "fa-wrench",
            "icon_color": "red" if mr.urgency == "High" else "orange" if mr.urgency == "Medium" else "blue",
            "text": f"{mr.lease.tenant.name if mr.lease.tenant else 'Tenant'}: {mr.get_issue_category_display()} ({mr.get_urgency_display()})",
            "time": mr.created_at,
            "badge": mr.get_status_display(),
            "badge_class": "red" if mr.status == "open" else "yellow" if mr.status in ("assigned", "in_progress") else "green",
        })
    activity_feed.sort(key=lambda x: x["time"] or timezone.now(), reverse=True)
    activity_feed = activity_feed[:8]

    return render(
        request,
        "landlord/landdash.html",
        {
            "total_properties": total_properties,
            "total_rooms": total_rooms,
            "occupied_rooms": occupied_rooms,
            "vacant_rooms": vacant_rooms,
            "total_tenants": total_tenants,
            "total_earnings": total_earnings,
            "pending_payments": pending_payments,
            "expiring_leases": expiring_leases,
            "overdue_payments": overdue_payments,
            "open_maintenance": open_maintenance,
            "unread_messages": unread_messages,
            "activity_feed": activity_feed,
        },
    )

@role_required("landlord")
def LandLord_messages(request):
    user = request.user

    # ── Auto-create conversations for all the landlord's active tenants ──
    leases = Lease.objects.filter(property__landlord=user, status="active").select_related("tenant", "property")
    tenants = [l.tenant for l in leases if l.tenant]
    unique_tenants = list({t.id: t for t in tenants}.values())

    # Private chats with each tenant
    for t in unique_tenants:
        prop = next((l.property for l in leases if l.tenant_id == t.id), None)
        _get_or_create_private_conversation(user, t, prop)

    # Group chats per property
    props = {l.property_id: l.property for l in leases}
    for prop_id, prop in props.items():
        prop_tenants = [l.tenant for l in leases if l.property_id == prop_id and l.tenant]
        group_members = list({t.id: t for t in prop_tenants}.values()) + [user]
        if len(group_members) >= 2:
            _get_or_create_group_conversation(prop, group_members)

    # ── Build conversation list (optimized: annotations avoid N+1) ──
    memberships = (
        ConversationMember.objects.filter(user=user)
        .select_related("conversation", "conversation__property")
        .annotate(
            _last_msg_time=Max("conversation__messages__created_at"),
            _last_msg_body=Subquery(
                Message.objects.filter(conversation=OuterRef("conversation"))
                .order_by("-created_at")
                .values("body")[:1]
            ),
        )
    )
    conversation_list = []
    for mem in memberships:
        conv = mem.conversation
        last_time = mem._last_msg_time or conv.created_at
        last_body = (mem._last_msg_body or "")[:60]
        if mem.last_read_at:
            unread = conv.messages.filter(created_at__gt=mem.last_read_at).exclude(sender=user).count()
        else:
            unread = conv.messages.exclude(sender=user).count()
        if conv.conv_type == "group":
            display_name = conv.title or f"{conv.property.name} Group"
            avatar_icon = "fa-users"
        else:
            other_member = conv.members.exclude(user=user).select_related("user").first()
            display_name = other_member.user.name if other_member else "Unknown"
            avatar_icon = "fa-user"
        conversation_list.append({
            "id": conv.id,
            "display_name": display_name,
            "avatar_icon": avatar_icon,
            "conv_type": conv.conv_type,
            "last_message": last_body,
            "last_time": last_time,
            "unread": unread,
        })
    conversation_list.sort(key=lambda c: c["last_time"], reverse=True)

    # ── Active conversation ──
    conv_id = request.GET.get("conv")
    message_all = request.GET.get("mode") == "all"
    active_conv = None
    thread = []
    conv_display_name = ""

    if conv_id and not message_all:
        try:
            active_conv = Conversation.objects.get(id=conv_id)
            mem = ConversationMember.objects.filter(conversation=active_conv, user=user).first()
            if not mem:
                active_conv = None
            else:
                mem.last_read_at = timezone.now()
                mem.save(update_fields=["last_read_at"])
                thread = active_conv.messages.select_related("sender").order_by("created_at")
                if active_conv.conv_type == "group":
                    conv_display_name = active_conv.title or f"{active_conv.property.name} Group"
                else:
                    other_member = active_conv.members.exclude(user=user).select_related("user").first()
                    conv_display_name = other_member.user.name if other_member else "Chat"
        except Conversation.DoesNotExist:
            active_conv = None

    # ── Handle POST: send or broadcast ──
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        action = request.POST.get("action", "single")

        if action == "message_all" and body:
            if not unique_tenants:
                messages.error(request, "No tenants to message. Invite tenants first.")
                return redirect("landmessages")
            sent = 0
            for t in unique_tenants:
                prop = next((l.property for l in leases if l.tenant_id == t.id), None)
                conv = _get_or_create_private_conversation(user, t, prop)
                Message.objects.create(conversation=conv, sender=user, body=body)
                sent += 1
            messages.success(request, f"Message sent to {sent} tenant(s).")
            return redirect("landmessages")

        if action != "message_all" and conv_id and body and active_conv:
            Message.objects.create(conversation=active_conv, sender=user, body=body)
            mem = ConversationMember.objects.filter(conversation=active_conv, user=user).first()
            if mem:
                mem.last_read_at = timezone.now()
                mem.save(update_fields=["last_read_at"])
            # Notify other members
            other_members = active_conv.members.exclude(user=user).select_related("user")
            for om in other_members:
                Notification.objects.create(
                    user=om.user,
                    message=f"New message from {user.name}: {body[:80]}{'…' if len(body) > 80 else ''}",
                )
            messages.success(request, "Message sent.")
            return redirect(reverse("landmessages") + f"?conv={conv_id}")

    return render(
        request,
        "landlord/landmessages.html",
        {
            "conversation_list": conversation_list,
            "active_conv": active_conv,
            "conv_display_name": conv_display_name,
            "thread": thread,
            "message_all": message_all,
            "tenant_count": len(unique_tenants),
        },
    )

@role_required("landlord")
def LandLord_notifications(request):
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            tenant_ids = set(
                Lease.objects.filter(property__landlord=request.user, status="active").values_list("tenant_id", flat=True)
            )
            for uid in tenant_ids:
                Notification.objects.create(user_id=uid, message=body)
            messages.success(request, f"Notification sent to {len(tenant_ids)} tenant(s).")
            return redirect("landnotifications")

    notifications = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    return render(request, "landlord/landnotifications.html", {"notifications": notifications})

@role_required("landlord")
def LandLord_earnings(request):
    properties = Property.objects.filter(landlord=request.user)
    payments_qs = Payment.objects.filter(lease__property__in=properties).select_related(
        "lease", "lease__property", "lease__tenant"
    )
    total_confirmed = payments_qs.filter(status="confirmed").aggregate(total=Sum("amount"))["total"] or 0
    total_pending = payments_qs.filter(status="pending").aggregate(total=Sum("amount"))["total"] or 0
    total_overdue = payments_qs.filter(status="overdue").aggregate(total=Sum("amount"))["total"] or 0
    payments = list(payments_qs.order_by("-paid_at")[:100])

    # Active leases for manual payment recording
    active_leases = Lease.objects.filter(
        property__in=properties, status="active"
    ).select_related("tenant", "property")

    if request.method == "POST" and request.POST.get("action") == "record_payment":
        lease_id = request.POST.get("lease_id", "").strip()
        amount = request.POST.get("amount", "").strip()
        method = request.POST.get("method", "cash").strip()
        reference = request.POST.get("reference", "").strip()
        try:
            lease = Lease.objects.get(pk=lease_id, property__landlord=request.user)
            amount_int = int(amount)
            Payment.objects.create(
                lease=lease,
                amount=amount_int,
                method=method,
                reference=reference,
                status="confirmed",
                paid_at=timezone.now(),
            )
            # Notify tenant
            if lease.tenant_id:
                Notification.objects.create(
                    user_id=lease.tenant_id,
                    message=f"Your landlord recorded a {method} payment of KSh {amount_int}. Reference: {reference or 'N/A'}.",
                )
            messages.success(request, f"Payment of KSh {amount_int} recorded successfully.")
        except (Lease.DoesNotExist, ValueError):
            messages.error(request, "Invalid lease or amount.")
        return redirect("myearnings")

    # Confirm pending payments
    if request.method == "POST" and request.POST.get("action") == "confirm_payment":
        payment_id = request.POST.get("payment_id", "").strip()
        try:
            payment = Payment.objects.get(pk=payment_id, lease__property__landlord=request.user)
            payment.status = "confirmed"
            payment.paid_at = timezone.now()
            payment.save(update_fields=["status", "paid_at"])
            if payment.lease.tenant_id:
                Notification.objects.create(
                    user_id=payment.lease.tenant_id,
                    message=f"Your payment of KSh {payment.amount} has been confirmed by your landlord.",
                )
            messages.success(request, "Payment confirmed.")
        except Payment.DoesNotExist:
            messages.error(request, "Payment not found.")
        return redirect("myearnings")

    return render(
        request,
        "landlord/myearnings.html",
        {
            "payments": payments,
            "total_confirmed": total_confirmed,
            "total_pending": total_pending,
            "total_overdue": total_overdue,
            "active_leases": active_leases,
        },
    )


@role_required("landlord")
def LandLord_payment_methods(request):
    """Let the landlord add / edit / delete the ways they receive rent."""
    methods = LandlordPaymentMethod.objects.filter(landlord=request.user)

    if request.method == "POST":
        action = request.POST.get("action", "add")

        # ── Delete ────────────────────────────────────────────────
        if action == "delete":
            pk = request.POST.get("method_id")
            LandlordPaymentMethod.objects.filter(pk=pk, landlord=request.user).delete()
            messages.success(request, "Payment method removed.")
            return redirect("landlord_payment_methods")

        # ── Toggle active / inactive ─────────────────────────────
        if action == "toggle":
            pk = request.POST.get("method_id")
            obj = LandlordPaymentMethod.objects.filter(pk=pk, landlord=request.user).first()
            if obj:
                obj.is_active = not obj.is_active
                obj.save(update_fields=["is_active"])
                messages.success(request, "Payment method updated.")
            return redirect("landlord_payment_methods")

        # ── Add / Edit ───────────────────────────────────────────
        method_type = request.POST.get("method_type", "").strip()
        display_name = request.POST.get("display_name", "").strip()
        mpesa_number = request.POST.get("mpesa_number", "").strip()
        mpesa_account_number = request.POST.get("mpesa_account_number", "").strip()
        bank_name = request.POST.get("bank_name", "").strip()
        bank_account_name = request.POST.get("bank_account_name", "").strip()
        bank_account_number = request.POST.get("bank_account_number", "").strip()
        bank_branch = request.POST.get("bank_branch", "").strip()

        if not method_type:
            messages.error(request, "Please choose a payment method type.")
            return redirect("landlord_payment_methods")

        edit_pk = request.POST.get("edit_id", "").strip()
        if edit_pk:
            obj = LandlordPaymentMethod.objects.filter(pk=edit_pk, landlord=request.user).first()
            if not obj:
                messages.error(request, "Payment method not found.")
                return redirect("landlord_payment_methods")
        else:
            obj = LandlordPaymentMethod(landlord=request.user)

        obj.method_type = method_type
        obj.display_name = display_name
        obj.mpesa_number = mpesa_number
        obj.mpesa_account_number = mpesa_account_number
        obj.bank_name = bank_name
        obj.bank_account_name = bank_account_name
        obj.bank_account_number = bank_account_number
        obj.bank_branch = bank_branch
        obj.save()

        messages.success(request, "Payment method saved.")
        return redirect("landlord_payment_methods")

    return render(request, "landlord/payment_methods.html", {"methods": methods})


@role_required("landlord")
def LandLord_properties(request):
    if request.method == "POST":
        name = request.POST.get("property_name", "").strip()
        property_type = request.POST.get("property_type", "").strip()
        address = request.POST.get("address", "").strip()
        monthly_rent = request.POST.get("price", "").strip()
        rooms_total = request.POST.get("rooms_total", "").strip()
        image = request.FILES.get("image")

        # Validate uploaded image
        if image:
            valid, err = validate_uploaded_file(image)
            if not valid:
                messages.error(request, err)
                return render(request, "landlord/myproperties.html")

        if not name or not property_type or not address or not monthly_rent:
            messages.error(request, "Please fill in all required property details.")
            return render(request, "landlord/myproperties.html")

        try:
            monthly_rent_int = int(monthly_rent)
            rooms_total_int = int(rooms_total) if rooms_total else 1
        except ValueError:
            messages.error(request, "Rent and rooms must be numbers.")
            return render(request, "landlord/myproperties.html")

        prop = Property.objects.create(
            landlord=request.user,
            name=name,
            property_type=property_type,
            address=address,
            monthly_rent=monthly_rent_int,
            rooms_total=max(rooms_total_int, 1),
            image=image,
        )
        for i in range(1, prop.rooms_total + 1):
            unit_obj, _created = Unit.objects.get_or_create(
                property=prop,
                unit_number=f"Room {i}",
                defaults={"monthly_rent": monthly_rent_int, "floor": 0},
            )
            _ensure_active_listing_for_unit(unit_obj)
        messages.success(request, "Property registered successfully.")
        return redirect("viewproperties")

    return render(request, "landlord/myproperties.html")

@role_required("landlord")
def LandLord_tenants(request):
    _check_expiring_leases()
    leases = (
        Lease.objects.filter(property__landlord=request.user, status__in=["active", "expiring"])
        .select_related("tenant", "property", "unit")
        .order_by("-start_date")
    )
    invites = TenantInvite.objects.filter(landlord=request.user, status="sent").select_related("property", "unit")[:20]
    return render(request, "landlord/mytenants.html", {"leases": leases, "invites": invites})


@role_required("landlord")
def LandLord_add_tenant(request):
    properties = Property.objects.filter(landlord=request.user).prefetch_related("units")
    units = Unit.objects.filter(property__landlord=request.user).select_related("property")
    if request.method == "POST":
        tenant_type = request.POST.get("tenant_type", "new")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        prop_id = request.POST.get("property_id", "").strip()
        unit_id = request.POST.get("unit_id", "").strip()
        start_date = request.POST.get("start_date", "").strip()
        end_date = request.POST.get("end_date", "").strip()
        rent_amount = request.POST.get("rent_amount", "").strip()
        deposit_amount = request.POST.get("deposit_amount", "").strip()

        if not name or not prop_id:
            messages.error(request, "Name and property are required.")
            return render(
                request,
                "landlord/add_tenant.html",
                {"properties": properties, "units": units},
            )
        if tenant_type == "new" and not email:
            messages.error(request, "Email is required for new tenants so they can log in later.")
            return render(
                request,
                "landlord/add_tenant.html",
                {"properties": properties, "units": units},
            )

        prop = get_object_or_404(Property, pk=prop_id, landlord=request.user)
        unit = None
        if unit_id:
            unit = Unit.objects.filter(pk=unit_id, property=prop).first()
            if not unit:
                messages.error(request, "Selected unit does not belong to the chosen property.")
                return render(
                    request,
                    "landlord/add_tenant.html",
                    {"properties": properties, "units": units},
                )

        # For "new tenant" flow, prefer vacant units
        if tenant_type == "new" and not unit:
            unit = (
                Unit.objects.filter(property=prop, status="vacant")
                .order_by("unit_number")
                .first()
            )
            if not unit:
                messages.error(request, "No vacant units available for this property.")
                return render(
                    request,
                    "landlord/add_tenant.html",
                    {"properties": properties, "units": units},
                )

        # Parse start_date
        try:
            start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else timezone.now().date()
        except ValueError:
            start_d = timezone.now().date()

        # Parse end_date
        try:
            end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            end_d = None

        # Convert rent and deposit
        try:
            rent_int = int(rent_amount) if rent_amount else (unit.monthly_rent if unit else prop.monthly_rent)
        except (ValueError, TypeError):
            rent_int = (unit.monthly_rent if unit else prop.monthly_rent) or 0

        try:
            deposit_int = int(deposit_amount) if deposit_amount else 0
        except ValueError:
            deposit_int = 0

        # Create or reuse tenant user
        existing = User.objects.filter(email__iexact=email).first() if email else None
        if existing:
            user = existing
            user.name = name
            user.phone = phone or (user.phone or "")
            user.role = "tenant"
            user.save(update_fields=["name", "phone", "role"])
        else:
            # If email missing for migration, create a placeholder non-login account
            if not email:
                email = f"tenant-{timezone.now().timestamp()}@placeholder.local"
            user = User.objects.create_user(
                email=email,
                password=None,
                name=name,
                phone=phone or "",
                role="tenant",
            )

        # Create the lease
        lease = Lease.objects.create(
            property=prop,
            unit=unit,
            tenant=user,
            room_label=unit.unit_number if unit else "",
            status="active",
            start_date=start_d,
            end_date=end_d,
            monthly_rent=rent_int,
            security_deposit=deposit_int,
        )

        # Update unit status if applicable
        if unit:
            unit.status = "occupied"
            unit.save(update_fields=["status"])
            Listing.objects.filter(unit=unit, status="active").update(status="filled")

        messages.success(request, "Tenant and lease created successfully.")
        return redirect("mytenants")

    return render(request, "landlord/add_tenant.html", {"properties": properties, "units": units})

@role_required("landlord")
def LandLord_profile(request):
    return redirect("profile")

@role_required("landlord")
def LandLord_viewproperties(request):
    properties = list(Property.objects.filter(landlord=request.user).order_by("-created_at"))
    occupancy_by_property_id = {
        row["property_id"]: row["cnt"]
        for row in Lease.objects.filter(property__in=properties, status="active")
        .values("property_id")
        .annotate(cnt=Count("id"))
    }
    property_rows = []
    for p in properties:
        occupied = occupancy_by_property_id.get(p.id, 0) or 0
        vacant = max(p.rooms_total - occupied, 0)
        property_rows.append(
            {
                "property": p,
                "occupied": occupied,
                "vacant": vacant,
            }
        )
    return render(request, "landlord/viewproperties.html", {"property_rows": property_rows})


@role_required("landlord")
def LandLord_invite_tenant(request):
    properties = Property.objects.filter(landlord=request.user)
    units = Unit.objects.filter(property__landlord=request.user).select_related("property")
    if request.method == "POST":
        tenant_email = request.POST.get("tenant_email", "").strip()
        tenant_name = request.POST.get("tenant_name", "").strip()
        tenant_phone = request.POST.get("tenant_phone", "").strip()
        prop_id = request.POST.get("property_id", "").strip()
        unit_id = request.POST.get("unit_id", "").strip()
        monthly_rent = request.POST.get("monthly_rent", "").strip()
        start_date = request.POST.get("start_date", "").strip()
        end_date = request.POST.get("end_date", "").strip()

        if not tenant_email or not tenant_name or not prop_id:
            messages.error(request, "Tenant email, name, and property are required.")
            return render(request, "landlord/invite_tenant.html", {"properties": properties, "units": units})

        prop = get_object_or_404(Property, pk=prop_id, landlord=request.user)
        unit = None
        if unit_id:
            unit = Unit.objects.filter(pk=unit_id, property=prop).first()
            if not unit:
                messages.error(request, "Selected unit does not belong to the chosen property.")
                return render(request, "landlord/invite_tenant.html", {"properties": properties, "units": units})

        try:
            rent_int = int(monthly_rent) if monthly_rent else (unit.monthly_rent if unit else prop.monthly_rent)
        except (ValueError, TypeError):
            rent_int = unit.monthly_rent if unit else prop.monthly_rent or 0

        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(days=7)
        try:
            start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else timezone.now().date()
        except ValueError:
            start_d = timezone.now().date()
        try:
            end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            end_d = None

        lease = Lease.objects.create(
            property=prop,
            unit=unit,
            tenant=None,
            room_label=unit.unit_number if unit else "",
            status="pending_signature",
            monthly_rent=rent_int,
            start_date=start_d,
            end_date=end_d,
        )
        TenantInvite.objects.create(
            token=token,
            tenant_email=tenant_email,
            tenant_name=tenant_name,
            tenant_phone=tenant_phone,
            landlord=request.user,
            property=prop,
            unit=unit,
            lease=lease,
            status="sent",
            expires_at=expires_at,
        )
        invite_url = request.build_absolute_uri(f"/invite/accept/{token}/")
        subject = f"You're invited to join {prop.name} – RentEasy"
        body = f"""Hi {tenant_name},

{request.user.name} has invited you to join {prop.name} as a tenant on RentEasy.

Click the link below to accept the invite and create your account (link expires in 7 days):

{invite_url}

If you didn't expect this invite, you can ignore this email.
"""
        try:
            send_mail(
                subject,
                body,
                None,  # Uses DEFAULT_FROM_EMAIL from settings
                [tenant_email],
                fail_silently=False,
            )
            messages.success(
                request,
                f"Tenant invited successfully. Invite link sent to {tenant_email}.",
            )
        except Exception:
            logger.exception("Failed to send tenant invite email")
            messages.success(
                request,
                "Tenant invited successfully. We couldn't send the email—please share this link with the tenant: "
                f"{invite_url}",
            )

        # Add notification for landlord with the invite link (so they can access it anytime)
        unit_label = unit.unit_number if unit else "—"
        Notification.objects.create(
            user=request.user,
            message=f"Invite sent to {tenant_name} ({tenant_email}) for {prop.name}"
            f"{f' – {unit_label}' if unit else ''}. "
            f"Invite link (valid 7 days): {invite_url}",
        )
        return redirect("mytenants")

    return render(request, "landlord/invite_tenant.html", {"properties": properties, "units": units})


def tenant_invite_accept(request, token):
    invite = get_object_or_404(TenantInvite, token=token)
    if invite.status != "sent":
        messages.error(request, "This invite has already been used or expired.")
        return redirect("home")
    if invite.expires_at < timezone.now():
        invite.status = "expired"
        invite.save(update_fields=["status"])
        messages.error(request, "This invite has expired.")
        return redirect("home")

    if request.method == "POST":
        password = request.POST.get("password", "").strip()
        if not password or len(password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return render(request, "invite_accept.html", {"invite": invite})

        existing = User.objects.filter(email__iexact=invite.tenant_email).first()
        if existing:
            user = existing
            user.name = invite.tenant_name
            user.phone = invite.tenant_phone or (user.phone or "")
            user.role = "tenant"
            user.set_password(password)
            user.password_set = True
            user.save()
        else:
            user = User.objects.create_user(
                email=invite.tenant_email,
                password=password,
                name=invite.tenant_name,
                phone=invite.tenant_phone or "",
                role="tenant",
            )
            user.password_set = True
            user.save(update_fields=["password_set"])

        invite.lease.tenant = user
        invite.lease.status = "active"
        invite.lease.save(update_fields=["tenant", "status"])
        if invite.unit:
            invite.unit.status = "occupied"
            invite.unit.save(update_fields=["status"])
            # Hide unit from public marketplace once occupied.
            Listing.objects.filter(unit=invite.unit, status="active").update(status="filled")
        invite.status = "accepted"
        invite.save(update_fields=["status"])

        # We create the user directly (not via authenticate), so specify backend.
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Account created. Welcome to your tenant dashboard!")
        return redirect("userdash")

    return render(request, "invite_accept.html", {"invite": invite})


def listing_list(request):
    qs = Listing.objects.filter(status="active").select_related("unit", "unit__property")

    # Annotate average rating
    qs = qs.annotate(avg_rating=Avg("reviews__rating"), review_count=Count("reviews"))

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(property_location__icontains=q)
        )
    category = request.GET.get("category", "").strip()
    if category:
        qs = qs.filter(unit__property__property_type=category)
    rent_min = request.GET.get("rent_min")
    rent_max = request.GET.get("rent_max")
    if rent_min:
        try:
            qs = qs.filter(rent_amount__gte=int(rent_min))
        except ValueError:
            pass
    if rent_max:
        try:
            qs = qs.filter(rent_amount__lte=int(rent_max))
        except ValueError:
            pass

    # Availability date filter
    avail_from = request.GET.get("available_from", "").strip()
    if avail_from:
        try:
            avail_date = datetime.strptime(avail_from, "%Y-%m-%d").date()
            qs = qs.filter(Q(available_from__isnull=True) | Q(available_from__lte=avail_date))
        except ValueError:
            pass

    # Sort
    sort = request.GET.get("sort", "newest").strip()
    if sort == "price_asc":
        qs = qs.order_by("rent_amount")
    elif sort == "price_desc":
        qs = qs.order_by("-rent_amount")
    elif sort == "rating":
        qs = qs.order_by(F("avg_rating").desc(nulls_last=True))
    else:
        qs = qs.order_by("-created_at")

    listings = list(qs[:50])
    return render(request, "listings/list.html", {
        "listings": listings, "q": q, "category": category,
        "rent_min": request.GET.get("rent_min", ""),
        "rent_max": request.GET.get("rent_max", ""),
        "available_from": avail_from,
        "sort": sort,
    })


def listing_detail(request, pk):
    listing = get_object_or_404(Listing.objects.select_related("unit", "unit__property"), pk=pk)
    if listing.status != "active":
        messages.error(request, "This listing is no longer available.")
        return redirect("listing_list")

    # Reviews for this listing
    reviews = listing.reviews.select_related("reviewer").order_by("-created_at")
    avg_rating = reviews.aggregate(avg=Avg("rating"))["avg"]
    review_count = reviews.count()

    # Check if the logged-in user already reviewed
    user_review = None
    can_review = False
    is_tenant_of_property = False
    if request.user.is_authenticated:
        user_review = reviews.filter(reviewer=request.user).first()
        # Only allow reviews from tenants who have/had a lease on this unit or property
        is_tenant_of_property = Lease.objects.filter(
            tenant=request.user,
            unit=listing.unit,
            status__in=["active", "expiring", "expired", "terminated"],
        ).exists()
        can_review = user_review is None and is_tenant_of_property

    if request.method == "POST":
        action = request.POST.get("form_action", "enquiry")

        # ── Review submission ──
        if action == "review" and request.user.is_authenticated and is_tenant_of_property:
            rating = request.POST.get("rating", "").strip()
            comment = request.POST.get("comment", "").strip()
            if not rating:
                messages.error(request, "Please select a rating.")
            else:
                try:
                    rating_int = int(rating)
                    if rating_int < 1 or rating_int > 5:
                        raise ValueError
                except ValueError:
                    messages.error(request, "Invalid rating.")
                    return redirect("listing_detail", pk=pk)

                if user_review:
                    user_review.rating = rating_int
                    user_review.comment = comment
                    user_review.save(update_fields=["rating", "comment"])
                    messages.success(request, "Review updated.")
                else:
                    Review.objects.create(
                        listing=listing,
                        reviewer=request.user,
                        rating=rating_int,
                        comment=comment,
                    )
                    messages.success(request, "Review submitted. Thank you!")
            return redirect("listing_detail", pk=pk)

        # ── Enquiry submission (default) ──
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        message_text = request.POST.get("message", "").strip()
        if not name or not email or not message_text:
            messages.error(request, "Please fill in name, email, and message.")
        else:
            Enquiry.objects.create(
                listing=listing,
                name=name,
                email=email,
                phone=phone,
                message=message_text,
            )
            messages.success(request, "Enquiry submitted. The landlord will contact you soon.")
        return redirect("listing_detail", pk=pk)

    return render(request, "listings/detail.html", {
        "listing": listing,
        "reviews": reviews,
        "avg_rating": avg_rating,
        "review_count": review_count,
        "can_review": can_review,
        "user_review": user_review,
        "is_tenant_of_property": is_tenant_of_property,
    })


def application_create(request, listing_pk):
    listing = get_object_or_404(Listing.objects.select_related("unit", "unit__property"), pk=listing_pk)
    if listing.status != "active":
        messages.error(request, "This listing is no longer available.")
        return redirect("listing_list")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        employer = request.POST.get("employer", "").strip()
        monthly_income = request.POST.get("monthly_income", "").strip()
        previous_address = request.POST.get("previous_address", "").strip()
        screening_consent = request.POST.get("screening_consent") == "on"
        if not name or not email or not phone:
            messages.error(request, "Name, email, and phone are required.")
            return render(request, "listings/apply.html", {"listing": listing})
        income = None
        if monthly_income:
            try:
                income = int(monthly_income)
            except ValueError:
                pass
        app = Application.objects.create(
            listing=listing,
            name=name,
            email=email,
            phone=phone,
            employer=employer,
            monthly_income=income,
            previous_address=previous_address,
            screening_consent=screening_consent,
        )
        # Notify landlord
        landlord = listing.unit.property.landlord
        if landlord:
            Notification.objects.create(
                user=landlord,
                message=f"New rental application from {name} ({email}) for listing: {listing.title}.",
            )
        messages.success(request, "Application submitted. The landlord will review it shortly.")
        return redirect("listing_detail", pk=listing_pk)
    return render(request, "listings/apply.html", {"listing": listing})


@role_required("landlord")
def LandLord_listings(request):
    units = Unit.objects.filter(property__landlord=request.user).select_related("property")
    # Backfill any missing public listings for existing vacant units.
    for u in units.filter(status="vacant"):
        _ensure_active_listing_for_unit(u)
    listings = Listing.objects.filter(unit__property__landlord=request.user).select_related("unit", "unit__property").order_by("-created_at")
    return render(request, "landlord/mylistings.html", {"listings": listings, "units": units})


@role_required("landlord")
def LandLord_create_listing(request):
    units = Unit.objects.filter(property__landlord=request.user, status="vacant").select_related("property")
    if request.method == "POST":
        unit_id = request.POST.get("unit_id", "").strip()
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        rent_amount = request.POST.get("rent_amount", "").strip()
        deposit_amount = request.POST.get("deposit_amount", "").strip()
        property_location = request.POST.get("property_location", "").strip()
        available_from = request.POST.get("available_from", "").strip()
        if not unit_id or not title or not rent_amount:
            messages.error(request, "Unit, title, and rent are required.")
            return render(request, "landlord/create_listing.html", {"units": units})
        unit = get_object_or_404(Unit, pk=unit_id, property__landlord=request.user)
        try:
            rent_int = int(rent_amount)
            deposit_int = int(deposit_amount) if deposit_amount else 0
        except ValueError:
            messages.error(request, "Rent and deposit must be numbers.")
            return render(request, "landlord/create_listing.html", {"units": units})
        avail = None
        if available_from:
            try:
                avail = datetime.strptime(available_from, "%Y-%m-%d").date()
            except ValueError:
                pass
        Listing.objects.create(
            unit=unit,
            title=title,
            description=description,
            rent_amount=rent_int,
            deposit_amount=deposit_int,
            property_location=property_location or unit.property.address,
            available_from=avail,
        )
        messages.success(request, "Listing created.")
        return redirect("landlord_listings")
    return render(request, "landlord/create_listing.html", {"units": units})


@role_required("landlord")
def LandLord_applications(request):
    applications = Application.objects.filter(listing__unit__property__landlord=request.user).select_related(
        "listing", "listing__unit", "listing__unit__property"
    ).order_by("-created_at")
    return render(request, "landlord/applications.html", {"applications": applications})


@role_required("landlord")
def LandLord_application_action(request, pk):
    app = get_object_or_404(
        Application.objects.select_related("listing", "listing__unit"),
        pk=pk,
        listing__unit__property__landlord=request.user,
    )
    action = request.POST.get("action")
    if action == "approve":
        app.status = "approved"
        app.save(update_fields=["status"])
        messages.success(request, "Application approved.")
    elif action == "reject":
        app.status = "rejected"
        app.save(update_fields=["status"])
        messages.success(request, "Application rejected.")
    return redirect("landlord_applications")


@role_required("landlord")
def LandLord_lease_actions(request):
    """Lease renewal/termination actions."""
    if request.method != "POST":
        return redirect("landdash")
    lease_id = request.POST.get("lease_id")
    action = request.POST.get("action")
    if not lease_id or not action:
        return redirect("landdash")
    lease = get_object_or_404(Lease, pk=lease_id, property__landlord=request.user)
    if action == "renew":
        if lease.end_date:
            lease.end_date = lease.end_date + timedelta(days=365)
        else:
            lease.end_date = timezone.now().date() + timedelta(days=365)
        lease.status = "active"
        lease.save(update_fields=["end_date", "status"])
        messages.success(request, "Lease renewed.")
    elif action == "terminate":
        lease.status = "terminated"
        lease.save(update_fields=["status"])
        if lease.unit:
            lease.unit.status = "vacant"
            lease.unit.save(update_fields=["status"])
            _ensure_active_listing_for_unit(lease.unit)
        messages.success(request, "Lease terminated.")
    return redirect("landlord_leases")


# ── Full Lease Workflow ──────────────────────────────────────────────────────

def _check_expiring_leases():
    """Auto-transition leases approaching end_date to 'expiring' status
    and expired ones to 'expired'. Called from lease list views."""
    today = timezone.now().date()
    thirty_days = today + timedelta(days=30)

    # Mark leases expiring within 30 days
    Lease.objects.filter(
        status="active",
        end_date__isnull=False,
        end_date__lte=thirty_days,
        end_date__gt=today,
    ).update(status="expiring")

    # Mark past-end-date leases as expired
    expired_leases = Lease.objects.filter(
        status__in=["active", "expiring"],
        end_date__isnull=False,
        end_date__lt=today,
    )
    for lease in expired_leases:
        lease.status = "expired"
        lease.save(update_fields=["status"])
        if lease.unit:
            lease.unit.status = "vacant"
            lease.unit.save(update_fields=["status"])
            _ensure_active_listing_for_unit(lease.unit)
        # Notify tenant
        if lease.tenant:
            Notification.objects.create(
                user=lease.tenant,
                message=f"Your lease for {lease.property.name} has expired. Please contact your landlord.",
            )
        # Notify landlord
        Notification.objects.create(
            user=lease.property.landlord,
            message=f"Lease #{lease.id} for {lease.tenant.name if lease.tenant else 'unknown'} at {lease.property.name} has expired.",
        )


@role_required("landlord")
def LandLord_leases(request):
    """Landlord lease management – view all leases across all properties."""
    _check_expiring_leases()

    status_filter = request.GET.get("status", "all")
    leases = Lease.objects.filter(
        property__landlord=request.user
    ).select_related("tenant", "property", "unit").order_by("-created_at")

    if status_filter != "all":
        leases = leases.filter(status=status_filter)

    # Counts for filter tabs
    all_leases = Lease.objects.filter(property__landlord=request.user)
    counts = {
        "all": all_leases.count(),
        "draft": all_leases.filter(status="draft").count(),
        "pending_signature": all_leases.filter(status="pending_signature").count(),
        "active": all_leases.filter(status="active").count(),
        "expiring": all_leases.filter(status="expiring").count(),
        "expired": all_leases.filter(status="expired").count(),
        "terminated": all_leases.filter(status="terminated").count(),
    }

    return render(request, "landlord/leases.html", {
        "leases": leases,
        "status_filter": status_filter,
        "counts": counts,
    })


@role_required("landlord")
def LandLord_lease_detail(request, pk):
    """Landlord view/edit a single lease with full details."""
    lease = get_object_or_404(
        Lease.objects.select_related("tenant", "property", "unit"),
        pk=pk,
        property__landlord=request.user,
    )
    properties = Property.objects.filter(landlord=request.user)
    units = Unit.objects.filter(property__landlord=request.user).select_related("property")
    tenants = User.objects.filter(role="tenant", leases__property__landlord=request.user).distinct()

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action == "save":
            # Update lease fields
            prop_id = request.POST.get("property_id")
            unit_id = request.POST.get("unit_id")
            tenant_id = request.POST.get("tenant_id")
            start_date = request.POST.get("start_date", "").strip()
            end_date = request.POST.get("end_date", "").strip()
            monthly_rent = request.POST.get("monthly_rent", "").strip()
            security_deposit = request.POST.get("security_deposit", "").strip()
            rent_due_day = request.POST.get("rent_due_day", "").strip()
            grace_period = request.POST.get("grace_period_days", "").strip()
            late_fee = request.POST.get("late_fee_amount", "").strip()
            notes = request.POST.get("notes", "").strip()

            if prop_id:
                lease.property = get_object_or_404(Property, pk=prop_id, landlord=request.user)
            if unit_id:
                lease.unit = Unit.objects.filter(pk=unit_id, property=lease.property).first()
            if tenant_id:
                lease.tenant = User.objects.filter(pk=tenant_id).first()

            try:
                lease.start_date = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else lease.start_date
            except ValueError:
                pass
            try:
                lease.end_date = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else lease.end_date
            except ValueError:
                pass
            try:
                lease.monthly_rent = int(monthly_rent) if monthly_rent else lease.monthly_rent
            except ValueError:
                pass
            try:
                lease.security_deposit = int(security_deposit) if security_deposit else lease.security_deposit
            except ValueError:
                pass
            try:
                lease.rent_due_day = int(rent_due_day) if rent_due_day else lease.rent_due_day
            except ValueError:
                pass
            try:
                lease.grace_period_days = int(grace_period) if grace_period else lease.grace_period_days
            except ValueError:
                pass
            try:
                lease.late_fee_amount = int(late_fee) if late_fee else lease.late_fee_amount
            except ValueError:
                pass
            lease.notes = notes

            # Handle file upload
            agreement_file = request.FILES.get("agreement_file")
            if agreement_file:
                ALLOWED_DOC_EXTENSIONS = {'.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'}
                valid, err = validate_uploaded_file(agreement_file, ALLOWED_DOC_EXTENSIONS, 10 * 1024 * 1024)
                if valid:
                    lease.agreement_file = agreement_file
                else:
                    messages.error(request, err)
                    return redirect("landlord_lease_detail", pk=lease.pk)

            lease.save()
            messages.success(request, "Lease updated successfully.")
            return redirect("landlord_lease_detail", pk=lease.pk)

        elif action == "send_for_signature":
            if not lease.tenant:
                messages.error(request, "Please assign a tenant before sending for signature.")
                return redirect("landlord_lease_detail", pk=lease.pk)
            lease.status = "pending_signature"
            lease.landlord_signed_at = timezone.now()
            lease.save(update_fields=["status", "landlord_signed_at"])
            # Notify tenant
            Notification.objects.create(
                user=lease.tenant,
                message=f"You have a new lease agreement to review for {lease.property.name}. "
                        f"Please review and sign it from your My Rental page.",
            )
            messages.success(request, f"Lease sent to {lease.tenant.name} for signature.")
            return redirect("landlord_lease_detail", pk=lease.pk)

        elif action == "activate":
            lease.status = "active"
            if not lease.landlord_signed_at:
                lease.landlord_signed_at = timezone.now()
            lease.signed_at = timezone.now()
            lease.save(update_fields=["status", "landlord_signed_at", "signed_at"])
            if lease.unit:
                lease.unit.status = "occupied"
                lease.unit.save(update_fields=["status"])
                Listing.objects.filter(unit=lease.unit, status="active").update(status="filled")
            if lease.tenant:
                Notification.objects.create(
                    user=lease.tenant,
                    message=f"Your lease for {lease.property.name} is now active!",
                )
            messages.success(request, "Lease activated.")
            return redirect("landlord_lease_detail", pk=lease.pk)

        elif action == "renew":
            if lease.end_date:
                lease.end_date = lease.end_date + timedelta(days=365)
            else:
                lease.end_date = timezone.now().date() + timedelta(days=365)
            lease.status = "active"
            lease.save(update_fields=["end_date", "status"])
            if lease.tenant:
                Notification.objects.create(
                    user=lease.tenant,
                    message=f"Your lease for {lease.property.name} has been renewed until {lease.end_date}.",
                )
            messages.success(request, "Lease renewed.")
            return redirect("landlord_lease_detail", pk=lease.pk)

        elif action == "terminate":
            lease.status = "terminated"
            lease.save(update_fields=["status"])
            if lease.unit:
                lease.unit.status = "vacant"
                lease.unit.save(update_fields=["status"])
                _ensure_active_listing_for_unit(lease.unit)
            if lease.tenant:
                Notification.objects.create(
                    user=lease.tenant,
                    message=f"Your lease for {lease.property.name} has been terminated by the landlord.",
                )
            messages.success(request, "Lease terminated.")
            return redirect("landlord_lease_detail", pk=lease.pk)

    return render(request, "landlord/lease_detail.html", {
        "lease": lease,
        "properties": properties,
        "units": units,
        "tenants": tenants,
    })


@role_required("landlord")
def LandLord_lease_create(request):
    """Create a new lease (draft)."""
    properties = Property.objects.filter(landlord=request.user).prefetch_related("units")
    units = Unit.objects.filter(property__landlord=request.user).select_related("property")

    if request.method == "POST":
        prop_id = request.POST.get("property_id", "").strip()
        unit_id = request.POST.get("unit_id", "").strip()
        tenant_id = request.POST.get("tenant_id", "").strip()
        start_date = request.POST.get("start_date", "").strip()
        end_date = request.POST.get("end_date", "").strip()
        monthly_rent = request.POST.get("monthly_rent", "").strip()
        security_deposit = request.POST.get("security_deposit", "").strip()
        rent_due_day = request.POST.get("rent_due_day", "").strip()
        grace_period = request.POST.get("grace_period_days", "").strip()
        late_fee = request.POST.get("late_fee_amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        if not prop_id:
            messages.error(request, "Property is required.")
            return render(request, "landlord/lease_create.html", {"properties": properties, "units": units})

        prop = get_object_or_404(Property, pk=prop_id, landlord=request.user)
        unit = Unit.objects.filter(pk=unit_id, property=prop).first() if unit_id else None

        try:
            start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else timezone.now().date()
        except ValueError:
            start_d = timezone.now().date()
        try:
            end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            end_d = None
        try:
            rent_int = int(monthly_rent) if monthly_rent else (unit.monthly_rent if unit else prop.monthly_rent)
        except (ValueError, TypeError):
            rent_int = (unit.monthly_rent if unit else prop.monthly_rent) or 0
        try:
            deposit_int = int(security_deposit) if security_deposit else 0
        except ValueError:
            deposit_int = 0
        try:
            due_day = int(rent_due_day) if rent_due_day else 1
        except ValueError:
            due_day = 1
        try:
            grace_int = int(grace_period) if grace_period else 5
        except ValueError:
            grace_int = 5
        try:
            late_int = int(late_fee) if late_fee else 0
        except ValueError:
            late_int = 0

        tenant = User.objects.filter(pk=tenant_id).first() if tenant_id else None

        # Handle agreement file
        agreement_file = request.FILES.get("agreement_file")
        if agreement_file:
            ALLOWED_DOC_EXTENSIONS = {'.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'}
            valid, err = validate_uploaded_file(agreement_file, ALLOWED_DOC_EXTENSIONS, 10 * 1024 * 1024)
            if not valid:
                messages.error(request, err)
                return render(request, "landlord/lease_create.html", {"properties": properties, "units": units})

        lease = Lease.objects.create(
            property=prop,
            unit=unit,
            tenant=tenant,
            room_label=unit.unit_number if unit else "",
            status="draft",
            start_date=start_d,
            end_date=end_d,
            monthly_rent=rent_int,
            security_deposit=deposit_int,
            rent_due_day=due_day,
            grace_period_days=grace_int,
            late_fee_amount=late_int,
            notes=notes,
        )
        if agreement_file:
            lease.agreement_file = agreement_file
            lease.save(update_fields=["agreement_file"])

        messages.success(request, f"Lease #{lease.id} created as draft.")
        return redirect("landlord_lease_detail", pk=lease.pk)

    return render(request, "landlord/lease_create.html", {"properties": properties, "units": units})


@role_required("tenant")
def tenant_lease_review(request):
    """Tenant reviews and signs/declines a pending lease."""
    pending_lease = Lease.objects.filter(
        tenant=request.user,
        status="pending_signature",
    ).select_related("property", "property__landlord", "unit").first()

    if not pending_lease:
        messages.info(request, "No pending lease to review.")
        return redirect("myrental")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "accept":
            pending_lease.status = "active"
            pending_lease.tenant_signed_at = timezone.now()
            pending_lease.signed_at = timezone.now()
            pending_lease.save(update_fields=["status", "tenant_signed_at", "signed_at"])
            if pending_lease.unit:
                pending_lease.unit.status = "occupied"
                pending_lease.unit.save(update_fields=["status"])
                Listing.objects.filter(unit=pending_lease.unit, status="active").update(status="filled")
            # Notify landlord
            Notification.objects.create(
                user=pending_lease.property.landlord,
                message=f"{request.user.name} has signed the lease for {pending_lease.property.name}. The lease is now active.",
            )
            messages.success(request, "Lease signed successfully! Your lease is now active.")
            return redirect("myrental")

        elif action == "decline":
            reason = request.POST.get("decline_reason", "").strip()
            pending_lease.declined_at = timezone.now()
            pending_lease.decline_reason = reason
            pending_lease.status = "draft"
            pending_lease.save(update_fields=["declined_at", "decline_reason", "status"])
            # Notify landlord
            Notification.objects.create(
                user=pending_lease.property.landlord,
                message=f"{request.user.name} declined the lease for {pending_lease.property.name}."
                        + (f" Reason: {reason}" if reason else ""),
            )
            messages.info(request, "Lease declined. The landlord has been notified.")
            return redirect("myrental")

    return render(request, "tenant/lease_review.html", {"lease": pending_lease})


@role_required("landlord")
def LandLord_maintenance(request):
    status_filter = request.GET.get("status", "open")
    qs = MaintenanceRequest.objects.filter(
        lease__property__landlord=request.user
    ).select_related("lease", "lease__property", "lease__tenant")
    if status_filter == "open":
        qs = qs.filter(status__in=["open", "assigned"])
    elif status_filter == "in_progress":
        qs = qs.filter(status__in=["in_progress", "pending_inspection"])
    elif status_filter == "completed":
        qs = qs.filter(status="resolved")
    requests_list = qs.order_by("-created_at")
    if request.method == "POST":
        mr_id = request.POST.get("maintenance_id")
        action = request.POST.get("action")
        assigned_to = request.POST.get("assigned_to", "").strip()
        internal_notes = request.POST.get("internal_notes", "").strip()
        resolution_notes = request.POST.get("resolution_notes", "").strip()
        if mr_id:
            mr = get_object_or_404(
                MaintenanceRequest,
                pk=mr_id,
                lease__property__landlord=request.user,
            )
            if action == "assign" and assigned_to:
                mr.assigned_to = assigned_to
                mr.status = "assigned"
                mr.save(update_fields=["assigned_to", "status"])
                messages.success(request, "Maintenance request assigned.")
            elif action == "start":
                mr.status = "in_progress"
                mr.save(update_fields=["status"])
                messages.success(request, "Marked as in progress.")
            elif action == "resolve" and resolution_notes:
                mr.resolution_notes = resolution_notes
                mr.status = "resolved"
                mr.resolved_at = timezone.now()
                mr.save(update_fields=["resolution_notes", "status", "resolved_at"])
                messages.success(request, "Marked as resolved.")
            elif action == "close":
                mr.status = "closed"
                mr.save(update_fields=["status"])
                messages.success(request, "Request closed.")
            elif action == "notes" and internal_notes:
                mr.internal_notes = internal_notes
                mr.save(update_fields=["internal_notes"])
                messages.success(request, "Internal notes saved.")
        return redirect("landlord_maintenance")
    return render(
        request,
        "landlord/maintenance.html",
        {"requests": requests_list, "status_filter": status_filter},
    )


@role_required("landlord")
def LandLord_maintenance_detail(request, pk: int):
    mr = get_object_or_404(
        MaintenanceRequest,
        pk=pk,
        lease__property__landlord=request.user,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "in_progress" and mr.status in ("open", "pending"):
            mr.status = "in_progress"
            mr.save(update_fields=["status"])
            messages.success(request, "Maintenance request marked as in progress.")
            return redirect("landlord_maintenance_detail", pk=mr.pk)
        if action == "complete" and mr.status != "resolved":
            mr.status = "resolved"
            mr.resolved_at = timezone.now()
            mr.save(update_fields=["status", "resolved_at"])
            messages.success(request, "Maintenance request marked as completed.")
            return redirect("landlord_maintenance_detail", pk=mr.pk)
    return render(request, "landlord/maintenance_detail.html", {"request_obj": mr})


def forgotpass(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = None

        if user is None:
            return render(request, 'forgotpass.html', {
                'status': 'error',
                'status_message': 'No account is registered with this email address. Please verify your email and try again.',
            })

        # User exists — generate token and send reset email
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_url = request.build_absolute_uri(
            reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        )
        send_mail(
            subject="RentEasy – Password Reset Request",
            message=(
                f"Dear {user.name},\n\n"
                f"We received a request to reset the password associated with your RentEasy account.\n\n"
                f"Please click the link below to set a new password:\n{reset_url}\n\n"
                f"This link will expire after one use. If you did not request a password reset, "
                f"you may safely disregard this email.\n\n"
                f"Best regards,\nThe RentEasy Team"
            ),
            from_email=None,
            recipient_list=[user.email],
            fail_silently=True,
        )
        return render(request, 'forgotpass.html', {
            'status': 'success',
            'status_message': f'A password reset link has been sent to {user.email}. Please check your inbox and follow the instructions to reset your password.',
        })
    return render(request, 'forgotpass.html')


def password_reset_confirm(request, uidb64, token):
    """Handle the password reset link clicked from email."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        messages.error(request, "This password reset link is invalid or has expired.")
        return redirect('forgotpass')

    if request.method == 'POST':
        password = request.POST.get('password', '')
        try:
            validate_password(password, user)
        except DjangoValidationError as e:
            for error in e.messages:
                messages.error(request, error)
            return render(request, 'updatepass.html', {'token': token, 'uidb64': uidb64})

        user.set_password(password)
        user.save()
        messages.success(request, "Your password has been reset. Please log in.")
        return redirect('user_login')

    return render(request, 'updatepass.html', {'token': token, 'uidb64': uidb64})



def register(request):
    if request.user.is_authenticated:
        if getattr(request.user, 'role', '').lower() == 'landlord':
            return redirect('landdash')
        return redirect('userdash')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        role = request.POST.get('role', '').strip()
        password = request.POST.get('password', '')

        # Validate role
        if role.lower() not in ('tenant', 'landlord'):
            messages.error(request, 'Invalid role. Must be Tenant or Landlord.')
            return render(request, 'register.html')

        if not name or not email:
            messages.error(request, 'Name and email are required.')
            return render(request, 'register.html')

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'An account with this email already exists.')
            return render(request, 'register.html')

        # Validate password strength using Django's AUTH_PASSWORD_VALIDATORS
        try:
            validate_password(password)
        except DjangoValidationError as e:
            for error in e.messages:
                messages.error(request, error)
            return render(request, 'register.html')

        user = User.objects.create_user(
            name=name,
            email=email,
            phone=phone,
            role=role,
            password=password
        )
        messages.success(request, 'Account created successfully! Please log in.')
        return redirect('user_login')

    return render(request, 'register.html')

@ratelimit(key="post:email", rate="5/m", method="POST", block=False)
@ratelimit(key="ip", rate="10/m", method="POST", block=False)
def user_login(request):
    if request.user.is_authenticated:
        if getattr(request.user, 'role', '').lower() == 'landlord':
            return redirect('landdash')
        return redirect('userdash')
    if request.method == 'POST':
        # Check rate limit
        if getattr(request, "limited", False):
            messages.error(request, "Too many login attempts. Please wait a minute and try again.")
            return render(request, 'user_login.html')
        email = request.POST.get('email')
        password = request.POST.get('password')
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, 'Welcome back!')
            if getattr(user, 'role', '').lower() == 'landlord':
                return redirect('landdash')
            return redirect('userdash')
        messages.error(request, 'Invalid email or password.')
    return render(request, 'user_login.html')

@role_required("tenant")
def maintenance(request):
    active_lease = (
        Lease.objects.filter(tenant=request.user, status="active").select_related("property").first()
    )
    if request.method == "POST":
        if not active_lease:
            messages.error(request, "You don't have an active lease yet.")
            return redirect("maintenance")

        issue_category = request.POST.get("issue_category", "").strip()
        urgency = request.POST.get("urgency", "").strip()
        body = request.POST.get("body", "").strip()
        issue_image = request.FILES.get("issue_image")

        # Validate uploaded image
        if issue_image:
            valid, err = validate_uploaded_file(issue_image)
            if not valid:
                messages.error(request, err)
                return redirect("maintenance")

        if not issue_category or not urgency or not body:
            messages.error(request, "Please fill in all required fields.")
            return redirect("maintenance")

        req = MaintenanceRequest.objects.create(
            lease=active_lease,
            issue_category=issue_category,
            urgency=urgency,
            body=body,
            issue_image=issue_image,
        )
        # Notify landlord
        landlord = active_lease.property.landlord
        if landlord:
            Notification.objects.create(
                user=landlord,
                message=f"Tenant {request.user.name} submitted a maintenance request: {req.get_issue_category_display()} ({req.get_urgency_display()}). {body[:80]}{'...' if len(body) > 80 else ''}",
            )
        messages.success(request, "Maintenance request submitted.")
        return redirect("maintenance")

    requests = (
        MaintenanceRequest.objects.filter(lease__tenant=request.user)
        .select_related("lease", "lease__property")
        .order_by("-created_at")[:50]
    )
    return render(request, "maintenance.html", {"active_lease": active_lease, "requests": requests})

def _get_or_create_private_conversation(user_a, user_b, prop=None):
    """Return (or create) the private Conversation between two users."""
    # Look for an existing private conv where both are members
    common = (
        Conversation.objects.filter(conv_type="private", members__user=user_a)
        .filter(members__user=user_b)
    )
    if prop:
        common = common.filter(property=prop)
    conv = common.first()
    if conv:
        return conv
    conv = Conversation.objects.create(conv_type="private", property=prop)
    ConversationMember.objects.create(conversation=conv, user=user_a)
    ConversationMember.objects.create(conversation=conv, user=user_b)
    return conv


def _get_or_create_group_conversation(prop, member_users):
    """Return (or create) a group chat for a property, ensuring all members belong."""
    conv = Conversation.objects.filter(conv_type="group", property=prop).first()
    if not conv:
        conv = Conversation.objects.create(
            conv_type="group",
            property=prop,
            title=f"{prop.name} Group Chat",
        )
    existing_ids = set(conv.members.values_list("user_id", flat=True))
    for u in member_users:
        if u.id not in existing_ids:
            ConversationMember.objects.create(conversation=conv, user=u)
    return conv


@role_required("tenant")
def message(request):
    """Google-Messages-style inbox: private chats + group chat per property."""
    user = request.user
    active_lease = (
        Lease.objects.filter(tenant=user, status="active")
        .select_related("property__landlord")
        .first()
    )
    if not active_lease:
        return render(request, "message.html", {"conversation_list": [], "active_conv": None, "thread": []})

    prop = active_lease.property
    landlord = prop.landlord

    # --- auto-create conversations ---
    # 1) Private chat with landlord
    if landlord:
        _get_or_create_private_conversation(user, landlord, prop)

    # 2) Private chats with co-tenants in the same property
    co_tenant_ids = (
        Lease.objects.filter(property=prop, status="active")
        .exclude(tenant=user)
        .exclude(tenant__isnull=True)
        .values_list("tenant_id", flat=True)
    )
    co_tenants = User.objects.filter(id__in=co_tenant_ids)
    for ct in co_tenants:
        _get_or_create_private_conversation(user, ct, prop)

    # 3) Group chat (all tenants + landlord)
    group_members = list(co_tenants) + [user]
    if landlord:
        group_members.append(landlord)
    if len(group_members) >= 2:
        _get_or_create_group_conversation(prop, group_members)

    # --- build conversation list (optimized: annotations avoid N+1) ---
    memberships = (
        ConversationMember.objects.filter(user=user)
        .select_related("conversation", "conversation__property")
        .annotate(
            _last_msg_time=Max("conversation__messages__created_at"),
            _last_msg_body=Subquery(
                Message.objects.filter(conversation=OuterRef("conversation"))
                .order_by("-created_at")
                .values("body")[:1]
            ),
        )
    )
    conversation_list = []
    for mem in memberships:
        conv = mem.conversation
        last_time = mem._last_msg_time or conv.created_at
        last_body = (mem._last_msg_body or "")[:60]
        if mem.last_read_at:
            unread = conv.messages.filter(created_at__gt=mem.last_read_at).exclude(sender=user).count()
        else:
            unread = conv.messages.exclude(sender=user).count()
        # Determine display name
        if conv.conv_type == "group":
            display_name = conv.title or f"{conv.property.name} Group"
            avatar_icon = "fa-users"
        else:
            other_member = conv.members.exclude(user=user).select_related("user").first()
            display_name = other_member.user.name if other_member else "Unknown"
            avatar_icon = "fa-user"
        conversation_list.append({
            "id": conv.id,
            "display_name": display_name,
            "avatar_icon": avatar_icon,
            "conv_type": conv.conv_type,
            "last_message": last_body,
            "last_time": last_time,
            "unread": unread,
        })
    # Sort by most recent message
    conversation_list.sort(key=lambda c: c["last_time"], reverse=True)

    # --- active conversation ---
    conv_id = request.GET.get("conv")
    active_conv = None
    thread = []
    conv_display_name = ""
    if conv_id:
        try:
            active_conv = Conversation.objects.get(id=conv_id)
            # Verify user is a member
            mem = ConversationMember.objects.filter(conversation=active_conv, user=user).first()
            if not mem:
                active_conv = None
            else:
                # Mark as read
                mem.last_read_at = timezone.now()
                mem.save(update_fields=["last_read_at"])
                thread = (
                    active_conv.messages.select_related("sender").order_by("created_at")
                )
                if active_conv.conv_type == "group":
                    conv_display_name = active_conv.title or f"{active_conv.property.name} Group"
                else:
                    other_member = active_conv.members.exclude(user=user).select_related("user").first()
                    conv_display_name = other_member.user.name if other_member else "Chat"
        except Conversation.DoesNotExist:
            active_conv = None

    return render(
        request,
        "message.html",
        {
            "conversation_list": conversation_list,
            "active_conv": active_conv,
            "conv_display_name": conv_display_name,
            "thread": thread,
        },
    )


@login_required
def message_send(request):
    """AJAX endpoint: send a message into a conversation."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    conv_id = request.POST.get("conv_id")
    body = request.POST.get("body", "").strip()
    if not conv_id or not body:
        return JsonResponse({"error": "Missing conv_id or body"}, status=400)
    try:
        conv = Conversation.objects.get(id=conv_id)
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Conversation not found"}, status=404)
    mem = ConversationMember.objects.filter(conversation=conv, user=request.user).first()
    if not mem:
        return JsonResponse({"error": "Not a member"}, status=403)
    msg = Message.objects.create(conversation=conv, sender=request.user, body=body)
    # Update sender's own last_read_at
    mem.last_read_at = timezone.now()
    mem.save(update_fields=["last_read_at"])
    # Notify other members
    other_members = conv.members.exclude(user=request.user).select_related("user")
    for om in other_members:
        Notification.objects.create(
            user=om.user,
            message=f"New message from {request.user.name}: {body[:80]}{'…' if len(body) > 80 else ''}",
        )
    return JsonResponse({
        "id": msg.id,
        "sender_name": request.user.name,
        "sender_id": request.user.id,
        "body": msg.body,
        "created_at": msg.created_at.strftime("%b %d, %H:%M"),
    })


@login_required
def message_poll(request):
    """AJAX long-ish poll: return messages newer than `after` for a conversation."""
    conv_id = request.GET.get("conv_id")
    after_id = request.GET.get("after", "0")
    if not conv_id:
        return JsonResponse({"messages": []})
    try:
        conv = Conversation.objects.get(id=conv_id)
    except Conversation.DoesNotExist:
        return JsonResponse({"messages": []})
    mem = ConversationMember.objects.filter(conversation=conv, user=request.user).first()
    if not mem:
        return JsonResponse({"messages": []})
    new_msgs = (
        conv.messages.filter(id__gt=int(after_id))
        .select_related("sender")
        .order_by("created_at")
    )
    # Update last_read_at
    if new_msgs.exists():
        mem.last_read_at = timezone.now()
        mem.save(update_fields=["last_read_at"])
    data = [
        {
            "id": m.id,
            "sender_name": m.sender.name,
            "sender_id": m.sender.id,
            "body": m.body,
            "created_at": m.created_at.strftime("%b %d, %H:%M"),
        }
        for m in new_msgs
    ]
    # Also return updated unread counts for sidebar
    memberships = ConversationMember.objects.filter(user=request.user)
    unread_map = {}
    for membership in memberships:
        c = membership.conversation
        if membership.last_read_at:
            unread_map[c.id] = c.messages.filter(created_at__gt=membership.last_read_at).exclude(sender=request.user).count()
        else:
            unread_map[c.id] = c.messages.exclude(sender=request.user).count()
    return JsonResponse({"messages": data, "unread_map": unread_map})

@role_required("tenant")
def myrental(request):
    active_lease = (
        Lease.objects.filter(tenant=request.user, status__in=["active", "expiring"]).select_related("property", "property__landlord").first()
    )
    pending_lease = (
        Lease.objects.filter(tenant=request.user, status="pending_signature").select_related("property", "property__landlord", "unit").first()
    )
    return render(request, "myrental.html", {
        "lease": active_lease,
        "property": getattr(active_lease, "property", None),
        "pending_lease": pending_lease,
    })

@role_required("tenant")
def notifications(request):
    notifications_list = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    return render(request, "notifications.html", {"notifications": notifications_list})


@role_required("tenant")
def notification_mark_read(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.is_read = True
    n.save(update_fields=["is_read"])
    return JsonResponse({"status": "success"})


@role_required("tenant")
def notification_delete(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.delete()
    return JsonResponse({"status": "success"})


@login_required(login_url="user_login")
def notification_unread_count(request):
    """Return JSON with the unread notification count for the current user."""
    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({"unread_count": count})


@role_required("tenant")
def payrent(request):
    active_lease = Lease.objects.filter(tenant=request.user, status="active").select_related("property").first()

    # ── Fetch the landlord's configured payment methods ────────────────
    landlord_methods = []
    if active_lease and active_lease.property.landlord_id:
        landlord_methods = list(
            LandlordPaymentMethod.objects.filter(
                landlord_id=active_lease.property.landlord_id, is_active=True
            )
        )

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        if not active_lease:
            if is_ajax:
                return JsonResponse({"success": False, "message": "You don't have an active lease yet."})
            messages.error(request, "You don't have an active lease yet.")
            return redirect("payrent")

        # Which landlord payment method did the tenant pick?
        lpm_id = request.POST.get("payment_method_id", "").strip()
        lpm = None
        if lpm_id:
            try:
                lpm = LandlordPaymentMethod.objects.get(pk=lpm_id, is_active=True)
            except LandlordPaymentMethod.DoesNotExist:
                pass

        # Determine method category for the Payment record
        if lpm and lpm.method_type.startswith("mpesa"):
            method = "mpesa"
        elif lpm and lpm.method_type == "bank_transfer":
            method = "bank"
        else:
            method = request.POST.get("method", "mpesa").strip()

        amount = request.POST.get("amount", "").strip()
        reference = request.POST.get("reference", "").strip()
        try:
            amount_int = int(amount)
        except ValueError:
            if is_ajax:
                return JsonResponse({"success": False, "message": "Please enter a valid amount."})
            messages.error(request, "Please enter a valid amount.")
            return redirect("payrent")

        if method == "mpesa":
            # ── M-Pesa STK Push flow ──────────────────────────────
            phone = request.POST.get("phone", "").strip()
            if not phone:
                phone = request.user.phone
            if not phone:
                if is_ajax:
                    return JsonResponse({"success": False, "message": "Please provide a phone number for M-Pesa."})
                messages.error(request, "Please provide a phone number for M-Pesa.")
                return redirect("payrent")

            from .mpesa import initiate_stk_push, format_phone
            try:
                resp = initiate_stk_push(
                    phone=phone,
                    amount=amount_int,
                    account_reference=f"Rent-{active_lease.id}",
                    transaction_desc=f"Rent payment for {active_lease.property.name}",
                    landlord_payment_method=lpm,
                )
            except Exception as exc:
                logger.exception("STK Push failed")
                if is_ajax:
                    return JsonResponse({"success": False, "message": f"M-Pesa request failed: {exc}"})
                messages.error(request, f"M-Pesa request failed: {exc}")
                return redirect("payrent")

            if resp.get("ResponseCode") == "0":
                payment = Payment.objects.create(
                    lease=active_lease,
                    amount=amount_int,
                    method="mpesa",
                    reference="",
                    status="pending",
                )
                MpesaTransaction.objects.create(
                    payment=payment,
                    phone=format_phone(phone),
                    amount=amount_int,
                    checkout_request_id=resp["CheckoutRequestID"],
                    merchant_request_id=resp.get("MerchantRequestID", ""),
                )
                return JsonResponse({
                    "success": True,
                    "checkout_request_id": resp["CheckoutRequestID"],
                    "message": resp.get("CustomerMessage", "Check your phone for the M-Pesa prompt."),
                })
            else:
                return JsonResponse({
                    "success": False,
                    "message": resp.get("ResponseDescription", "STK Push request was rejected."),
                })
        else:
            # ── Non-M-Pesa (cash / bank) – original flow ─────────
            Payment.objects.create(
                lease=active_lease, amount=amount_int, method=method,
                reference=reference, status="pending",
            )
            if active_lease.property.landlord_id:
                Notification.objects.create(
                    user_id=active_lease.property.landlord_id,
                    message=f"Tenant {request.user.name} submitted a payment of KSh {amount_int} (ref: {reference or 'N/A'}) - pending confirmation.",
                )
            messages.success(request, "Payment submitted (pending confirmation).")
            return redirect("rentspay")

    return render(request, "payrent.html", {
        "active_lease": active_lease,
        "landlord_methods": landlord_methods,
    })


# ── M-Pesa callback (called by Safaricom servers) ──────────────────────────


@csrf_exempt
def mpesa_callback(request):
    """Safaricom POSTs the STK-Push result here."""
    if request.method != "POST":
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Method not allowed"}, status=405)

    # Validate callback origin (Safaricom IP whitelist)
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR", "")
    if client_ip not in SAFARICOM_IPS and not settings.DEBUG:
        logger.warning("M-Pesa callback from untrusted IP: %s", client_ip)
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid JSON"}, status=400)

    logger.info("M-Pesa callback payload: %s", data)

    stk = data.get("Body", {}).get("stkCallback", {})
    checkout_id = stk.get("CheckoutRequestID", "")
    result_code = stk.get("ResultCode")
    result_desc = stk.get("ResultDesc", "")

    try:
        tx = MpesaTransaction.objects.get(checkout_request_id=checkout_id)
    except MpesaTransaction.DoesNotExist:
        logger.warning("Callback for unknown CheckoutRequestID: %s", checkout_id)
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    tx.result_code = result_code
    tx.result_desc = result_desc

    if result_code == 0:
        # Successful payment
        tx.status = "success"

        # Extract receipt number from callback metadata
        items = stk.get("CallbackMetadata", {}).get("Item", [])
        meta = {item["Name"]: item.get("Value") for item in items}
        receipt = meta.get("MpesaReceiptNumber", "")
        tx.mpesa_receipt_number = receipt

        # Update linked Payment
        if tx.payment:
            tx.payment.status = "confirmed"
            tx.payment.reference = receipt
            tx.payment.paid_at = timezone.now()
            tx.payment.save()

            # Notify landlord
            lease = tx.payment.lease
            if lease and lease.property.landlord_id:
                Notification.objects.create(
                    user_id=lease.property.landlord_id,
                    message=(
                        f"M-Pesa payment of KSh {tx.amount} confirmed "
                        f"(receipt {receipt}) for {lease.property.name}."
                    ),
                )
    else:
        tx.status = "failed" if result_code != 1032 else "cancelled"
        if tx.payment:
            tx.payment.status = "failed"
            tx.payment.save()

    tx.save()
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ── Polling endpoint – front-end checks payment status ─────────────────────
@login_required(login_url="user_login")
def mpesa_check_status(request):
    checkout_id = request.GET.get("checkout_request_id", "")
    if not checkout_id:
        return JsonResponse({"error": "Missing checkout_request_id"}, status=400)
    try:
        tx = MpesaTransaction.objects.get(checkout_request_id=checkout_id)
    except MpesaTransaction.DoesNotExist:
        return JsonResponse({"error": "Transaction not found"}, status=404)

    # If still pending, ask Daraja directly instead of waiting for callback
    if tx.status == "pending":
        try:
            from .mpesa import query_stk_status
            result = query_stk_status(checkout_id)
            rc = result.get("ResultCode")

            if rc is not None:                       # Daraja returned a definitive answer
                rc = int(rc)                         # Safaricom sends it as a string
                tx.result_code = rc
                tx.result_desc = result.get("ResultDesc", "")

                # Map common result codes to user-friendly messages
                _FRIENDLY = {
                    1:    "Insufficient M-Pesa balance.",
                    1032: "You cancelled the transaction.",
                    1037: "You didn't enter your PIN in time. Please try again.",
                    2001: "The wrong M-Pesa PIN was entered.",
                    1025: "An error occurred. Please try again.",
                    1019: "Transaction expired. Please try again.",
                }
                friendly = _FRIENDLY.get(rc)

                if rc == 0:
                    tx.status = "success"
                    tx.mpesa_receipt_number = result.get("MpesaReceiptNumber", "")
                    # Also mark linked Payment as confirmed
                    if tx.payment and tx.payment.status != "confirmed":
                        tx.payment.status = "confirmed"
                        tx.payment.save()
                elif rc == 1032:                      # user cancelled
                    tx.status = "cancelled"
                    tx.result_desc = friendly or tx.result_desc
                else:                                 # insufficient funds / wrong PIN / timeout / other
                    tx.status = "failed"
                    tx.result_desc = friendly or tx.result_desc or "The transaction was not completed."

                tx.save()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("STK Query failed for %s: %s", checkout_id, exc)
            # Don't crash – just return the DB status as-is

    return JsonResponse({
        "status": tx.status,
        "receipt": tx.mpesa_receipt_number,
        "result_desc": tx.result_desc,
    })

@login_required(login_url="user_login")
def profile(request):
    if request.method == "POST":
        action = request.POST.get("form_action", "profile")

        # ── Password change ──────────────────────────────────────────────
        if action == "change_password":
            current_password = request.POST.get("current_password", "")
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not request.user.check_password(current_password):
                messages.error(request, "Current password is incorrect.")
                return redirect("profile")
            if new_password != confirm_password:
                messages.error(request, "New passwords do not match.")
                return redirect("profile")
            try:
                validate_password(new_password, request.user)
            except DjangoValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                return redirect("profile")

            request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password updated successfully.")
            return redirect("profile")

        # ── Profile info update ──────────────────────────────────────────
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()

        if not name or not email:
            messages.error(request, "Name and email are required.")
            return redirect("profile")

        request.user.name = name
        request.user.email = email
        request.user.phone = phone or ""

        # Avatar upload
        if request.FILES.get("avatar"):
            avatar = request.FILES["avatar"]
            # Validate file type and size (max 2 MB)
            allowed = ("image/jpeg", "image/png", "image/gif", "image/webp")
            if avatar.content_type not in allowed:
                messages.error(request, "Please upload a JPEG, PNG, GIF or WebP image.")
                return redirect("profile")
            if avatar.size > 2 * 1024 * 1024:
                messages.error(request, "Image must be under 2 MB.")
                return redirect("profile")
            # Delete old avatar file if it exists
            if request.user.avatar:
                request.user.avatar.delete(save=False)
            request.user.avatar = avatar

        # Avatar removal
        if request.POST.get("remove_avatar") == "1":
            if request.user.avatar:
                request.user.avatar.delete(save=False)
            request.user.avatar = None

        request.user.save()

        # Notify landlord(s) when tenant updates profile
        if getattr(request.user, "role", "") == "tenant":
            for lease in Lease.objects.filter(tenant=request.user, status="active").select_related("property"):
                if lease.property.landlord_id:
                    Notification.objects.create(
                        user_id=lease.property.landlord_id,
                        message=f"Tenant {request.user.name} ({request.user.email}) updated their profile.",
                    )
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "profile.html")

@role_required("tenant")
def rentspay(request):
    active_lease = Lease.objects.filter(tenant=request.user, status="active").select_related("property").first()
    payments = (
        Payment.objects.filter(lease__tenant=request.user)
        .select_related("lease", "lease__property")
        .order_by("-paid_at")[:100]
    )

    # Compute extra stats for the tenant payment page
    total_paid = 0
    payment_status = "No lease"
    rent_due_day = 5
    if active_lease:
        confirmed_qs = Payment.objects.filter(lease=active_lease, status="confirmed")
        total_paid = confirmed_qs.aggregate(total=Sum("amount"))["total"] or 0
        rent_due_day = active_lease.rent_due_day or 5

        now = timezone.now().date()
        current_month_paid = confirmed_qs.filter(
            paid_at__month=now.month, paid_at__year=now.year
        ).exists()
        if current_month_paid:
            payment_status = "Paid"
        elif now.day > rent_due_day + (active_lease.grace_period_days or 5):
            payment_status = "Overdue"
        else:
            payment_status = "Pending"

    return render(request, "rentspay.html", {
        "active_lease": active_lease,
        "payments": payments,
        "total_paid": total_paid,
        "payment_status": payment_status,
        "rent_due_day": rent_due_day,
    })

@login_required(login_url='user_login')
def updatepass(request):
    """Change password for a logged-in user."""
    if request.method == 'POST':
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('password', '')

        if not request.user.check_password(current_password):
            messages.error(request, 'Current password is incorrect.')
            return render(request, 'updatepass.html', {'mode': 'change'})

        try:
            validate_password(new_password, request.user)
        except DjangoValidationError as e:
            for error in e.messages:
                messages.error(request, error)
            return render(request, 'updatepass.html', {'mode': 'change'})

        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Password updated successfully.')
        return redirect('userdash')

    return render(request, 'updatepass.html', {'mode': 'change'})

@login_required(login_url='user_login')
def userdash(request):
    if getattr(request.user, "role", "").strip().lower() == "landlord":
        return redirect("landdash")

    active_lease = Lease.objects.filter(tenant=request.user, status="active").select_related("property", "property__landlord").first()

    # Payment stats
    recent_payments = []
    total_paid = 0
    last_payment = None
    payment_status = "No lease"
    unread_messages = 0
    open_maintenance = 0

    if active_lease:
        payments_qs = Payment.objects.filter(lease=active_lease).order_by("-paid_at")
        recent_payments = list(payments_qs[:5])
        total_paid = payments_qs.filter(status="confirmed").aggregate(total=Sum("amount"))["total"] or 0
        last_payment = payments_qs.filter(status="confirmed").first()

        # Determine current payment status
        now = timezone.now().date()
        current_month_paid = payments_qs.filter(
            status="confirmed",
            paid_at__month=now.month,
            paid_at__year=now.year,
        ).exists()
        if current_month_paid:
            payment_status = "Paid"
        elif now.day > (active_lease.rent_due_day or 5) + (active_lease.grace_period_days or 5):
            payment_status = "Overdue"
        else:
            payment_status = "Pending"

        open_maintenance = MaintenanceRequest.objects.filter(
            lease=active_lease, status__in=["open", "assigned", "in_progress"]
        ).count()

    unread_messages = Notification.objects.filter(user=request.user, is_read=False).count()

    # Recent activity feed for tenant
    activity_feed = []
    if active_lease:
        for p in recent_payments[:3]:
            activity_feed.append({
                "icon": "fa-coins",
                "icon_color": "green" if p.status == "confirmed" else "orange",
                "text": f"Payment of KSh {p.amount} – {p.get_status_display()}",
                "time": p.paid_at or active_lease.created_at,
            })
        recent_mr = MaintenanceRequest.objects.filter(lease=active_lease).order_by("-created_at")[:3]
        for mr in recent_mr:
            activity_feed.append({
                "icon": "fa-wrench",
                "icon_color": "blue",
                "text": f"{mr.get_issue_category_display()}: {mr.get_status_display()}",
                "time": mr.created_at,
            })
        activity_feed.sort(key=lambda x: x["time"] or timezone.now(), reverse=True)
        activity_feed = activity_feed[:5]

    return render(request, "userdash.html", {
        "active_lease": active_lease,
        "payment_status": payment_status,
        "total_paid": total_paid,
        "last_payment": last_payment,
        "recent_payments": recent_payments,
        "open_maintenance": open_maintenance,
        "unread_messages": unread_messages,
        "activity_feed": activity_feed,
    })

def dashboard(request):
    if 'user_id' not in request.session:
        return redirect('user_login')
    return render(request, 'dashboard.html')


# ── Reports & Analytics ──────────────────────────────────────────────────────
@role_required("landlord")
def LandLord_reports(request):
    """Landlord reports & analytics dashboard."""

    properties = Property.objects.filter(landlord=request.user)
    total_properties = properties.count()
    total_units = Unit.objects.filter(property__in=properties).count()
    occupied_units = Lease.objects.filter(
        property__in=properties, status="active"
    ).values("unit_id").distinct().count()
    vacant_units = max(total_units - occupied_units, 0)
    occupancy_rate = round((occupied_units / total_units * 100) if total_units else 0, 1)

    # Date range for report (default: last 12 months)
    now = timezone.now()
    year = int(request.GET.get("year", now.year))
    payments_qs = Payment.objects.filter(
        lease__property__in=properties,
        status="confirmed",
    )

    # Monthly income breakdown for the selected year
    monthly_income = defaultdict(int)
    year_payments = payments_qs.filter(paid_at__year=year)
    for row in year_payments.values("paid_at__month").annotate(total=Sum("amount")):
        monthly_income[row["paid_at__month"]] = row["total"]
    monthly_data = []
    for m in range(1, 13):
        monthly_data.append({
            "month": calendar.month_abbr[m],
            "amount": monthly_income.get(m, 0),
        })

    total_income_year = sum(d["amount"] for d in monthly_data)
    max_month_income = max((d["amount"] for d in monthly_data), default=1) or 1

    # Summary totals  
    total_income_all = payments_qs.aggregate(t=Sum("amount"))["t"] or 0
    total_pending = Payment.objects.filter(
        lease__property__in=properties, status="pending"
    ).aggregate(t=Sum("amount"))["t"] or 0
    total_overdue = Payment.objects.filter(
        lease__property__in=properties, status="overdue"
    ).aggregate(t=Sum("amount"))["t"] or 0

    # Maintenance stats
    maint_qs = MaintenanceRequest.objects.filter(lease__property__in=properties)
    maint_open = maint_qs.filter(status__in=["open", "assigned"]).count()
    maint_progress = maint_qs.filter(status__in=["in_progress", "pending_inspection"]).count()
    maint_resolved = maint_qs.filter(status__in=["resolved", "closed"]).count()
    maint_total = maint_qs.count()

    # Maintenance by category
    maint_by_category = list(
        maint_qs.values("issue_category").annotate(cnt=Count("id")).order_by("-cnt")
    )

    # Tenant summary
    active_leases = Lease.objects.filter(property__in=properties, status="active").select_related("tenant", "property")
    total_tenants = active_leases.values("tenant_id").distinct().count()
    expiring_soon = active_leases.filter(
        end_date__isnull=False,
        end_date__lte=now.date() + timezone.timedelta(days=60),
    ).count()

    # Per-property breakdown
    property_breakdown = []
    for prop in properties:
        p_units = Unit.objects.filter(property=prop).count()
        p_occupied = Lease.objects.filter(property=prop, status="active").values("unit_id").distinct().count()
        p_income = payments_qs.filter(lease__property=prop, paid_at__year=year).aggregate(t=Sum("amount"))["t"] or 0
        p_maint = maint_qs.filter(lease__property=prop, status__in=["open", "assigned", "in_progress"]).count()
        property_breakdown.append({
            "property": prop,
            "units": p_units,
            "occupied": p_occupied,
            "vacant": max(p_units - p_occupied, 0),
            "occupancy_pct": round((p_occupied / p_units * 100) if p_units else 0),
            "income": p_income,
            "open_maintenance": p_maint,
        })

    # Payment method distribution
    method_dist = list(
        year_payments.values("method").annotate(cnt=Count("id"), total=Sum("amount")).order_by("-total")
    )

    # Available years for dropdown
    first_payment = payments_qs.order_by("paid_at").first()
    available_years = list(range(first_payment.paid_at.year if first_payment and first_payment.paid_at else now.year, now.year + 1))

    return render(request, "landlord/reports.html", {
        "total_properties": total_properties,
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "occupancy_rate": occupancy_rate,
        "year": year,
        "available_years": available_years,
        "monthly_data": monthly_data,
        "total_income_year": total_income_year,
        "max_month_income": max_month_income,
        "total_income_all": total_income_all,
        "total_pending": total_pending,
        "total_overdue": total_overdue,
        "maint_open": maint_open,
        "maint_progress": maint_progress,
        "maint_resolved": maint_resolved,
        "maint_total": maint_total,
        "maint_by_category": maint_by_category,
        "total_tenants": total_tenants,
        "expiring_soon": expiring_soon,
        "property_breakdown": property_breakdown,
        "method_dist": method_dist,
    })


def about(request):
    return render(request, 'renteasyweb/about.html')


def contact(request):
    return render(request, 'renteasyweb/contact.html')


def contactlandlord(request, listing_pk):
    listing = get_object_or_404(
        Listing.objects.select_related("unit", "unit__property", "unit__property__landlord"),
        pk=listing_pk,
    )
    landlord = listing.unit.property.landlord

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        message_text = request.POST.get("body", "").strip()
        if not name or not email or not message_text:
            messages.error(request, "Please fill in name, email, and message.")
        else:
            Enquiry.objects.create(
                listing=listing,
                name=name,
                email=email,
                phone=phone,
                message=message_text,
            )
            # Send email notification to landlord
            try:
                send_mail(
                    f"RentEasy: New enquiry for {listing.title}",
                    f"Hi {landlord.name},\n\n"
                    f"{name} ({email}) sent you a message about your listing \"{listing.title}\":\n\n"
                    f"{message_text}\n\n"
                    f"Reply to them at: {email}" + (f" | Phone: {phone}" if phone else ""),
                    None,
                    [landlord.email],
                    fail_silently=True,
                )
            except Exception:
                pass
            messages.success(request, "Message sent! The landlord will get back to you soon.")
            return redirect("contactlandlord", listing_pk=listing_pk)

    return render(request, "renteasyweb/contactlandlord.html", {
        "listing": listing,
        "landlord": landlord,
    })


def properties(request):
    return render(request, 'renteasyweb/properties.html')