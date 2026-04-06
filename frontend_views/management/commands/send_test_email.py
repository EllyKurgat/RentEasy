from django.core.management.base import BaseCommand
from django.core.mail import send_mail, get_connection
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

        self.stdout.write("Testing email configuration...")
        self.stdout.write(f"EMAIL_BACKEND: {settings.EMAIL_BACKEND}")
        
        if settings.EMAIL_BACKEND == "sendgrid_backend.SendgridBackend":
            self.stdout.write(f"SENDGRID_API_KEY: {'***' if getattr(settings, 'SENDGRID_API_KEY', None) else 'Not set'}")
        else:
            self.stdout.write(f"EMAIL_HOST: {getattr(settings, 'EMAIL_HOST', 'Not set')}")
            self.stdout.write(f"EMAIL_PORT: {getattr(settings, 'EMAIL_PORT', 'Not set')}")
            self.stdout.write(f"EMAIL_HOST_USER: {getattr(settings, 'EMAIL_HOST_USER', 'Not set')}")
            self.stdout.write(f"EMAIL_USE_TLS: {getattr(settings, 'EMAIL_USE_TLS', 'Not set')}")

        self.stdout.write(f"DEFAULT_FROM_EMAIL: {getattr(settings, 'DEFAULT_FROM_EMAIL', 'Not set')}")

        try:
            # Test connection first
            connection = get_connection()
            connection.open()
            self.stdout.write(self.style.SUCCESS("✓ SMTP connection successful"))
            connection.close()

            # Send test email
            send_mail(
                subject="RentEasy Test Email",
                message=f"""This is a test email from RentEasy.

Configuration Details:
- Backend: {settings.EMAIL_BACKEND}
{'- SendGrid API: Configured' if settings.EMAIL_BACKEND == 'sendgrid_backend.SendgridBackend' else f''}
- Host: {getattr(settings, 'EMAIL_HOST', 'N/A')}
- Port: {getattr(settings, 'EMAIL_PORT', 'N/A')}
- User: {getattr(settings, 'EMAIL_HOST_USER', 'N/A')}
- TLS: {getattr(settings, 'EMAIL_USE_TLS', 'N/A')}

If you received this, your email configuration is working correctly!

Sent from: {settings.DEFAULT_FROM_EMAIL}""",
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
                    f"✗ Email failed: {str(e)}"
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    "Common solutions:"
                )
            )
            self.stdout.write("  1. Check if Gmail app password is correct (no spaces)")
            self.stdout.write("  2. Try SendGrid instead: https://sendgrid.com")
            self.stdout.write("  3. Verify environment variables in Render dashboard")
            self.stdout.write("  4. Check Render logs for SMTP connection errors")
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to send test email: {str(e)}"
                )
            )
