from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

ROLE_CHOICES = [
    ("employee", "Employee"),
    ("manager", "Manager"),
    ("hr_admin", "HR Admin"),
    ("super_admin", "Super Admin"),
    ("rota_manager", "Rota Manager"),  # read-only view of one unit's leave; no approval authority
]


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "hr_admin")
        extra_fields.setdefault("must_change_password", False)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="employee")
    must_change_password = models.BooleanField(default=True)
    # A login used by several people (e.g. the Shift Engineers' common ID).
    # Approvals made from it must carry the acting person's name and P.No.
    # db_default: the database itself fills in False, so the previous version of
    # the code keeps working if the code is ever rolled back.
    is_shared_account = models.BooleanField(default=False, db_default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    password_reset_token = models.CharField(max_length=200, blank=True)
    password_reset_expires = models.DateTimeField(null=True, blank=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    @property
    def is_hr_admin(self):
        return self.role in ("hr_admin", "super_admin")

    @property
    def is_manager_role(self):
        return self.role in ("manager", "hr_admin", "super_admin")
