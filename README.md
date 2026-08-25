# WhatsApp E-commerce Platform

A Django-based commerce and business-management platform that connects online ordering with WhatsApp communication, payments, notifications, delivery workflows and business analytics.

## Overview

This project explores a real-world commerce workflow for businesses that want to manage products, customers and orders while using WhatsApp as a customer communication channel.

The application is structured as a modular Django system with separate domains for businesses, customers, products, orders, payments, deliveries, notifications, analytics and automation.

## Key Features

- Business/account management
- Product catalogue management
- Customer management
- Order lifecycle management
- WhatsApp Business API integration points
- M-Pesa/Daraja payment integration points
- Payment credential encryption
- Delivery management
- Notifications and SMS workflows
- Background jobs with Celery and Redis
- Analytics and reporting
- Automation workflows
- JWT authentication
- REST APIs
- OpenAPI documentation with drf-spectacular

## Architecture

```text
Customer
   |
   v
Frontend / WhatsApp
   |
   v
Django Application
   |
   +--> Businesses
   +--> Products
   +--> Orders
   +--> Customers
   +--> Payments
   +--> Deliveries
   +--> Notifications
   +--> Analytics
   +--> Automation
   |
   +--> PostgreSQL
   +--> Redis --> Celery Workers
   |
   +--> WhatsApp Business API
   +--> M-Pesa / Daraja
   +--> SMS Provider
```

## Technology Stack

| Area | Technology |
|---|---|
| Backend | Python, Django, Django REST Framework |
| Authentication | JWT |
| Database | PostgreSQL |
| Async processing | Celery, Redis |
| API documentation | drf-spectacular / OpenAPI |
| Payments | M-Pesa Daraja integration |
| Messaging | WhatsApp Business API |
| SMS | Twilio |
| Security | Cryptography / environment-based secrets |
| Frontend | HTML, CSS, JavaScript / Django templates |

## Project Structure

```text
businesses/       Business management
customers/        Customer management
products/         Product catalogue
orders/           Order management
payments/         Payment workflows
deliveries/       Delivery workflows
notifications/   Notification services
analytics/        Business analytics
automation/       Automation workflows
accounts/         Authentication and accounts
frontend/         Frontend application
Storefront/       Storefront functionality
core/             Shared application configuration
```

## Local Development

### Clone

```bash
git clone https://github.com/Eunice-ctrlz/Whatsapp-ecommerce.git
cd Whatsapp-ecommerce
```

### Create a virtual environment

```bash
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
pip install -r requirements.txt
```

### Configure environment variables

Copy `.env.example` to `.env` and provide local values:

```bash
cp .env.example .env
```

On Windows, copy the file manually or use:

```powershell
Copy-Item .env.example .env
```

Do not commit `.env` or real API credentials.

### Run migrations

```bash
python manage.py migrate
```

### Start Django

```bash
python manage.py runserver
```

### Start Redis/Celery

Run Redis locally, then start a Celery worker using the project's configured Celery application:

```bash
celery -A <celery_app> worker -l info
```

Replace `<celery_app>` with the Celery application module used by the project.

## API Documentation

When the application is running, the project exposes OpenAPI documentation through the configured drf-spectacular endpoints. Check the project's URL configuration for the exact documentation routes.

## Security

- Never commit `.env` files or production credentials.
- Keep WhatsApp and M-Pesa credentials in environment variables or secure secret storage.
- Rotate credentials if they have ever been exposed.
- Validate webhook signatures and external callback payloads in production.
- Use HTTPS for production API and webhook endpoints.
- Apply least-privilege access to business and customer data.

## Testing

Before deployment, run the Django test suite:

```bash
python manage.py test
```

Add integration tests for payment callbacks, WhatsApp webhooks, order state transitions and asynchronous jobs before treating the system as production-ready.

## Roadmap

- Production WhatsApp webhook handling
- Reliable payment reconciliation
- Delivery-provider integrations
- AI sales assistant
- Advanced sales automation
- Customer segmentation
- Automated deployment and CI/CD
- Expanded API test coverage

## Author

**Eunice Muturi**

GitHub: https://github.com/Eunice-ctrlz
