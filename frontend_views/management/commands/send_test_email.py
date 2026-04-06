from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = "Send a test email to verify SMTP configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "email",
            type=str,
            help="Email address to send test email to",
        )

    def handle(self, *args, **options):
        test_email = options["email"]
        
        try:
            send_mail(
                subject="RentEasy Test Email",
                message=f"This is a test email from RentEasy.\n\nIf you received this, your email configuration is working correctly!\n\nFrom: {settings.DEFAULT_FROM_EMAIL}",
                from_email=None,  # Uses DEFAULT_FROM_EMAIL
                recipient_list=[test_email],
                fail_silently=False,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Test email sent successfully to {test_email}"
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to send test email: {str(e)}"
                )
            )
