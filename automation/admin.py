from django.contrib import admin

from .models import AutomationRun


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    """Read-only: these rows are an audit trail, not something to hand-edit."""

    list_display = ('event', 'status', 'business', 'subject_type', 'subject_id', 'created_at')
    list_filter = ('status', 'event', 'business')
    search_fields = ('subject_id', 'idempotency_key', 'error')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'business', 'event', 'subject_type', 'subject_id', 'idempotency_key',
        'status', 'payload', 'actions', 'error', 'created_at', 'completed_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
