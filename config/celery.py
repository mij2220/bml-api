import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
app = Celery('bookmyleave')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'monthly-leave-accrual': {
        'task': 'apps.leaves.tasks.run_monthly_accrual',
        'schedule': crontab(minute=1, hour=0, day_of_month=1),
    },
    'year-end-carryover': {
        'task': 'apps.leaves.tasks.run_year_end_carryover',
        'schedule': crontab(minute=0, hour=23, day_of_month=31, month_of_year=12),
    },
    'approval-escalation-check': {
        'task': 'apps.leaves.tasks.check_approval_escalations',
        'schedule': crontab(minute=0, hour='*/6'),
    },
    'daily-absent-marking': {
        'task': 'apps.attendance.tasks.mark_absent_employees',
        'schedule': crontab(minute=59, hour=23),
    },
    'close-replacement-assignments': {
        'task': 'apps.replacements.tasks.close_completed_assignments',
        'schedule': crontab(minute=0, hour=1),
    },
}
