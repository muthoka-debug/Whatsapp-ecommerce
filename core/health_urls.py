# core/health_urls.py
from django.urls import path
from django.http import JsonResponse
from django.db import connections
from django.db.utils import OperationalError

def health_check(request):
    """Health check endpoint for monitoring"""
    status = {
        'status': 'healthy',
        'database': 'connected'
    }
    
    # Check database connection
    try:
        connections['default'].cursor()
    except OperationalError:
        status['database'] = 'disconnected'
        status['status'] = 'unhealthy'
    
    return JsonResponse(status)

urlpatterns = [
    path('', health_check, name='health-check'),
]