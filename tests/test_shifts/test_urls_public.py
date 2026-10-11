"""Production serves every request through the public-schema URL file
(PUBLIC_SCHEMA_URLCONF). Every API route must be there as well."""
from django.urls import resolve, Resolver404, get_resolver


def test_public_urlconf_has_every_api_route():
    main = {str(p.pattern) + str(sub.pattern) for p in get_resolver('config.urls').url_patterns
            if hasattr(p, 'url_patterns') for sub in p.url_patterns}
    for path in ('/api/v1/shifts/meta/', '/api/v1/shifts/sections/', '/api/v1/overtime/statement/',
                 '/api/v1/shifts/admin/sections/', '/api/v1/leaves/', '/api/v1/auth/me/'):
        resolve(path, urlconf='config.urls_public')
    assert main  # sanity
