import logging
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_task(self, to_email: str, subject: str, html_body: str):
    try:
        api_key = getattr(settings, 'SENDGRID_API_KEY', '')
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@bookmyleave.com')
        if api_key and api_key.startswith('SG.'):
            import urllib.request, json
            payload = {
                'personalizations': [{'to': [{'email': to_email}]}],
                'from': {'email': from_email, 'name': 'BookMyLeave'},
                'subject': subject,
                'content': [{'type': 'text/html', 'value': html_body}],
            }
            req = urllib.request.Request(
                'https://api.sendgrid.com/v3/mail/send',
                data=json.dumps(payload).encode('utf-8'),
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 202):
                    raise Exception(f'SendGrid error {resp.status}')
        else:
            from django.core.mail import EmailMessage
            msg = EmailMessage(subject=subject, body=html_body, from_email=from_email, to=[to_email])
            msg.content_subtype = 'html'
            msg.send()
        logger.info('Email sent to %s', to_email)
    except Exception as exc:
        raise self.retry(exc=exc)

@shared_task(bind=True, max_retries=3)
def send_password_reset_email(self, user_id, token):
    try:
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.get(pk=user_id)
        reset_url = f'{settings.FRONTEND_URL}/reset-password?token={token}'
        html = f'<p>Reset your password: <a href="{reset_url}">{reset_url}</a></p><p>Expires in 2 hours.</p>'
        send_email_task.delay(user.email, '[BookMyLeave] Password reset', html)
    except Exception as exc:
        raise self.retry(exc=exc)
