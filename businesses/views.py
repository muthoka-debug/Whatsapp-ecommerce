from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from .models import Business
from .serializers import BusinessSerializer, BusinessCreateSerializer
from accounts.models import UserBusinessMembership
from accounts.serializers import UserBusinessMembershipSerializer
from core.permissions import IsBusinessOwner, IsBusinessManagerOrOwner, IsBusinessMember

class BusinessViewSet(viewsets.ModelViewSet):
    """ViewSet for managing businesses.

    Note this viewset intentionally does *not* use the X-Business-ID header for
    authorization: listing your businesses and creating your first one both
    happen before any business context exists. Per-business role checks below
    resolve the caller's membership in the *target* business instead.
    """

    permission_classes = [IsAuthenticated]
    # For OpenAPI model/path-param resolution; get_queryset() serves the data.
    queryset = Business.objects.none()

    def get_queryset(self):
        # Users can only see businesses they are members of. Ordered because the
        # model has no default ordering: an unordered queryset lets the paginator
        # return the same business on two pages and drop another entirely.
        return Business.objects.filter(
            members__user=self.request.user,
            members__is_active=True
        ).distinct().order_by('name')

    def _membership_for(self, business, required_roles=None):
        """The caller's active membership in ``business``, or None.

        Previously these actions read request.current_business_membership, which
        describes whichever business the X-Business-ID header names. A user who
        owned business A could send `X-Business-ID: A` while calling
        /businesses/B/add_member/ and pass the owner check against the wrong
        business.
        """
        membership = UserBusinessMembership.objects.filter(
            user=self.request.user,
            business=business,
            is_active=True,
        ).first()

        if membership is None:
            return None
        if required_roles and membership.role not in required_roles:
            return None
        return membership

    def get_serializer_class(self):
        if self.action == 'create':
            return BusinessCreateSerializer
        return BusinessSerializer
    
    def perform_create(self, serializer):
        business = serializer.save(owner=self.request.user)
        # Automatically add owner as a member with OWNER role
        UserBusinessMembership.objects.create(
            user=self.request.user,
            business=business,
            role='OWNER',
            is_active=True
        )
    
    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        """Get all members of a business"""
        business = self.get_object()
        # Check permission - only business members can view members
        if not self._membership_for(business):
            return Response(
                {'error': 'You are not a member of this business'},
                status=status.HTTP_403_FORBIDDEN
            )

        members = UserBusinessMembership.objects.filter(
            business=business,
            is_active=True
        ).select_related('user')
        serializer = UserBusinessMembershipSerializer(members, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Add a member to the business - Only owners can do this"""
        business = self.get_object()

        # Check permission - only owners of *this* business can add members
        if not self._membership_for(business, ['OWNER']):
            return Response(
                {'error': 'Only business owners can add members'},
                status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get('user_id')
        role = request.data.get('role', 'STAFF')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if role not in ['OWNER', 'MANAGER', 'STAFF', 'CASHIER']:
            return Response(
                {'error': 'Invalid role'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if already a member
        if UserBusinessMembership.objects.filter(user=user, business=business).exists():
            return Response(
                {'error': 'User is already a member of this business'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        new_membership = UserBusinessMembership.objects.create(
            user=user,
            business=business,
            role=role,
            is_active=True
        )
        
        serializer = UserBusinessMembershipSerializer(new_membership)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def update_member_role(self, request, pk=None):
        """Update a member's role - Only owners can do this"""
        business = self.get_object()

        # Check permission
        if not self._membership_for(business, ['OWNER']):
            return Response(
                {'error': 'Only business owners can update member roles'},
                status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get('user_id')
        new_role = request.data.get('role')
        
        if not user_id or not new_role:
            return Response(
                {'error': 'user_id and role are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_role not in ['OWNER', 'MANAGER', 'STAFF', 'CASHIER']:
            return Response(
                {'error': 'Invalid role'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            member = UserBusinessMembership.objects.get(
                user_id=user_id,
                business=business
            )
        except UserBusinessMembership.DoesNotExist:
            return Response(
                {'error': 'Member not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Prevent removing the last owner
        if member.role == 'OWNER' and new_role != 'OWNER':
            owner_count = UserBusinessMembership.objects.filter(
                business=business,
                role='OWNER',
                is_active=True
            ).count()
            if owner_count <= 1:
                return Response(
                    {'error': 'Cannot remove the last business owner'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        member.role = new_role
        member.save()
        
        serializer = UserBusinessMembershipSerializer(member)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Remove a member from the business - Only owners can do this"""
        business = self.get_object()

        # Check permission
        if not self._membership_for(business, ['OWNER']):
            return Response(
                {'error': 'Only business owners can remove members'},
                status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get('user_id')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            member = UserBusinessMembership.objects.get(
                user_id=user_id,
                business=business
            )
        except UserBusinessMembership.DoesNotExist:
            return Response(
                {'error': 'Member not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Prevent removing self
        if member.user_id == request.user.id:
            return Response(
                {'error': 'You cannot remove yourself from the business'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Prevent removing the last owner
        if member.role == 'OWNER':
            owner_count = UserBusinessMembership.objects.filter(
                business=business,
                role='OWNER',
                is_active=True
            ).count()
            if owner_count <= 1:
                return Response(
                    {'error': 'Cannot remove the last business owner'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        member.is_active = False
        member.save()
        
        return Response({'message': 'Member removed successfully'})