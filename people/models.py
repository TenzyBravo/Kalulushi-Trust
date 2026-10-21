from django.conf import settings
from django.db import models
from django.db.models import Q

from .phone import normalise_phone, validate_phone


class PhoneField(models.CharField):
    """A Zambian mobile number, normalised to +260XXXXXXXXX before saving."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 13)
        super().__init__(*args, **kwargs)
        if validate_phone not in self.validators:
            self.validators.append(validate_phone)

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value:
            value = normalise_phone(value)
            setattr(model_instance, self.attname, value)
        return value


class Guardian(models.Model):
    """A parent or guardian. Siblings share the same Guardian row."""

    full_name = models.CharField(max_length=200)
    phone = PhoneField(unique=True, help_text="Zambian mobile, e.g. 0971234567")
    alt_phone = PhoneField(blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class Student(models.Model):
    FEMALE = "F"
    MALE = "M"
    GENDER_CHOICES = [(FEMALE, "Female"), (MALE, "Male")]

    admission_number = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    other_names = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text="Untick when a pupil leaves the school.")
    guardians = models.ManyToManyField(Guardian, through="StudentGuardian", related_name="students")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "pupil"

    def __str__(self):
        return f"{self.full_name} ({self.admission_number})"

    @property
    def full_name(self):
        return " ".join(p for p in [self.first_name, self.other_names, self.last_name] if p)

    @property
    def primary_guardian(self):
        link = self.guardian_links.filter(is_primary=True).select_related("guardian").first()
        return link.guardian if link else None

    def result_recipients(self):
        """Guardians who should receive this pupil's results."""
        return Guardian.objects.filter(student_links__student=self, student_links__receives_results=True)


class StudentGuardian(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="student_links")
    relationship = models.CharField(max_length=50, blank=True, help_text="e.g. Mother, Uncle")
    is_primary = models.BooleanField(default=False, help_text="Main contact for this pupil.")
    receives_results = models.BooleanField(default=True)

    class Meta:
        verbose_name = "pupil guardian"
        constraints = [
            models.UniqueConstraint(fields=["student", "guardian"], name="unique_student_guardian"),
            models.UniqueConstraint(
                fields=["student"], condition=Q(is_primary=True), name="one_primary_guardian_per_student"
            ),
        ]

    def __str__(self):
        return f"{self.guardian.full_name} → {self.student.full_name}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            StudentGuardian.objects.filter(student=self.student_id, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)
        super().save(*args, **kwargs)


class Staff(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="staff"
    )
    staff_number = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=20, blank=True, help_text="e.g. Mr, Mrs, Ms, Dr")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = PhoneField(blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "staff"

    def __str__(self):
        return " ".join(p for p in [self.title, self.first_name, self.last_name] if p)
