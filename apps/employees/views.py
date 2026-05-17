from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from apps.core.utils import success, error, log_action
from apps.core.permissions import IsEmployee, IsManager, IsHRAdmin, StandardPagination
from .models import Employee, Department, Designation, Branch
from .serializers import (EmployeeListSerializer, EmployeeDetailSerializer,
                          EmployeeCreateSerializer, DepartmentSerializer,
                          DesignationSerializer, BranchSerializer)

class EmployeeListCreateView(APIView):
    permission_classes = [IsEmployee]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    def get_queryset(self, request):
        user = request.user
        qs = Employee.objects.select_related('user','department','designation','branch','reporting_manager')
        if user.is_hr_admin:
            return qs
        if user.is_manager_role:
            try:
                emp = user.employee_profile
                team_ids = list(emp.direct_reports.values_list('id', flat=True)) + [emp.id]
                return qs.filter(id__in=team_ids)
            except Exception:
                return qs.none()
        try:
            return qs.filter(id=user.employee_profile.id)
        except Exception:
            return qs.none()
    def get(self, request):
        qs = self.get_queryset(request)
        search = request.query_params.get('search')
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(full_name__icontains=search)|Q(employee_id__icontains=search)|Q(user__email__icontains=search))
        status_f = request.query_params.get('status')
        if status_f:
            qs = qs.filter(status=status_f)
        dept_f = request.query_params.get('department')
        if dept_f:
            qs = qs.filter(department_id=dept_f)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(EmployeeListSerializer(page, many=True).data)
    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = EmployeeCreateSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        emp = s.save(created_by=request.user)
        from apps.leaves.services import initialize_employee_balances
        initialize_employee_balances(emp)
        log_action(request, 'employee.created', 'Employee', emp.id, {'employee_id': emp.employee_id})
        return success(EmployeeDetailSerializer(emp).data, 'Employee created.', status=201)

class EmployeeDetailView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request, pk):
        try:
            emp = Employee.objects.select_related('user','department','designation','branch','reporting_manager').get(pk=pk)
        except Employee.DoesNotExist:
            return error('Employee not found.', status=404)
        return success(EmployeeDetailSerializer(emp).data)
    def patch(self, request, pk):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        try:
            emp = Employee.objects.get(pk=pk)
        except Employee.DoesNotExist:
            return error('Employee not found.', status=404)
        s = EmployeeDetailSerializer(emp, data=request.data, partial=True)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save()
        return success(s.data, 'Employee updated.')

class MeEmployeeView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        try:
            emp = request.user.employee_profile
            return success(EmployeeDetailSerializer(emp).data)
        except Exception:
            return error('No employee profile found.', status=404)

class EmployeeBalancesView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request, pk):
        from django.utils import timezone
        try:
            emp = Employee.objects.get(pk=pk)
        except Employee.DoesNotExist:
            return error('Employee not found.', status=404)
        year = int(request.query_params.get('year', timezone.now().year))
        from apps.leaves.models import LeaveBalance
        balances = LeaveBalance.objects.filter(employee=emp, year=year).select_related('leave_type')
        data = [{'leave_type_id': str(b.leave_type.id), 'leave_type_name': b.leave_type.name,
                 'leave_type_code': b.leave_type.code, 'year': b.year,
                 'allocated': str(b.allocated), 'used': str(b.used),
                 'carried_over': str(b.carried_over), 'available': str(b.available)} for b in balances]
        return success(data)

class EmployeeDocumentView(APIView):
    permission_classes = [IsEmployee]
    parser_classes = [MultiPartParser, FormParser]
    def post(self, request, pk):
        try:
            emp = Employee.objects.get(pk=pk)
        except Employee.DoesNotExist:
            return error('Employee not found.', status=404)
        file = request.FILES.get('file')
        if not file:
            return error('No file provided.', status=400)
        from apps.core.models import Document
        from django.contrib.contenttypes.models import ContentType
        doc = Document.objects.create(
            content_type=ContentType.objects.get_for_model(emp), object_id=emp.id,
            name=request.data.get('name', file.name), file=file,
            file_type=file.content_type or '', file_size=file.size, created_by=request.user)
        return success({'id': str(doc.id), 'name': doc.name, 'url': doc.file.url}, status=201)

class DepartmentView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        return success(DepartmentSerializer(Department.objects.select_related('head').all(), many=True).data)
    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = DepartmentSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        dept = s.save(created_by=request.user)
        return success(s.data, status=201)

class DesignationView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        qs = Designation.objects.select_related('department').all()
        dept_id = request.query_params.get('department')
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        return success(DesignationSerializer(qs, many=True).data)
    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = DesignationSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save(created_by=request.user)
        return success(s.data, status=201)

class BranchView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        return success(BranchSerializer(Branch.objects.all(), many=True).data)
    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = BranchSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save(created_by=request.user)
        return success(s.data, status=201)
