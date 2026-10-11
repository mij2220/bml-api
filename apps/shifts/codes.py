"""
Codes written on Engro's daily attendance sheet (assumption A-7).

kind:
  present  worked the rostered shift (attendance hours = shift hours)
  off      rostered day off
  leave    on leave, no attendance hours
  holiday  gazetted holiday not worked
  absent   unauthorised absence
counts_48: counts toward the 48 crew-week hours (assumption A-13)
"""

CODES = {
    'P':  {'label': 'Present',                 'kind': 'present', 'counts_48': True},
    'SA': {'label': 'Special assignment',      'kind': 'present', 'counts_48': True},
    'RT': {'label': 'Rotational training',     'kind': 'present', 'counts_48': True},
    'LD': {'label': 'Light duty',              'kind': 'present', 'counts_48': True},
    'O':  {'label': 'Off',                     'kind': 'off',     'counts_48': False},
    'V':  {'label': 'Annual leave',            'kind': 'leave',   'counts_48': True},
    'C':  {'label': 'Casual leave',            'kind': 'leave',   'counts_48': False},
    'S':  {'label': 'Sick leave',              'kind': 'leave',   'counts_48': False},
    'CD': {'label': 'Compensatory day off',    'kind': 'leave',   'counts_48': True},
    'BD': {'label': 'Birthday off',            'kind': 'leave',   'counts_48': False},
    'L':  {'label': 'Leave without pay',       'kind': 'leave',   'counts_48': False},
    'GH': {'label': 'Gazetted holiday',        'kind': 'holiday', 'counts_48': True},
    'A':  {'label': 'Unauthorised absence',    'kind': 'absent',  'counts_48': False},
}

CODE_CHOICES = [(k, v['label']) for k, v in CODES.items()]

SHIFT_CODES = ['M', 'E', 'N', 'G']          # working shifts; 'O' = off in the roster
ROSTER_CHOICES = [('M', 'Morning'), ('E', 'Evening'), ('N', 'Night'), ('G', 'General'), ('O', 'Off')]

EXTRA_KIND_CHOICES = [('normal', 'Normal'), ('training', 'Training'), ('callout', 'Call-out')]

# Leave module code -> sheet code. Admin can change it in Attendance settings.
DEFAULT_LEAVE_CODE_MAP = {
    'AL': 'V', 'V': 'V', 'ANNUAL': 'V',
    'CL': 'C', 'C': 'C', 'CASUAL': 'C',
    'SL': 'S', 'S': 'S', 'SICK': 'S',
    'CD': 'CD', 'BD': 'BD',
    'UL': 'L', 'LWP': 'L', 'L': 'L',
}


def kind(code):
    return CODES.get(code, {}).get('kind', 'absent')


def is_present(code):
    return kind(code) == 'present'
