"""
Tests for Messages and Notifications workflow.
Run with: python manage.py test frontend_views.tests_messages_notifications
"""
from django.test import TestCase, Client
from django.urls import reverse

from .models import User, Property, Unit, Lease, Message, Notification, MaintenanceRequest


class MessagesNotificationsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.landlord = User.objects.create_user(
            email="landlord@test.com", password="testpass123",
            name="Landlord", role="landlord"
        )
        self.tenant1 = User.objects.create_user(
            email="tenant1@test.com", password="testpass123",
            name="Tenant One", role="tenant"
        )
        self.tenant2 = User.objects.create_user(
            email="tenant2@test.com", password="testpass123",
            name="Tenant Two", role="tenant"
        )
        self.prop = Property.objects.create(
            landlord=self.landlord, name="Test Property",
            address="123 Test St", property_type="apartment",
            monthly_rent=10000, rooms_total=2
        )
        self.unit1 = Unit.objects.create(
            property=self.prop, unit_number="Room 1",
            monthly_rent=10000, status="occupied"
        )
        self.unit2 = Unit.objects.create(
            property=self.prop, unit_number="Room 2",
            monthly_rent=10000, status="occupied"
        )
        Lease.objects.create(
            property=self.prop, unit=self.unit1, tenant=self.tenant1,
            status="active", monthly_rent=10000, room_label="Room 1"
        )
        Lease.objects.create(
            property=self.prop, unit=self.unit2, tenant=self.tenant2,
            status="active", monthly_rent=10000, room_label="Room 2"
        )

    def test_landlord_messages_page_loads(self):
        self.client.login(username="landlord@test.com", password="testpass123")
        r = self.client.get(reverse("landmessages"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Tenants", r.content.decode())
        self.assertIn("Message all tenants", r.content.decode())

    def test_landlord_1on1_message(self):
        self.client.login(username="landlord@test.com", password="testpass123")
        r = self.client.post(
            reverse("landmessages") + f"?with={self.tenant1.id}",
            {"body": "Hello tenant", "action": "single"},
            follow=True
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Message.objects.filter(sender=self.landlord, recipient=self.tenant1).count(), 1)

    def test_landlord_message_all_tenants(self):
        self.client.login(username="landlord@test.com", password="testpass123")
        r = self.client.post(
            reverse("landmessages"),
            {"body": "Hello everyone", "action": "message_all"},
            follow=True
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Message.objects.filter(sender=self.landlord, body="Hello everyone").count(), 2)

    def test_message_history_displayed(self):
        Message.objects.create(sender=self.landlord, recipient=self.tenant1, body="Test msg")
        self.client.login(username="landlord@test.com", password="testpass123")
        r = self.client.get(reverse("landmessages") + f"?with={self.tenant1.id}")
        self.assertIn("Test msg", r.content.decode())

    def test_landlord_notifications_page_loads(self):
        self.client.login(username="landlord@test.com", password="testpass123")
        r = self.client.get(reverse("landnotifications"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("System Notifications", r.content.decode())

    def test_tenant_profile_update_notifies_landlord(self):
        self.client.login(username="tenant1@test.com", password="testpass123")
        self.client.post(reverse("profile"), {
            "name": "Tenant One Updated", "email": "tenant1@test.com", "phone": ""
        })
        notifs = Notification.objects.filter(user=self.landlord)
        self.assertTrue(notifs.filter(message__icontains="updated their profile").exists())

    def test_maintenance_request_notifies_landlord(self):
        self.client.login(username="tenant1@test.com", password="testpass123")
        self.client.post(reverse("maintenance"), {
            "issue_category": "plumbing", "urgency": "High", "body": "Leak in bathroom"
        })
        self.assertTrue(
            Notification.objects.filter(user=self.landlord, message__icontains="maintenance").exists()
        )
