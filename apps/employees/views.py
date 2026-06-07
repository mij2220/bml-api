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
            return success(EmployeeDetailSerializer(emp, context={'request': request}).data)
        except Exception:
            return error('No employee profile found.', status=404)

    def patch(self, request):
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile found.', status=404)
        # Merge data + files so multipart uploads (profile_picture) are handled
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        if request.FILES:
            data.update(request.FILES)
        s = EmployeeDetailSerializer(
            emp, data=data, partial=True,
            context={'request': request}
        )
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save()
        return success(EmployeeDetailSerializer(emp, context={'request': request}).data)

class MeBalancesView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        from django.utils import timezone
        from apps.leaves.models import LeaveBalance
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile found.', status=400)
        year = int(request.query_params.get('year', timezone.now().year))
        balances = LeaveBalance.objects.filter(employee=emp, year=year).select_related('leave_type')
        data = [{'leave_type': str(b.leave_type.id), 'leave_type_name': b.leave_type.name,
                 'leave_type_code': b.leave_type.code, 'leave_type_color': b.leave_type.color,
                 'year': b.year, 'remaining_days': float(b.available),
                 'allocated_days': float(b.allocated), 'used_days': float(b.used),
                 'splits_used': b.splits_used, 'splits_allowed': b.splits_allowed,
                 'leave_type_code': b.leave_type.code} for b in balances]
        return success(data)

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

    def post(self, request, pk):
        """Adjust a leave balance for an employee."""
        from apps.leaves.models import LeaveBalance, LeaveType
        from django.utils import timezone
        from decimal import Decimal, InvalidOperation

        try:
            emp = Employee.objects.get(pk=pk)
        except Employee.DoesNotExist:
            return error('Employee not found.', status=404)

        leave_type_id = request.data.get('leave_type_id')
        adjustment    = request.data.get('adjustment')
        note          = request.data.get('note', '')
        year          = int(request.data.get('year', timezone.now().year))

        if not leave_type_id or adjustment is None:
            return error('leave_type_id and adjustment are required.', status=400)

        try:
            adj = Decimal(str(adjustment))
        except InvalidOperation:
            return error('adjustment must be a number.', status=400)

        try:
            leave_type = LeaveType.objects.get(pk=leave_type_id)
        except LeaveType.DoesNotExist:
            return error('Leave type not found.', status=404)

        balance, created = LeaveBalance.objects.get_or_create(
            employee=emp, leave_type=leave_type, year=year,
            defaults={'allocated': Decimal('0'), 'used': Decimal('0'), 'carried_over': Decimal('0')}
        )
        balance.allocated = max(Decimal('0'), balance.allocated + adj)
        balance.save()

        return success({
            'leave_type_id': str(leave_type.id),
            'leave_type_name': leave_type.name,
            'year': year,
            'allocated': str(balance.allocated),
            'used': str(balance.used),
            'available': str(balance.available),
            'note': note,
        })

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


