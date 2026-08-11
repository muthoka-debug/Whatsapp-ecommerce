from rest_framework import serializers
from .models import Business
from accounts.serializers import UserSerializer

class BusinessSerializer(serializers.ModelSerializer):
    owner_details = UserSerializer(source='owner', read_only=True)
    business_type_display = serializers.CharField(source='get_business_type_display', read_only=True)
    member_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Business
        fields = ['id', 'name', 'slug', 'logo', 'phone', 'email', 'address', 
                 'currency', 'business_type', 'business_type_display', 
                 'owner', 'owner_details', 'member_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']
    
    def get_member_count(self, obj) -> int:
        return obj.members.filter(is_active=True).count()

class BusinessCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = ['name', 'logo', 'phone', 'email', 'address', 'currency', 'business_type']