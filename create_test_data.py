#!/usr/bin/env python
import os
import django
import sys

# Setup Django
sys.path.append('/home/joy/Desktop/RentEasy')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'RMS.settings')
django.setup()

from frontend_views.models import User, Property, Unit, Lease, LandlordPaymentMethod
from django.utils import timezone

def create_test_data():
    print("Creating test data...")

    # Create test landlord
    landlord = User.objects.filter(email='landlord@test.com').first()
    if not landlord:
        landlord = User.objects.create_user(
            email='landlord@test.com',
            password='test123',
            name='Test Landlord',
            phone='0712345678',
            role='landlord'
        )
        landlord.password_set = True
        landlord.save()
    print(f'✓ Landlord: {landlord.email}')

    # Create test tenant
    tenant = User.objects.filter(email='tenant@test.com').first()
    if not tenant:
        tenant = User.objects.create_user(
            email='tenant@test.com',
            password='test123',
            name='Test Tenant',
            phone='0723456789',
            role='tenant'
        )
        tenant.password_set = True
        tenant.save()
    print(f'✓ Tenant: {tenant.email}')

    # Create test property
    prop = Property.objects.filter(name='Test Property', landlord=landlord).first()
    if not prop:
        prop = Property.objects.create(
            name='Test Property',
            landlord=landlord,
            address='123 Test Street',
            property_type='apartment',
            rooms_total=5
        )
    print(f'✓ Property: {prop.name}')

    # Create test unit
    unit = Unit.objects.filter(property=prop, unit_number='A1').first()
    if not unit:
        unit = Unit.objects.create(
            property=prop,
            unit_number='A1',
            monthly_rent=15000,
            status='vacant'
        )
    print(f'✓ Unit: {unit.unit_number}')

    # Create test lease
    lease = Lease.objects.filter(property=prop, tenant=tenant).first()
    if not lease:
        lease = Lease.objects.create(
            property=prop,
            tenant=tenant,
            unit=unit,
            room_label=unit.unit_number,
            status='active',
            monthly_rent=15000,
            start_date=timezone.now().date(),
            end_date=timezone.now().date().replace(year=timezone.now().year + 1),
            rent_due_day=1
        )
        unit.status = 'occupied'
        unit.save()
    print(f'✓ Lease: {lease.id}')

    # Create M-Pesa payment method
    method = LandlordPaymentMethod.objects.filter(landlord=landlord, method_type='mpesa_paybill').first()
    if not method:
        method = LandlordPaymentMethod.objects.create(
            landlord=landlord,
            method_type='mpesa_paybill',
            display_name='M-Pesa Paybill',
            mpesa_number='174379',
            mpesa_account_number='TEST001',
            is_active=True
        )
    print(f'✓ Payment method: {method.display_name}')

    print('\n🎉 All test data created successfully!')
    print('\nTest Credentials:')
    print('Landlord: landlord@test.com / test123')
    print('Tenant: tenant@test.com / test123')

if __name__ == '__main__':
    create_test_data()