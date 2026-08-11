from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate, login, logout
from .models import User, UserBusinessMembership
from .serializers import UserSerializer, RegisterSerializer, LoginSerializer, UserBusinessMembershipSerializer
from core.permissions import IsBusinessOwner

class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer
    # Rate limited by the 'auth' scope so the endpoint cannot be used to
    # enumerate or mass-create accounts.
    throttle_scope = 'auth'


    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            token, created = Token.objects.get_or_create(user=user)
            return Response({
                'user': UserSerializer(user).data,
                'token': token.key
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LoginView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer
    # Brute-force protection: caps password attempts per client.
    throttle_scope = 'auth'


    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = authenticate(
                username=serializer.validated_data['username'],
                password=serializer.validated_data['password']
            )
            if user:
                login(request, user)
                token, created = Token.objects.get_or_create(user=user)
                # Get user's businesses
                memberships = UserBusinessMembership.objects.filter(user=user, is_active=True)
                return Response({
                    'user': UserSerializer(user).data,
                    'token': token.key,
                    'businesses': UserBusinessMembershipSerializer(memberships, many=True).data
                })
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

class LogoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    # Takes no body and returns a bare message; declared so the endpoint still
    # appears in the OpenAPI schema.
    serializer_class = serializers.Serializer

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        logout(request)
        return Response({'message': 'Logged out successfully'})

class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer
    
    def get_object(self):
        return self.request.user

class UserBusinessesView(generics.ListAPIView):
    """Get all businesses a user belongs to"""
    permission_classes = [IsAuthenticated]
    serializer_class = UserBusinessMembershipSerializer
    
    def get_queryset(self):
        return UserBusinessMembership.objects.filter(
            user=self.request.user, 
            is_active=True
        ).select_related('business')