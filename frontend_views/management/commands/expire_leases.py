"""
Management command to expire leases whose end_date has passed.

Usage:
    python manage.py expire_leases
    python manage.py expire_leases --dry-run

Schedule daily via cron / Windows Task Scheduler:
    0 1 * * * /path/to/venv/bin/python /path/to/manage.py expire_leases
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from frontend_views.models import Lease, Listing, Notification, Unit


class Command(BaseCommand):
    help = "Expire leases whose end_date has passed and free up their units."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be expired without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        today = timezone.now().date()

        expired_leases = Lease.objects.filter(
            status="active",
            end_date__isnull=False,
            end_date__lt=today,
        ).select_related("tenant", "property", "unit")

        count = expired_leases.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No leases to expire."))
            return

        self.stdout.write(f"Found {count} expired lease(s).")

        for lease in expired_leases:
            label = f"Lease #{lease.id} – {lease.tenant.name} @ {lease.property.name}"
            if dry_run:
                self.stdout.write(f"  [DRY RUN] Would expire: {label}")
                continue

            lease.status = "expired"
            lease.save(update_fields=["status"])

            # Free up the unit
            if lease.unit:
                lease.unit.status = "vacant"
                lease.unit.save(update_fields=["status"])
                # Re-activate listing for the now-vacant unit
                Listing.objects.filter(unit=lease.unit, status="filled").update(status="active")

            # Notify tenant
            Notification.objects.create(
                user=lease.tenant,
                message=f"Your lease for {lease.property.name} has expired. Please contact your landlord to renew.",
            )

            # Notify landlord
            if lease.property.landlord:
                Notification.objects.create(
                    user=lease.property.landlord,
                    message=f"Lease for tenant {lease.tenant.name} at {lease.property.name} has expired.",
                )

            self.stdout.write(f"  Expired: {label}")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run complete. {count} lease(s) would be expired."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully expired {count} lease(s)."))
