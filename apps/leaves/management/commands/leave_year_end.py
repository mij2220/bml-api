"""python manage.py leave_year_end --year 2026 [--create] [--carry] [--preview]

Same as Admin > Quota Management > Year end. Use it from a scheduled job
(e.g. a Railway cron on 1 Dec and 1 Jan) or by hand. Safe to repeat."""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Create next year leave balances and/or apply AL carry forward (capped at one year).'

    def add_arguments(self, p):
        p.add_argument('--year', type=int, required=True, help='The year being closed, e.g. 2026')
        p.add_argument('--create', action='store_true', help='Create year+1 balances (from 1 Dec)')
        p.add_argument('--carry', action='store_true', help='Apply carry forward (from 1 Jan of year+1)')
        p.add_argument('--preview', action='store_true')

    def handle(self, *a, **o):
        from apps.leaves.year_end import preview, create_balances, carry_forward, YearEndError
        try:
            if o['preview'] or not (o['create'] or o['carry']):
                rows = preview(o['year'])
                self.stdout.write(f"{len(rows)} people; carry {sum(r['carry'] for r in rows):g} days, "
                                  f"lapse {sum(r['lapsed'] for r in rows):g} days")
            if o['create']:
                self.stdout.write(str(create_balances(o['year'])))
            if o['carry']:
                self.stdout.write(str(carry_forward(o['year'])))
        except YearEndError as e:
            raise CommandError(str(e))
