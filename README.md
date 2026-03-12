# 🏠 RentEasy — Rental Management System

A full-featured **Rental Management System** built with Django, designed for the Kenyan rental market. RentEasy streamlines property management for landlords and simplifies the rental experience for tenants — from listing properties and managing leases to collecting rent via M-Pesa.

![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-6.0-green?logo=django&logoColor=white)
![M-Pesa](https://img.shields.io/badge/M--Pesa-Integrated-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features

### For Landlords
- **Property Management** — Add, edit, and view residential/commercial properties with units
- **Tenant Management** — Add tenants directly, invite via email, or onboard through applications
- **Lease Workflow** — Full lifecycle management: Draft → Pending Signature → Active → Expiring → Expired/Terminated
  - Upload paper lease agreements (PDF, DOC, images)
  - Digital tenant acceptance/signature
  - Auto-detection of expiring and expired leases
  - Lease renewal and termination
- **Listings & Marketplace** — Create public listings for vacant units with photos, amenities, and availability
- **Application Processing** — Review, approve, or reject tenant applications
- **Earnings & Payments** — Track rent payments, view monthly income, and manage payment methods
- **Payment Methods** — Configure M-Pesa Paybill, Till, Send Money, Pochi La Biashara, or Bank Transfer
- **Maintenance Tracking** — Receive, assign, track, and resolve maintenance requests
- **Reports & Analytics** — Occupancy rates, income trends, maintenance stats, and per-property breakdowns
- **Notifications** — Real-time notification bell with unread count badge
- **Messaging** — In-app messaging with tenants via conversations

### For Tenants
- **Dashboard** — At-a-glance view of payment status, lease info, and recent activity
- **My Rental** — View property details, lease terms, and download agreement files
- **Lease Review & Signing** — Review and digitally accept or decline pending lease agreements
- **Rent Payments** — Pay rent via M-Pesa STK Push with automatic confirmation
- **Maintenance Requests** — Submit requests with category, urgency, description, and images
- **Notifications & Messages** — Stay informed and communicate with your landlord

### Public Facing
- **Landing Page** — Modern, responsive homepage with property highlights
- **Property Listings** — Browse available rentals with search, filters, and reviews
- **Contact Landlord** — Enquiry form with email notifications to property owners
- **About & Contact** — Informational pages

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12+, Django 6.0 |
| **Database** | SQLite (development) — easily swappable to PostgreSQL |
| **Frontend** | HTML5, CSS3 (custom design system), Vanilla JavaScript |
| **Payments** | Safaricom M-Pesa Daraja API (STK Push) |
| **Email** | Gmail SMTP for notifications, invites, and password resets |
| **Auth** | Django custom user model with role-based access (landlord/tenant) |
| **Icons** | Font Awesome 6 |
| **Fonts** | Google Fonts (Poppins) |

---

## 📁 Project Structure

```
RMS/
├── manage.py
├── .env                    # Environment variables (not committed)
├── .gitignore
├── README.md
├── RMS/                    # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── frontend_views/         # Main application
│   ├── models.py           # User, Property, Unit, Lease, Payment, etc.
│   ├── views.py            # All view functions
│   ├── urls.py             # URL routing
│   ├── mpesa.py            # M-Pesa STK Push integration
│   ├── backends.py         # Custom auth backends
│   ├── Templates/          # HTML templates
│   │   ├── landlord/       # Landlord dashboard pages
│   │   ├── tenant/         # Tenant sidebar partial
│   │   ├── listings/       # Public listing pages
│   │   └── renteasyweb/    # Public-facing pages
│   ├── static/
│   │   ├── css/            # style.css, theme.css
│   │   ├── Js/             # theme.js
│   │   └── Img/            # Images and logos
│   └── migrations/
└── media/                  # User uploads (not committed)
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- pip

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/EllyKurgat/RentEasy.git
   cd RentEasy
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate    # Linux/Mac
   venv\Scripts\activate       # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install django django-ratelimit python-dotenv requests
   ```

4. **Set up environment variables**

   Create a `.env` file in the project root:
   ```env
   SECRET_KEY=your-django-secret-key
   DEBUG=True

   # Email (Gmail SMTP)
   EMAIL_HOST_USER=your-email@gmail.com
   EMAIL_HOST_PASSWORD=your-app-password

   # M-Pesa Daraja API
   MPESA_CONSUMER_KEY=your-consumer-key
   MPESA_CONSUMER_SECRET=your-consumer-secret
   MPESA_SHORTCODE=174379
   MPESA_PASSKEY=your-passkey
   MPESA_CALLBACK_BASE_URL=https://your-domain.com
   MPESA_ENVIRONMENT=sandbox
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create a superuser**
   ```bash
   python manage.py createsuperuser
   ```

7. **Start the development server**
   ```bash
   python manage.py runserver
   ```

8. **Visit** `http://127.0.0.1:8000/`

---

## ☁️ Deploying to Render (Blueprint)

This project includes a `render.yaml` blueprint file for one-click deployment to Render.

### Prerequisites
- A [Render](https://render.com) account
- A GitHub/GitLab repository with this code

### Quick Deploy

1. Go to the [Render Dashboard](https://dashboard.render.com)
2. Click **New +** → **Blueprint**
3. Connect your repository
4. Render will detect `render.yaml` and show the services to create:
   - **rms-db**: PostgreSQL database (free tier)
   - **rms**: Django web service (free tier)
5. Review and configure the environment variables:
   - `MPESA_CONSUMER_KEY`: Your M-Pesa consumer key
   - `MPESA_CONSUMER_SECRET`: Your M-Pesa consumer secret
   - `MPESA_CALLBACK_URL`: Your Render service URL + `/mpesa/callback/`
6. Click **Apply Blueprint**

### Environment Variables

After deployment, configure these in the Render dashboard:

| Variable | Description |
|----------|-------------|
| `DJANGO_SECRET_KEY` | Auto-generated (secure) |
| `DJANGO_DEBUG` | Set to `False` for production |
| `DJANGO_ALLOWED_HOSTS` | Your Render service URL |
| `MPESA_ENVIRONMENT` | `sandbox` or `production` |
| `MPESA_CONSUMER_KEY` | From Safaricom Developer Portal |
| `MPESA_CONSUMER_SECRET` | From Safaricom Developer Portal |
| `MPESA_SHORTCODE` | Your business shortcode |
| `MPESA_PASSKEY` | From Safaricom Developer Portal |
| `MPESA_CALLBACK_URL` | `https://your-service.onrender.com/mpesa/callback/` |

See `.env.production` for a complete reference template.

### First-Time Setup

After deployment, you may need to:
1. Create a superuser: Use Render's **Shell** to run:
   ```bash
   python manage.py createsuperuser
   ```

---

## 📋 Data Models

| Model | Description |
|-------|------------|
| **User** | Custom user with roles (landlord/tenant), email auth, avatars |
| **Property** | Rental properties with type, address, and landlord ownership |
| **Unit** | Individual units within properties (vacant/occupied/maintenance) |
| **Lease** | Full lifecycle: draft → pending_signature → active → expiring → expired/terminated |
| **Payment** | Rent payments linked to leases with M-Pesa integration |
| **MaintenanceRequest** | Categorized requests with urgency, assignment, and resolution tracking |
| **Listing** | Public marketplace listings with photos, amenities, and reviews |
| **Application** | Tenant applications with screening and approval workflow |
| **Notification** | In-app notifications with read/unread tracking |
| **Conversation/Message** | Real-time messaging between landlords and tenants |
| **MpesaTransaction** | STK Push request tracking with Safaricom callback handling |

---

## 🎨 Design

- **Color Palette**: White + Dark Navy (#1F2937) + Blue (#3B82F6)
- **Responsive**: Mobile-first with bottom navigation bar for mobile and full sidebar for desktop
- **Dark Mode**: Built-in theme toggle support
- **UI Components**: Custom design system with cards, badges, alerts, tables, buttons, and form styles

---

## 💳 M-Pesa Integration

RentEasy integrates with Safaricom's **Daraja API** for seamless rent collection:

1. Tenant initiates payment from the Pay Rent page
2. STK Push prompt is sent to the tenant's phone
3. Tenant enters M-Pesa PIN to confirm
4. Callback from Safaricom confirms payment automatically
5. Payment record is updated and landlord is notified

Supports both **sandbox** and **production** environments.

---

## 📄 License

This project is licensed under the MIT License.

---

## 👤 Author

**Elly Kurgat**
- GitHub: [@EllyKurgat](https://github.com/EllyKurgat)
- Email: renteasyk@gmail.com

---

> Built with ❤️ for the Kenyan rental market.
