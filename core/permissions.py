from rest_framework.permissions import SAFE_METHODS, BasePermission

# Ordered least- to most-privileged.
ROLE_HIERARCHY = ['CASHIER', 'STAFF', 'MANAGER', 'OWNER']

_UNRESOLVED = object()


def get_membership(request):
    """The caller's active membership in the business from X-Business-ID.

    Resolved here rather than in BusinessMiddleware: DRF's TokenAuthentication
    runs inside the view, so during middleware ``request.user`` is still
    AnonymousUser and every token-authenticated request would look unauthorized.

    The result is cached on the underlying HttpRequest, so repeated permission
    checks in one request cost a single query.
    """
    # DRF wraps the Django request; cache on the wrapped object so the value is
    # shared between the DRF Request and the HttpRequest views may also touch.
    holder = getattr(request, '_request', request)

    cached = getattr(holder, '_resolved_business_membership', _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cached

    membership = None
    business = getattr(request, 'current_business', None)
    user = getattr(request, 'user', None)

    if business is not None and user is not None and user.is_authenticated:
        from accounts.models import UserBusinessMembership

        membership = UserBusinessMembership.objects.filter(
            user=user,
            business=business,
            is_active=True,
        ).select_related('business').first()

    holder._resolved_business_membership = membership
    # Keep the legacy attribute in step for code that reads it directly.
    holder.current_business_membership = membership
    return membership


class BaseBusinessRolePermission(BasePermission):
    """Grants access when the caller holds one of ``allowed_roles``.

    Also enforces tenant isolation at the object level: an object belonging to a
    different business than the active one is never visible, even if the view's
    queryset forgot to filter.
    """

    allowed_roles = ()
    message = 'You do not have permission to perform this action for this business.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        membership = get_membership(request)
        if not membership or not membership.is_active:
            return False

        return membership.role in self.allowed_roles

    def has_object_permission(self, request, view, obj):
        business = getattr(request, 'current_business', None)
        obj_business_id = getattr(obj, 'business_id', None)
        if obj_business_id is not None and business is not None:
            if obj_business_id != business.id:
                return False
        return True


class IsBusinessMember(BaseBusinessRolePermission):
    """Any active member of the business. The baseline for read access."""

    allowed_roles = tuple(ROLE_HIERARCHY)


class IsCashierOrAbove(BaseBusinessRolePermission):
    """Cashiers and above. Used for payment processing."""

    allowed_roles = ('CASHIER', 'STAFF', 'MANAGER', 'OWNER')


class IsBusinessStaffOrAbove(BaseBusinessRolePermission):
    """Staff and above. Day-to-day operations such as processing orders."""

    allowed_roles = ('STAFF', 'MANAGER', 'OWNER')


class IsBusinessManagerOrOwner(BaseBusinessRolePermission):
    """Managers and owners. Management operations."""

    allowed_roles = ('MANAGER', 'OWNER')


class IsBusinessOwner(BaseBusinessRolePermission):
    """Owners only. Full access."""

    allowed_roles = ('OWNER',)


class IsOwnerOrReadOnly(BaseBusinessRolePermission):
    """Any member may read; only owners may write."""

    allowed_roles = tuple(ROLE_HIERARCHY)

    def has_object_permission(self, request, view, obj):
        if not super().has_object_permission(request, view, obj):
            return False

        if request.method in SAFE_METHODS:
            return True

        membership = get_membership(request)
        return bool(membership and membership.role == 'OWNER')