class QuotaManagementView(APIView):
    """HR admin endpoint — recalculate experience/splits for one or all employees."""
    permission_classes = [IsManager]

    def get(self, request):
        """List all employees with their current quota and experience data."""
        from apps.leaves.experience import ExperienceService
        from apps.leaves.models import LeaveBalance
        from django.utils import timezone
        year = timezone.now().year

        employees = Employee.objects.filter(status__in=['active','on_leave']).select_related(
            'department', 'designation'
        )
        result = []
        for emp in employees:
            exp_years = float(ExperienceService.get_experience_years(emp))
            tier = ExperienceService.get_experience_tier(emp)
            exp_display = ExperienceService.get_experience_display(emp)

            al_bal = LeaveBalance.objects.filter(
                employee=emp, leave_type__code='AL', year=year
            ).first()
            sl_bal = LeaveBalance.objects.filter(
                employee=emp, leave_type__code='SL', year=year
            ).first()

            result.append({
                'id': str(emp.id),
                'employee_id': emp.employee_id,
                'full_name': emp.full_name,
                'department': emp.department.name if emp.department else None,
                'experience_start_date': str(emp.experience_start_date) if emp.experience_start_date else None,
                'joining_date': str(emp.joining_date),
                'experience_years': round(exp_years, 1),
                'experience_tier': tier,
                'experience_display': exp_display,
                'al_allocated': float(al_bal.allocated) if al_bal else 0,
                'al_used': float(al_bal.used) if al_bal else 0,
                'al_available': float(al_bal.available) if al_bal else 0,
                'al_splits_used': al_bal.splits_used if al_bal else 0,
                'al_splits_allowed': al_bal.splits_allowed if al_bal else 0,
                'sl_splits_used': sl_bal.splits_used if sl_bal else 0,
                'sl_splits_allowed': sl_bal.splits_allowed if sl_bal else 0,
            })
        return success(result)

    def post(self, request):
        """Recalculate quota for one employee or all employees.
        Body: { action: 'recalculate_experience'|'recalculate_splits'|'replenish_cd'|'all', employee_id?: uuid }
        """
        from apps.leaves.experience import ExperienceService
        from apps.leaves.models import LeaveBalance, LeaveApplication
        from django.utils import timezone
        year = timezone.now().year

        action = request.data.get('action', 'all')
        emp_id = request.data.get('employee_id')

        employees = []
        if emp_id:
            try:
                employees = [Employee.objects.get(pk=emp_id)]
            except Employee.DoesNotExist:
                from apps.core.utils import error
                return error('Employee not found.', status=404)
        else:
            employees = list(Employee.objects.filter(status__in=['active','on_leave']))

        results = []
        for emp in employees:
            try:
                if action in ('recalculate_experience', 'all'):
                    bal = ExperienceService.recalculate_al_balance(emp, year, triggered_by='hr_manual')
                    ExperienceService.recalculate_sl_balance(emp, year, triggered_by='hr_manual')

                if action in ('recalculate_splits', 'all'):
                    for code in ['AL', 'SL']:
                        count = LeaveApplication.objects.filter(
                            employee=emp, status='approved',
                            leave_type__code=code, start_date__year=year
                        ).count()
                        LeaveBalance.objects.filter(
                            employee=emp, leave_type__code=code, year=year
                        ).update(splits_used=count)

                if action in ('replenish_cd', 'all'):
                    from decimal import Decimal
                    from apps.leaves.models import LeaveType
                    cd_type = LeaveType.objects.filter(code='CD').first()
                    if cd_type:
                        bal_cd, created = LeaveBalance.objects.get_or_create(
                            employee=emp, leave_type=cd_type, year=year,
                            defaults={'allocated': Decimal('2')}
                        )
                        if not created:
                            new_alloc = min(bal_cd.allocated + Decimal('2'), cd_type.max_balance)
                            if new_alloc != bal_cd.allocated:
                                bal_cd.allocated = new_alloc
                                bal_cd.save(update_fields=['allocated'])

                results.append({'employee_id': emp.employee_id, 'status': 'ok'})
            except Exception as e:
                results.append({'employee_id': emp.employee_id, 'status': 'error', 'message': str(e)})

        return success({
            'action': action,
            'processed': len(results),
            'results': results,
        })


class TeamBalancesView(APIView):
    """
    Returns leave balance summary for a set of employees.
    HR Admin: all employees.
    Manager: only their direct reports.
    """
    permission_classes = [IsManager]

    def get(self, request):
        from apps.leaves.experience import ExperienceService
        from apps.leaves.models import LeaveBalance, LeaveType
        from django.utils import timezone
        year = timezone.now().year

        try:
            me = request.user.employee_profile
        except Exception:
            from apps.core.utils import error
            return error('Employee profile not found.', status=403)

        # HR/super admin sees all; manager sees direct reports only
        is_hr = request.user.role in ('hr_admin', 'super_admin')
        if is_hr:
            employees = Employee.objects.filter(
                status__in=['active', 'on_leave']
            ).select_related('department', 'designation')
        else:
            employees = Employee.objects.filter(
                reporting_manager=me,
                status__in=['active', 'on_leave']
            ).select_related('department', 'designation')

        leave_types = LeaveType.objects.filter(is_active=True).order_by('code')
        lt_codes = [lt.code for lt in leave_types]

        result = []
        for emp in employees.order_by('full_name'):
            yrs = float(ExperienceService.get_experience_years(emp))
            tier = ExperienceService.get_experience_tier(emp)
            balances_qs = LeaveBalance.objects.filter(
                employee=emp, year=year
            ).select_related('leave_type')
            bal_map = {b.leave_type.code: b for b in balances_qs}

            leave_summary = []
            for lt in leave_types:
                b = bal_map.get(lt.code)
                leave_summary.append({
                    'code': lt.code,
                    'name': lt.name,
                    'allocated': float(b.allocated) if b else 0,
                    'used': float(b.used) if b else 0,
                    'available': float(b.available) if b else 0,
                    'splits_used': b.splits_used if b else 0,
                    'splits_allowed': b.splits_allowed if b else 0,
                })

            result.append({
                'id': str(emp.id),
                'employee_id': emp.employee_id,
                'full_name': emp.full_name,
                'department': emp.department.name if emp.department else None,
                'designation': emp.designation.name if emp.designation else None,
                'experience_years': round(yrs, 1),
                'experience_tier': tier,
                'balances': leave_summary,
            })

        return success({
            'year': year,
            'is_hr_view': is_hr,
            'leave_types': [{'code': lt.code, 'name': lt.name} for lt in leave_types],
            'employees': result,
        })
