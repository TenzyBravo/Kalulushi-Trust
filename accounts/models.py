from django.contrib.auth.models import AbstractUser, Group
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models

from . import roles


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", roles.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """A person who can sign in. The role decides admin access and permissions."""

    role = models.CharField(max_length=16, choices=roles.ROLE_CHOICES, default=roles.TEACHER)

    objects = UserManager()

    class Meta:
        ordering = ["last_name", "first_name", "username"]

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        self.is_staff = self.is_superuser or self.role in roles.ADMIN_SITE_ROLES
        super().save(*args, **kwargs)
        self.sync_role_group()

    def sync_role_group(self):
        role_group_names = [roles.group_name(r) for r, _ in roles.ROLE_CHOICES]
        group, _ = Group.objects.get_or_create(name=roles.group_name(self.role))
        self.groups.remove(*self.groups.filter(name__in=role_group_names).exclude(pk=group.pk))
        self.groups.add(group)

    @property
    def is_admin_role(self):
        return self.role == roles.ADMIN

    @property
    def is_head(self):
        return self.role == roles.HEAD

    @property
    def is_teacher(self):
        return self.role == roles.TEACHER
