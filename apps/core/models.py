import uuid
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('accounts.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    class Meta:
        abstract = True

class Document(BaseModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey('content_type', 'object_id')
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to='documents/')
    file_type = models.CharField(max_length=50)
    file_size = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['-created_at']

class AuditLog(BaseModel):
    user = models.ForeignKey('accounts.User', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='audit_logs')
    action = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    target_id = models.UUIDField(null=True, blank=True)
    changes = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    class Meta:
        ordering = ['-created_at']
