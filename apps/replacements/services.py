"""
ReplacementAssignmentService — handles replacement assignment logic.
"""
from django.db import transaction
from .models import ReplacementAssignment, ReplacementProject


class ReplacementAssignmentService:

    @staticmethod
    @transaction.atomic
    def assign(leave_application, replacement_employee, assigned_by,
               notes='', projects=None, method='manager_assigned'):
        """
        Assign a replacement employee for a leave application.
        Creates or updates a ReplacementAssignment record.
        """
        assignment, created = ReplacementAssignment.objects.update_or_create(
            leave_application=leave_application,
            defaults={
                'absent_employee': leave_application.employee,
                'replacement_employee': replacement_employee,
                'assigned_by': assigned_by,
                'assignment_method': method,
                'notes': notes,
                'status': 'active',
            }
        )
        if projects:
            assignment.projects.set(projects)

        return assignment

    @staticmethod
    def get_assignment(leave_application):
        """Get the replacement assignment for a leave application, or None."""
        try:
            return ReplacementAssignment.objects.select_related(
                'replacement_employee', 'assigned_by'
            ).get(leave_application=leave_application)
        except ReplacementAssignment.DoesNotExist:
            return None

    @staticmethod
    def prompt_assignment(application):
        """
        Called after final approval — signals that a replacement
        should be assigned. Currently a no-op hook for future automation.
        """
        pass

    @staticmethod
    @transaction.atomic
    def cancel_assignment(leave_application):
        """Cancel a replacement assignment when leave is cancelled."""
        ReplacementAssignment.objects.filter(
            leave_application=leave_application
        ).update(status='cancelled')
