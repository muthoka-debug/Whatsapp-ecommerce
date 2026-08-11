from datetime import datetime, timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AIInsight, AnalyticsMetric, SalesReport

# The dashboard actions return ad-hoc aggregate dicts rather than a model
# serializer. Without this the whole viewset is dropped from the OpenAPI schema,
# which would hide the endpoints the frontend dashboard is built on.
dashboard_schema = extend_schema(responses={200: OpenApiTypes.OBJECT})
from .serializers import (
    AnalyticsMetricSerializer, AnalyticsEventSerializer,
    SalesReportSerializer, AIInsightSerializer
)
from core.permissions import IsBusinessMember, IsBusinessManagerOrOwner
from orders.models import Order
from payments.models import Payment
from customers.models import Customer
from products.models import Product
import json

class DashboardViewSet(viewsets.GenericViewSet):
    """ViewSet for dashboard data"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get dashboard summary stats"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Date range - today
        today = timezone.now().date()
        start_of_day = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        
        # Today's stats
        today_orders = Order.objects.filter(
            business=business,
            placed_at__gte=start_of_day,
            status__in=['PAID', 'DELIVERED']
        )
        
        today_revenue = today_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        today_order_count = today_orders.count()
        
        # Pending orders
        pending_orders = Order.objects.filter(
            business=business,
            status__in=['PENDING', 'CONFIRMED', 'PROCESSING']
        ).count()
        
        # Total customers
        total_customers = Customer.objects.filter(business=business).count()
        
        # Total products
        total_products = Product.objects.filter(business=business, is_active=True).count()
        
        # Low stock products
        low_stock_products = Product.objects.filter(
            business=business,
            is_active=True,
            stock__lte=F('low_stock_threshold')
        ).count()
        
        # Pending payments
        pending_payments = Payment.objects.filter(
            business=business,
            status__in=['PENDING', 'PROCESSING']
        ).count()
        
        # Recent orders (last 10)
        recent_orders = Order.objects.filter(business=business)[:10]
        from orders.serializers import OrderSerializer
        recent_orders_data = OrderSerializer(recent_orders, many=True).data
        
        # Calculate growth (compare with yesterday)
        yesterday = today - timedelta(days=1)
        start_of_yesterday = timezone.make_aware(datetime.combine(yesterday, datetime.min.time()))
        
        yesterday_orders = Order.objects.filter(
            business=business,
            placed_at__gte=start_of_yesterday,
            placed_at__lt=start_of_day,
            status__in=['PAID', 'DELIVERED']
        )
        yesterday_revenue = yesterday_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        yesterday_order_count = yesterday_orders.count()
        
        revenue_growth = 0
        order_growth = 0
        
        if yesterday_revenue > 0:
            revenue_growth = ((today_revenue - yesterday_revenue) / yesterday_revenue) * 100
        
        if yesterday_order_count > 0:
            order_growth = ((today_order_count - yesterday_order_count) / yesterday_order_count) * 100
        
        return Response({
            'today': {
                'revenue': today_revenue,
                'orders': today_order_count,
                'revenue_growth': round(revenue_growth, 2),
                'order_growth': round(order_growth, 2),
            },
            'pending': {
                'orders': pending_orders,
                'payments': pending_payments,
            },
            'inventory': {
                'total_products': total_products,
                'low_stock': low_stock_products,
            },
            'customers': {
                'total': total_customers,
            },
            'recent_orders': recent_orders_data,
        })
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def revenue_chart(self, request):
        """Get revenue data for charts"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get period (default: last 30 days)
        period = request.query_params.get('period', '30')
        try:
            days = int(period)
        except ValueError:
            days = 30
        
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)
        
        # Get daily revenue
        from django.db import connection
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    DATE(placed_at) as date,
                    COUNT(*) as order_count,
                    SUM(total_amount) as revenue
                FROM orders_order
                WHERE business_id = %s
                    AND placed_at >= %s
                    AND placed_at <= %s
                    AND status IN ('PAID', 'DELIVERED')
                GROUP BY DATE(placed_at)
                ORDER BY date ASC
            """, [business.id, start_date, end_date])
            
            rows = cursor.fetchall()
        
        # Fill in missing dates with zeros
        date_range = [(start_date + timedelta(days=i)) for i in range((end_date - start_date).days + 1)]
        data_by_date = {row[0].strftime('%Y-%m-%d'): {'orders': row[1], 'revenue': float(row[2])} for row in rows}
        
        chart_data = []
        for date in date_range:
            date_str = date.strftime('%Y-%m-%d')
            if date_str in data_by_date:
                chart_data.append({
                    'date': date_str,
                    'orders': data_by_date[date_str]['orders'],
                    'revenue': data_by_date[date_str]['revenue']
                })
            else:
                chart_data.append({
                    'date': date_str,
                    'orders': 0,
                    'revenue': 0
                })
        
        return Response({
            'data': chart_data,
            'start_date': start_date,
            'end_date': end_date,
            'total_orders': sum(d['orders'] for d in chart_data),
            'total_revenue': sum(d['revenue'] for d in chart_data)
        })
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def sales_by_source(self, request):
        """Get sales breakdown by source"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        sources = Order.objects.filter(
            business=business,
            status__in=['PAID', 'DELIVERED']
        ).values('source').annotate(
            total_orders=Count('id'),
            total_revenue=Sum('total_amount')
        )
        
        return Response(sources)
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def sales_by_payment_method(self, request):
        """Get sales breakdown by payment method"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        payments = Payment.objects.filter(
            business=business,
            status='COMPLETED'
        ).values('method').annotate(
            total_payments=Count('id'),
            total_amount=Sum('amount')
        )
        
        return Response(payments)
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def top_products(self, request):
        """Get top selling products"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        limit = request.query_params.get('limit', 10)
        
        from orders.models import OrderItem
        top_products = OrderItem.objects.filter(
            business=business,
            order__status__in=['PAID', 'DELIVERED']
        ).values('product', 'product__name', 'product__price').annotate(
            total_sold=Sum('quantity'),
            total_revenue=Sum('total')
        ).order_by('-total_sold')[:limit]
        
        return Response(top_products)
    
    @dashboard_schema
    @action(detail=False, methods=['get'])
    def recent_activity(self, request):
        """Get recent platform activity"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        limit = int(request.query_params.get('limit', 10))
        
        # Get recent events
        recent_orders = Order.objects.filter(business=business)[:limit]
        recent_payments = Payment.objects.filter(business=business)[:limit]
        recent_customers = Customer.objects.filter(business=business).order_by('-created_at')[:limit]
        
        from orders.serializers import OrderSerializer
        from payments.serializers import PaymentSerializer
        from customers.serializers import CustomerSerializer
        
        return Response({
            'recent_orders': OrderSerializer(recent_orders, many=True).data,
            'recent_payments': PaymentSerializer(recent_payments, many=True).data,
            'recent_customers': CustomerSerializer(recent_customers, many=True).data,
        })


class AnalyticsMetricViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing analytics metrics"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = AnalyticsMetricSerializer
    queryset = AnalyticsMetric.objects.none()  # OpenAPI model resolution
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return AnalyticsMetric.objects.none()
        
        queryset = AnalyticsMetric.objects.filter(business=business)
        
        # Filter by metric type
        metric_type = self.request.query_params.get('metric_type')
        if metric_type:
            queryset = queryset.filter(metric_type=metric_type)
        
        # Filter by period
        period = self.request.query_params.get('period')
        if period:
            queryset = queryset.filter(period=period)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(period_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(period_date__lte=end_date)
        
        return queryset


class SalesReportViewSet(viewsets.ModelViewSet):
    """ViewSet for sales reports"""
    permission_classes = [IsAuthenticated, IsBusinessManagerOrOwner]
    serializer_class = SalesReportSerializer
    queryset = SalesReport.objects.none()  # OpenAPI model resolution
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return SalesReport.objects.none()
        return SalesReport.objects.filter(business=business)
    
    def perform_create(self, serializer):
        serializer.save(
            business=self.request.current_business,
            generated_by=self.request.user
        )
    
    @action(detail=False, methods=['post'])
    def generate(self, request):
        """Generate a new sales report"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        report_type = request.data.get('report_type', 'DAILY')
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')
        
        if not start_date or not end_date:
            # Calculate based on report type
            today = timezone.now().date()
            if report_type == 'DAILY':
                start_date = today
                end_date = today
            elif report_type == 'WEEKLY':
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif report_type == 'MONTHLY':
                start_date = today.replace(day=1)
                end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)
            else:
                return Response(
                    {'error': 'Invalid report type'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'error': 'Invalid date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Generate report data
        orders = Order.objects.filter(
            business=business,
            placed_at__date__gte=start_date,
            placed_at__date__lte=end_date,
            status__in=['PAID', 'DELIVERED']
        )
        
        total_revenue = orders.aggregate(total=Sum('total_amount'))['total'] or 0
        total_orders = orders.count()
        
        # Get unique customers
        total_customers = orders.values('customer').distinct().count()
        
        # Average order value
        avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
        
        # Orders by status
        orders_by_status = list(Order.objects.filter(
            business=business,
            placed_at__date__gte=start_date,
            placed_at__date__lte=end_date
        ).values('status').annotate(count=Count('id')))
        
        # Orders by source
        orders_by_source = list(Order.objects.filter(
            business=business,
            placed_at__date__gte=start_date,
            placed_at__date__lte=end_date
        ).values('source').annotate(count=Count('id')))
        
        # Payments by method
        payments_by_method = list(Payment.objects.filter(
            business=business,
            completed_at__date__gte=start_date,
            completed_at__date__lte=end_date,
            status='COMPLETED'
        ).values('method').annotate(
            count=Count('id'),
            total=Sum('amount')
        ))
        
        # Top products
        from orders.models import OrderItem
        top_products = list(OrderItem.objects.filter(
            business=business,
            order__placed_at__date__gte=start_date,
            order__placed_at__date__lte=end_date,
            order__status__in=['PAID', 'DELIVERED']
        ).values('product__name', 'product__id').annotate(
            total_sold=Sum('quantity'),
            total_revenue=Sum('total')
        ).order_by('-total_sold')[:10])
        
        # Top customers
        top_customers = list(orders.values('customer__name', 'customer__id').annotate(
            total_orders=Count('id'),
            total_spent=Sum('total_amount')
        ).order_by('-total_spent')[:10])
        
        # Revenue by day
        revenue_by_day = list(orders.extra(
            select={'date': 'DATE(placed_at)'}
        ).values('date').annotate(
            revenue=Sum('total_amount'),
            orders=Count('id')
        ).order_by('date'))
        
        # Create report
        report = SalesReport.objects.create(
            business=business,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            generated_by=request.user,
            total_revenue=total_revenue,
            total_orders=total_orders,
            total_customers=total_customers,
            average_order_value=avg_order_value,
            orders_by_status=orders_by_status,
            orders_by_source=orders_by_source,
            payments_by_method=payments_by_method,
            top_products=top_products,
            top_customers=top_customers,
            revenue_by_day=revenue_by_day
        )
        
        # TODO: Generate PDF/Excel exports
        
        serializer = self.get_serializer(report)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AIInsightViewSet(viewsets.ModelViewSet):
    """ViewSet for AI insights"""
    permission_classes = [IsAuthenticated, IsBusinessMember]
    serializer_class = AIInsightSerializer
    queryset = AIInsight.objects.none()  # OpenAPI model resolution
    
    def get_queryset(self):
        business = self.request.current_business
        if not business:
            return AIInsight.objects.none()
        
        queryset = AIInsight.objects.filter(business=business)
        
        # Filter by insight type
        insight_type = self.request.query_params.get('insight_type')
        if insight_type:
            queryset = queryset.filter(insight_type=insight_type)
        
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # Filter unread
        unread = self.request.query_params.get('unread')
        if unread == 'true':
            queryset = queryset.filter(is_read=False)
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(business=self.request.current_business)
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark insight as read"""
        insight = self.get_object()
        insight.is_read = True
        insight.read_at = timezone.now()
        insight.save()
        serializer = self.get_serializer(insight)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def generate_insights(self, request):
        """Generate AI insights"""
        business = request.current_business
        if not business:
            return Response(
                {'error': 'Business not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # This is where AI logic would be implemented
        # For now, create sample insights
        insights = []
        
        # Check low stock products
        low_stock_products = Product.objects.filter(
            business=business,
            is_active=True,
            stock__lte=F('low_stock_threshold')
        )
        
        for product in low_stock_products[:5]:
            insight = AIInsight.objects.create(
                business=business,
                insight_type='INVENTORY_ALERT',
                title=f"Low Stock Alert: {product.name}",
                description=f"Product {product.name} has {product.stock} units remaining. Low stock threshold is {product.low_stock_threshold}.",
                priority='HIGH',
                data={'product_id': product.id, 'stock': product.stock},
                is_actionable=True,
                action_url=f"/products/{product.id}/"
            )
            insights.append(insight)
        
        # Check top selling products
        from orders.models import OrderItem
        top_products = OrderItem.objects.filter(
            business=business,
            order__status__in=['PAID', 'DELIVERED']
        ).values('product').annotate(
            total_sold=Sum('quantity')
        ).order_by('-total_sold')[:3]
        
        if top_products:
            product_ids = [p['product'] for p in top_products]
            products = Product.objects.filter(id__in=product_ids)
            product_names = [p.name for p in products]
            
            insight = AIInsight.objects.create(
                business=business,
                insight_type='PRODUCT_RECOMMENDATION',
                title="Top Selling Products",
                description=f"Your best selling products are: {', '.join(product_names)}. Consider promoting these more.",
                priority='MEDIUM',
                data={'products': [{'id': p.id, 'name': p.name} for p in products]},
                is_actionable=True,
                action_url="/analytics/"
            )
            insights.append(insight)
        
        # Check sales trend
        today = timezone.now().date()
        last_week = today - timedelta(days=7)
        last_week_orders = Order.objects.filter(
            business=business,
            placed_at__date__gte=last_week,
            placed_at__date__lte=today,
            status__in=['PAID', 'DELIVERED']
        )
        
        previous_week = last_week - timedelta(days=7)
        prev_week_orders = Order.objects.filter(
            business=business,
            placed_at__date__gte=previous_week,
            placed_at__date__lt=last_week,
            status__in=['PAID', 'DELIVERED']
        )
        
        current_revenue = last_week_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        previous_revenue = prev_week_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        
        if previous_revenue > 0:
            growth = ((current_revenue - previous_revenue) / previous_revenue) * 100
            if abs(growth) > 20:
                insight_type = 'SALES_TREND' if growth > 0 else 'RISK_ALERT'
                title = "Sales Growth" if growth > 0 else "Sales Decline"
                priority = 'HIGH' if abs(growth) > 50 else 'MEDIUM'
                
                insight = AIInsight.objects.create(
                    business=business,
                    insight_type=insight_type,
                    title=f"{title}: {growth:.1f}% Change",
                    description=f"Your sales have {'increased' if growth > 0 else 'decreased'} by {abs(growth):.1f}% compared to the previous week.",
                    priority=priority,
                    data={'growth': growth, 'current_revenue': current_revenue, 'previous_revenue': previous_revenue},
                    is_actionable=True,
                    action_url="/analytics/"
                )
                insights.append(insight)
        
        serializer = self.get_serializer(insights, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)