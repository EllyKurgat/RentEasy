from django.urls import path
from . import views
from django.contrib.auth import views as auth_views


urlpatterns = [
    path('', views.home, name='home'),

    # Landlord Views
    path('landlord/landdash/', views.LandLord_dash, name='landdash'),
    path('landlord/landmessages/', views.LandLord_messages, name='landmessages'),
    path('landlord/landnotifications/', views.LandLord_notifications, name='landnotifications'),
    path('landlord/myearnings/', views.LandLord_earnings, name='myearnings'),
    path('landlord/payment-methods/', views.LandLord_payment_methods, name='landlord_payment_methods'),
    path('landlord/myproperties/', views.LandLord_properties, name='myproperties'),
    path('landlord/mytenants/', views.LandLord_tenants, name='mytenants'),
    path('landlord/tenants/add/', views.LandLord_add_tenant, name='landlord_add_tenant'),
    path('landlord/lease-action/', views.LandLord_lease_actions, name='lease_action'),
    path('landlord/leases/', views.LandLord_leases, name='landlord_leases'),
    path('landlord/leases/create/', views.LandLord_lease_create, name='landlord_lease_create'),
    path('landlord/leases/<int:pk>/', views.LandLord_lease_detail, name='landlord_lease_detail'),
    path('landlord/invite-tenant/', views.LandLord_invite_tenant, name='invite_tenant'),
    path('landlord/listings/', views.LandLord_listings, name='landlord_listings'),
    path('landlord/listings/create/', views.LandLord_create_listing, name='create_listing'),
    path('landlord/applications/', views.LandLord_applications, name='landlord_applications'),
    path('landlord/applications/<int:pk>/action/', views.LandLord_application_action, name='application_action'),
    path('landlord/maintenance/', views.LandLord_maintenance, name='landlord_maintenance'),
    path('landlord/maintenance/<int:pk>/', views.LandLord_maintenance_detail, name='landlord_maintenance_detail'),
    path('landlord/profile/', views.LandLord_profile, name='profile'),
    path('landlord/viewproperties/', views.LandLord_viewproperties, name='viewproperties'),
    path('landlord/reports/', views.LandLord_reports, name='landlord_reports'),

    path('invite/accept/<str:token>/', views.tenant_invite_accept, name='invite_accept'),
    path('listings/', views.listing_list, name='listing_list'),
    path('listings/<int:pk>/', views.listing_detail, name='listing_detail'),
    path('listings/<int:listing_pk>/apply/', views.application_create, name='application_create'),

    # General/User Views
    path('forgotpass/', views.forgotpass, name='forgotpass'),
    path('user_login/', views.user_login, name='user_login'),
    path('maintenance/', views.maintenance, name='maintenance'),
    path('message/', views.message, name='message'),
    path('message/send/', views.message_send, name='message_send'),
    path('message/poll/', views.message_poll, name='message_poll'),
    path('myrental/', views.myrental, name='myrental'),
    path('lease/review/', views.tenant_lease_review, name='tenant_lease_review'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/mark_read/<int:pk>/', views.notification_mark_read, name='notification_mark_read'),
    path('notifications/delete/<int:pk>/', views.notification_delete, name='notification_delete'),
    path('notifications/unread-count/', views.notification_unread_count, name='notification_unread_count'),
    path('payrent/', views.payrent, name='payrent'),
    path('mpesa/callback/', views.mpesa_callback, name='mpesa_callback'),
    path('mpesa/status/', views.mpesa_check_status, name='mpesa_check_status'),
    path('profile/', views.profile, name='profile'),
    path('register/', views.register, name='register'),
    path('rentspay/', views.rentspay, name='rentspay'),
    path('updatepass/', views.updatepass, name='updatepass'),
    path('reset/<str:uidb64>/<str:token>/', views.password_reset_confirm, name='password_reset_confirm'),
    path('userdash/', views.userdash, name='userdash'),
    path('logout/', auth_views.LogoutView.as_view(next_page='home'), name='logout'), 
    
    # Static/informational pages
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('contactlandlord/<int:listing_pk>/', views.contactlandlord, name='contactlandlord'),
    path('index/', views.home, name='index'),
    path('properties/', views.properties, name='properties'),
]
