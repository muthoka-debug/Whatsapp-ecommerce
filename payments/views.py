import logging

from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated

from .callbacks import is_authentic_callback
from .models import (
    MpesaTransaction,
    Payment,
    PaymentLog,
)

from .serializers import (
    PaymentSerializer,
    PaymentCreateSerializer,
    PaymentLogSerializer,
)

from core.permissions import IsBusinessMember, get_membership

logger = logging.getLogger(__name__)


class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing payments.

    Business members can view payments.
    Cashiers, staff, managers, and owners can process payments.
    Managers and owners can refund payments.
    """

    permission_classes = [
        IsAuthenticated,
        IsBusinessMember,
    ]

    # For OpenAPI model/path-param resolution; get_queryset() serves the data.
    queryset = Payment.objects.none()

    # =========================================================
    # QUERYSET
    # =========================================================

    def get_queryset(self):
        """
        Return only payments belonging to the current business.
        """

        business = getattr(
            self.request,
            "current_business",
            None
        )

        if not business:
            return Payment.objects.none()

        queryset = Payment.objects.filter(
            business=business
        )

        # -----------------------------------------------------
        # Filter by payment status
        # Example:
        # ?status=COMPLETED
        # ?status=COMPLETED,PROCESSING
        # -----------------------------------------------------

        status_filter = self.request.query_params.get(
            "status"
        )

        if status_filter:
            status_list = status_filter.split(",")

            queryset = queryset.filter(
                status__in=status_list
            )

        # -----------------------------------------------------
        # Filter by payment method
        # Example:
        # ?method=MPESA
        # -----------------------------------------------------

        method_filter = self.request.query_params.get(
            "method"
        )

        if method_filter:
            queryset = queryset.filter(
                method=method_filter
            )

        # -----------------------------------------------------
        # Filter by date
        # Example:
        # ?start_date=2026-08-01
        # ?end_date=2026-08-10
        # -----------------------------------------------------

        start_date = self.request.query_params.get(
            "start_date"
        )

        end_date = self.request.query_params.get(
            "end_date"
        )

        if start_date:
            queryset = queryset.filter(
                initiated_at__date__gte=start_date
            )

        if end_date:
            queryset = queryset.filter(
                initiated_at__date__lte=end_date
            )

        return queryset

    # =========================================================
    # SERIALIZER
    # =========================================================

    def get_serializer_class(self):
        """
        Use the create serializer when creating a payment.
        """

        if self.action == "create":
            return PaymentCreateSerializer

        return PaymentSerializer

    # =========================================================
    # CREATE PAYMENT
    # =========================================================

    def perform_create(self, serializer):
        """
        Create a payment for the current business.

        Allowed roles:
        OWNER
        MANAGER
        STAFF
        CASHIER
        """

        membership = get_membership(self.request)

        allowed_roles = [
            "OWNER",
            "MANAGER",
            "STAFF",
            "CASHIER",
        ]

        if (
            not membership
            or membership.role not in allowed_roles
        ):
            # DRF renders PermissionDenied as 403; a bare PermissionError
            # would escape as an unhandled 500.
            raise PermissionDenied(
                "Only cashiers, staff, managers, "
                "and owners can process payments"
            )

        payment = serializer.save(
            processed_by=self.request.user,
            business=self.request.current_business,
        )

        # Create payment log
        PaymentLog.objects.create(
            payment=payment,
            business=payment.business,
            action="INITIATED",
            status="PENDING",
            message=(
                f"Payment initiated via "
                f"{payment.get_method_display()}"
            ),
            metadata={
                "initiated_by": self.request.user.id
            },
        )

        return payment

    # =========================================================
    # M-PESA STK PUSH
    # =========================================================

    @action(
        detail=True,
        methods=["post"]
    )
    def initiate_mpesa(self, request, pk=None):
        """
        Initiate an M-Pesa STK Push.
        """

        payment = self.get_object()

        # -----------------------------------------------------
        # Make sure payment uses M-Pesa
        # -----------------------------------------------------

        if payment.method != "MPESA":
            return Response(
                {
                    "error": (
                        "This payment is not "
                        "an M-Pesa payment"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Prevent duplicate processing
        # -----------------------------------------------------

        if payment.status in [
            "COMPLETED",
            "PROCESSING",
        ]:
            return Response(
                {
                    "error": (
                        f"Payment already "
                        f"{payment.status}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Get customer's M-Pesa phone number
        # -----------------------------------------------------

        mpesa_phone = request.data.get(
            "mpesa_phone"
        )

        if not mpesa_phone:
            return Response(
                {
                    "error": "mpesa_phone is required"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Save phone number
        # -----------------------------------------------------

        payment.mpesa_phone = mpesa_phone

        payment.save(
            update_fields=["mpesa_phone"]
        )

        # -----------------------------------------------------
        # Import M-Pesa service
        # -----------------------------------------------------

        from .mpesa import MpesaAPI

        mpesa = MpesaAPI(
            request.current_business
        )

        # -----------------------------------------------------
        # Account reference
        # -----------------------------------------------------

        if payment.order:
            account_reference = (
                payment.order.order_number
            )
        else:
            account_reference = (
                f"PAY{payment.id}"
            )

        # -----------------------------------------------------
        # Transaction description
        # -----------------------------------------------------

        if payment.order:
            transaction_desc = (
                f"Payment for order "
                f"{payment.order.order_number}"
            )
        else:
            transaction_desc = (
                f"Payment {payment.id}"
            )

        # -----------------------------------------------------
        # Send STK Push
        # -----------------------------------------------------

        result = mpesa.stk_push(
            phone_number=mpesa_phone,
            amount=payment.amount,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
        )

        # =====================================================
        # STK PUSH FAILED
        # =====================================================

        if result.get("error"):

            MpesaTransaction.objects.create(
                business=request.current_business,
                payment=payment,
                transaction_type="STK_PUSH",
                phone_number=mpesa_phone,
                amount=payment.amount,
                account_reference=account_reference,
                status="FAILED",
                result_desc=result.get("error"),
                request_payload={
                    "mpesa_phone": mpesa_phone
                },
            )

            return Response(
                {
                    "error": result.get("error")
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # =====================================================
        # STK PUSH SUCCESSFULLY INITIATED
        # =====================================================

        transaction = MpesaTransaction.objects.create(
            business=request.current_business,
            payment=payment,
            transaction_type="STK_PUSH",
            checkout_request_id=result.get(
                "CheckoutRequestID"
            ),
            phone_number=mpesa_phone,
            amount=payment.amount,
            account_reference=account_reference,
            status="PROCESSING",
            request_payload=result,
            response_payload=result,
        )

        # -----------------------------------------------------
        # Update payment status
        # -----------------------------------------------------

        payment.status = "PROCESSING"

        payment.save(
            update_fields=["status"]
        )

        # -----------------------------------------------------
        # Create payment log
        # -----------------------------------------------------

        PaymentLog.objects.create(
            payment=payment,
            business=payment.business,
            action="MPESA_INITIATED",
            status="PROCESSING",
            message=(
                "M-Pesa STK Push initiated. "
                f"Checkout ID: "
                f"{result.get('CheckoutRequestID')}"
            ),
            metadata={
                "checkout_request_id": result.get(
                    "CheckoutRequestID"
                ),
                "phone": mpesa_phone,
            },
        )

        return Response(
            {
                "message": (
                    "M-Pesa STK Push initiated"
                ),
                "checkout_request_id": result.get(
                    "CheckoutRequestID"
                ),
                "merchant_request_id": result.get(
                    "MerchantRequestID"
                ),
                "response_code": result.get(
                    "ResponseCode"
                ),
                "response_description": result.get(
                    "ResponseDescription"
                ),
            },
            status=status.HTTP_200_OK,
        )

    # =========================================================
    # M-PESA STATUS
    # =========================================================

    @action(
        detail=True,
        methods=["get"]
    )
    def mpesa_status(self, request, pk=None):
        """
        Query the status of an M-Pesa transaction.
        """

        payment = self.get_object()

        # -----------------------------------------------------
        # Find latest STK transaction
        # -----------------------------------------------------

        transaction = (
            MpesaTransaction.objects.filter(
                payment=payment,
                transaction_type="STK_PUSH",
            )
            .order_by("-created_at")
            .first()
        )

        if not transaction:
            return Response(
                {
                    "error": (
                        "No M-Pesa transaction "
                        "found for this payment"
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # -----------------------------------------------------
        # If transaction already has final status
        # -----------------------------------------------------

        if transaction.status in [
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "TIMEOUT",
        ]:
            return Response(
                {
                    "status": transaction.status,
                    "result_desc": (
                        transaction.result_desc
                    ),
                    "mpesa_receipt": (
                        transaction.mpesa_receipt
                    ),
                    "completed_at": (
                        transaction.completed_at
                    ),
                }
            )

        # -----------------------------------------------------
        # Query M-Pesa
        # -----------------------------------------------------

        from .mpesa import MpesaAPI

        mpesa = MpesaAPI(
            request.current_business
        )

        result = mpesa.query_status(
            transaction.checkout_request_id
        )

        if result.get("error"):
            return Response(
                {
                    "error": result.get("error")
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        result_code = result.get(
            "ResultCode"
        )

        result_desc = result.get(
            "ResultDesc"
        )

        # =====================================================
        # PAYMENT SUCCESSFUL
        # =====================================================

        if str(result_code) == "0":

            mpesa_receipt = result.get(
                "MpesaReceiptNumber"
            )

            if mpesa_receipt:

                # complete() also updates the payment and its order.
                transaction.complete(
                    mpesa_receipt,
                    int(result_code),
                    result_desc,
                )

                return Response(
                    {
                        "status": "COMPLETED",
                        "mpesa_receipt": (
                            mpesa_receipt
                        ),
                        "result_desc": result_desc,
                    }
                )

            # ResultCode 0 with no receipt: Safaricom has accepted the request
            # but has not settled it yet. Leave the transaction PROCESSING and
            # let the caller poll again.
            logger.info(
                "M-Pesa query for %s returned success without a receipt",
                transaction.checkout_request_id,
            )

            return Response(
                {
                    "status": transaction.status,
                    "result_desc": result_desc,
                }
            )

        # =====================================================
        # PAYMENT FAILED / TIMEOUT
        # =====================================================

        if str(result_code) in [
            "1037",
            "2001",
        ]:
            transaction.status = "TIMEOUT"
        else:
            transaction.status = "FAILED"

        transaction.result_code = (
            int(result_code)
            if result_code is not None
            else None
        )

        transaction.result_desc = result_desc

        transaction.save()

        payment.status = "FAILED"

        payment.save(
            update_fields=["status"]
        )

        return Response(
            {
                "status": transaction.status,
                "result_code": result_code,
                "result_desc": result_desc,
            }
        )

    # =========================================================
    # COMPLETE PAYMENT
    # =========================================================

    @action(
        detail=True,
        methods=["post"]
    )
    def complete(self, request, pk=None):
        """
        Manually complete a pending payment.
        """

        membership = get_membership(request)

        allowed_roles = [
            "OWNER",
            "MANAGER",
            "STAFF",
            "CASHIER",
        ]

        if (
            not membership
            or membership.role not in allowed_roles
        ):
            return Response(
                {
                    "error": (
                        "Only cashiers, staff, "
                        "managers, and owners "
                        "can complete payments"
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = self.get_object()

        # -----------------------------------------------------
        # Validate payment status
        # -----------------------------------------------------

        if payment.status == "COMPLETED":
            return Response(
                {
                    "error": (
                        "Payment already completed"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if payment.status == "REFUNDED":
            return Response(
                {
                    "error": (
                        "Cannot complete "
                        "a refunded payment"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Get transaction ID
        # -----------------------------------------------------

        transaction_id = request.data.get(
            "transaction_id"
        )

        if not transaction_id:
            return Response(
                {
                    "error": (
                        "Transaction ID is required"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Prevent duplicate transaction IDs
        # -----------------------------------------------------

        if Payment.objects.filter(
            transaction_id=transaction_id
        ).exclude(
            id=payment.id
        ).exists():

            return Response(
                {
                    "error": (
                        "Transaction ID already exists"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Complete payment
        # -----------------------------------------------------

        payment.complete_payment(
            transaction_id
        )

        # -----------------------------------------------------
        # Create payment log
        # -----------------------------------------------------

        PaymentLog.objects.create(
            payment=payment,
            business=payment.business,
            action="COMPLETED",
            status="COMPLETED",
            message=(
                "Payment completed with "
                f"transaction ID: "
                f"{transaction_id}"
            ),
            metadata={
                "completed_by": request.user.id
            },
        )

        serializer = self.get_serializer(
            payment
        )

        return Response(
            serializer.data
        )

    # =========================================================
    # REFUND
    # =========================================================

    @action(
        detail=True,
        methods=["post"]
    )
    def refund(self, request, pk=None):
        """
        Refund a payment.

        Only managers and owners can refund.
        """

        membership = get_membership(request)

        if (
            not membership
            or membership.role not in [
                "OWNER",
                "MANAGER",
            ]
        ):
            return Response(
                {
                    "error": (
                        "Only managers and owners "
                        "can refund payments"
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = self.get_object()

        # -----------------------------------------------------
        # Validate status
        # -----------------------------------------------------

        if payment.status != "COMPLETED":
            return Response(
                {
                    "error": (
                        "Can only refund "
                        "completed payments"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Get refund reason
        # -----------------------------------------------------

        reason = request.data.get(
            "reason",
            ""
        )

        if not reason:
            return Response(
                {
                    "error": (
                        "Refund reason is required"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # TODO:
        # Implement actual provider refund.
        # -----------------------------------------------------

        payment.status = "REFUNDED"

        # Only set this if your Payment model
        # has a refunded_at field.
        if hasattr(payment, "refunded_at"):
            payment.refunded_at = timezone.now()

        payment.save()

        # -----------------------------------------------------
        # Create refund log
        # -----------------------------------------------------

        PaymentLog.objects.create(
            payment=payment,
            business=payment.business,
            action="REFUNDED",
            status="REFUNDED",
            message=(
                f"Payment refunded: {reason}"
            ),
            metadata={
                "refunded_by": request.user.id,
                "reason": reason,
            },
        )

        serializer = self.get_serializer(
            payment
        )

        return Response(
            serializer.data
        )

    # =========================================================
    # PAYMENT LOGS
    # =========================================================

    @action(
        detail=True,
        methods=["get"]
    )
    def logs(self, request, pk=None):
        """
        Get logs for a specific payment.
        """

        payment = self.get_object()

        logs = (
            PaymentLog.objects
            .filter(payment=payment)
            .order_by("-created_at")
        )

        serializer = PaymentLogSerializer(
            logs,
            many=True
        )

        return Response(
            serializer.data
        )


# =============================================================
# M-PESA STK CALLBACK
# =============================================================

class MpesaCallbackView(APIView):
    """
    Receives M-Pesa STK Push callbacks.

    Safaricom sends the payment result to this endpoint
    after the customer completes or cancels the STK request.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # Safaricom retries callbacks in bursts; throttling could drop a payment
    # result. Authenticity is enforced by is_authentic_callback() instead.
    throttle_classes = []
    # Excluded from the OpenAPI schema: Safaricom calls this, no client of ours
    # does, and the payload shape is defined by Daraja rather than by us.
    schema = None

    def post(self, request):
        if not is_authentic_callback(request):
            return Response(
                {"error": "Unauthorized callback"},
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data

        # -----------------------------------------------------
        # Get STK callback data
        # -----------------------------------------------------

        body = data.get(
            "Body",
            {}
        )

        stk_callback = body.get(
            "stkCallback",
            {}
        )

        checkout_request_id = (
            stk_callback.get(
                "CheckoutRequestID"
            )
        )

        result_code = stk_callback.get(
            "ResultCode"
        )

        result_desc = stk_callback.get(
            "ResultDesc"
        )

        # -----------------------------------------------------
        # Validate CheckoutRequestID
        # -----------------------------------------------------

        if not checkout_request_id:
            return Response(
                {
                    "error": (
                        "CheckoutRequestID missing"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # -----------------------------------------------------
        # Find transaction
        # -----------------------------------------------------

        transaction = (
            MpesaTransaction.objects
            .filter(
                checkout_request_id=(
                    checkout_request_id
                )
            )
            .first()
        )

        if not transaction:
            return Response(
                {
                    "error": (
                        "Transaction not found"
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # =====================================================
        # SUCCESSFUL PAYMENT
        # =====================================================

        if str(result_code) == "0":

            callback_metadata = (
                stk_callback.get(
                    "CallbackMetadata",
                    {}
                )
            )

            items = callback_metadata.get(
                "Item",
                []
            )

            mpesa_receipt = None
            transaction_date = None
            phone_number = None
            amount = None

            # -------------------------------------------------
            # Extract callback values
            # -------------------------------------------------

            for item in items:

                name = item.get("Name")
                value = item.get("Value")

                if name == "MpesaReceiptNumber":
                    mpesa_receipt = value

                elif name == "TransactionDate":
                    transaction_date = value

                elif name == "PhoneNumber":
                    phone_number = value

                elif name == "Amount":
                    amount = value

            # -------------------------------------------------
            # Complete transaction
            # -------------------------------------------------

            transaction.complete(
                mpesa_receipt,
                int(result_code),
                result_desc,
            )

            # -------------------------------------------------
            # Update transaction information
            # -------------------------------------------------

            if phone_number:
                transaction.phone_number = (
                    str(phone_number)
                )

            if amount:
                transaction.amount = amount

            transaction.save()

            # -------------------------------------------------
            # Complete payment
            # -------------------------------------------------

            payment = transaction.payment

            payment.status = "COMPLETED"

            if mpesa_receipt:
                # Only assign if your Payment model
                # contains an appropriate field.
                if hasattr(
                    payment,
                    "transaction_id"
                ):
                    payment.transaction_id = (
                        mpesa_receipt
                    )

            payment.save()

            # -------------------------------------------------
            # Payment log
            # -------------------------------------------------

            PaymentLog.objects.create(
                payment=payment,
                business=payment.business,
                action="COMPLETED",
                status="COMPLETED",
                message=(
                    "M-Pesa payment completed "
                    "successfully"
                ),
                metadata={
                    "mpesa_receipt": (
                        mpesa_receipt
                    ),
                    "checkout_request_id": (
                        checkout_request_id
                    ),
                    "transaction_date": (
                        transaction_date
                    ),
                },
            )

        # =====================================================
        # FAILED PAYMENT
        # =====================================================

        else:

            # -------------------------------------------------
            # Determine failure type
            # -------------------------------------------------

            if str(result_code) in [
                "1037",
                "2001",
            ]:
                transaction.status = "TIMEOUT"
            else:
                transaction.status = "FAILED"

            transaction.result_code = (
                int(result_code)
                if result_code is not None
                else None
            )

            transaction.result_desc = (
                result_desc
            )

            transaction.save()

            # -------------------------------------------------
            # Update payment
            # -------------------------------------------------

            payment = transaction.payment

            payment.status = "FAILED"

            payment.save(
                update_fields=["status"]
            )

            # -------------------------------------------------
            # Payment log
            # -------------------------------------------------

            PaymentLog.objects.create(
                payment=payment,
                business=payment.business,
                action="FAILED",
                status="FAILED",
                message=(
                    f"M-Pesa payment failed: "
                    f"{result_desc}"
                ),
                metadata={
                    "result_code": result_code,
                    "checkout_request_id": (
                        checkout_request_id
                    ),
                },
            )

        # -----------------------------------------------------
        # Always acknowledge callback
        # -----------------------------------------------------

        return Response(
            {
                "ResultCode": 0,
                "ResultDesc": (
                    "Callback received successfully"
                ),
            },
            status=status.HTTP_200_OK,
        )


# =============================================================
# M-PESA C2B VALIDATION
# =============================================================

class MpesaC2BValidationView(APIView):
    """
    Handles M-Pesa C2B validation requests.

    Safaricom calls this endpoint before processing
    a C2B transaction.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # Safaricom retries callbacks in bursts; throttling could drop a payment
    # result. Authenticity is enforced by is_authentic_callback() instead.
    throttle_classes = []
    # Excluded from the OpenAPI schema: Safaricom calls this, no client of ours
    # does, and the payload shape is defined by Daraja rather than by us.
    schema = None

    def post(self, request):
        if not is_authentic_callback(request):
            return Response(
                {
                    "ResultCode": 1,
                    "ResultDesc": "Rejected",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # -----------------------------------------------------
        # Validate that the bill reference names a real order
        # for the shortcode's business.
        # -----------------------------------------------------

        bill_reference = request.data.get("BillRefNumber")

        if bill_reference:
            from orders.models import Order

            order_exists = Order.objects.filter(
                order_number=bill_reference
            ).exists()

            if not order_exists:
                logger.warning(
                    "C2B validation rejected unknown BillRefNumber %r",
                    bill_reference,
                )
                return Response(
                    {
                        "ResultCode": "C2B00012",
                        "ResultDesc": "Invalid account number",
                    },
                    status=status.HTTP_200_OK,
                )

        return Response(
            {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            },
            status=status.HTTP_200_OK,
        )


# =============================================================
# M-PESA C2B CONFIRMATION
# =============================================================

class MpesaC2BConfirmationView(APIView):
    """
    Handles M-Pesa C2B confirmation requests.

    Safaricom sends this after the C2B payment
    has been completed.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # Safaricom retries callbacks in bursts; throttling could drop a payment
    # result. Authenticity is enforced by is_authentic_callback() instead.
    throttle_classes = []
    # Excluded from the OpenAPI schema: Safaricom calls this, no client of ours
    # does, and the payload shape is defined by Daraja rather than by us.
    schema = None

    def post(self, request):
        if not is_authentic_callback(request):
            return Response(
                {
                    "ResultCode": 1,
                    "ResultDesc": "Rejected",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data

        # -----------------------------------------------------
        # Extract common C2B fields
        # -----------------------------------------------------

        transaction_type = data.get(
            "TransactionType"
        )

        transaction_id = data.get(
            "TransID"
        )

        amount = data.get(
            "TransAmount"
        )

        phone_number = data.get(
            "MSISDN"
        )

        bill_reference = data.get(
            "BillRefNumber"
        )

        business_short_code = data.get(
            "BusinessShortCode"
        )

        first_name = data.get(
            "FirstName"
        )

        last_name = data.get(
            "LastName"
        )

        # -----------------------------------------------------
        # Record the transaction against the order named by
        # BillRefNumber, so C2B money is not silently dropped.
        # -----------------------------------------------------

        if not transaction_id:
            return Response(
                {
                    "ResultCode": 0,
                    "ResultDesc": "Accepted",
                },
                status=status.HTTP_200_OK,
            )

        # Safaricom retries confirmations; TransID is unique per payment.
        if MpesaTransaction.objects.filter(
            transaction_id=transaction_id
        ).exists():
            return Response(
                {
                    "ResultCode": 0,
                    "ResultDesc": "Accepted",
                },
                status=status.HTTP_200_OK,
            )

        from orders.models import Order

        order = Order.objects.filter(
            order_number=bill_reference
        ).select_related("business").first()

        if not order:
            logger.warning(
                "C2B confirmation for unknown BillRefNumber %r (TransID %s)",
                bill_reference,
                transaction_id,
            )
            return Response(
                {
                    "ResultCode": 0,
                    "ResultDesc": "Accepted",
                },
                status=status.HTTP_200_OK,
            )

        payment = Payment.objects.create(
            business=order.business,
            order=order,
            amount=amount or 0,
            method="MPESA",
            status="COMPLETED",
            transaction_id=transaction_id,
            mpesa_receipt=transaction_id,
            mpesa_phone=phone_number or "",
            reference=bill_reference or "",
            completed_at=timezone.now(),
            metadata={
                "transaction_type": transaction_type,
                "business_short_code": business_short_code,
                "first_name": first_name,
                "last_name": last_name,
            },
        )

        MpesaTransaction.objects.create(
            business=order.business,
            payment=payment,
            transaction_type="C2B",
            transaction_id=transaction_id,
            mpesa_receipt=transaction_id,
            phone_number=phone_number or "",
            amount=amount or 0,
            account_reference=bill_reference or "",
            status="COMPLETED",
            result_code=0,
            result_desc="C2B confirmation received",
            callback_payload=data,
            completed_at=timezone.now(),
        )

        if order.status == "PENDING":
            order.status = "PAID"
            order.paid_at = timezone.now()
            order.save(update_fields=["status", "paid_at", "updated_at"])

        PaymentLog.objects.create(
            payment=payment,
            business=payment.business,
            action="COMPLETED",
            status="COMPLETED",
            message=f"C2B payment received: {transaction_id}",
            metadata={"bill_reference": bill_reference},
        )

        return Response(
            {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            },
            status=status.HTTP_200_OK,
        )
