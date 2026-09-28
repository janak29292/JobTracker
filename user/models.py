from django.contrib.auth.models import AbstractUser
from django.db import models
from bs4 import BeautifulSoup
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType

# Create your models here.


# Create your models here.
class User(AbstractUser):
    pass


class Unstructured(models.Model):
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, related_name='children'
    )
    info = models.TextField()

    def __str__(self):
        if self.parent:
            return f"{self.parent.__str__()}: {BeautifulSoup(self.info, 'html.parser').get_text()}"
        return BeautifulSoup(self.info, 'html.parser').get_text()


class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Pattern(models.Model):
    name = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='patterns')
    description = models.TextField()
    use_cases = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return self.name

class Problem(models.Model):
    pattern = models.ForeignKey(Pattern, on_delete=models.CASCADE, related_name='problems')
    phrase = models.CharField(max_length=100)
    statement = models.TextField()

    def __str__(self):
        return self.phrase

class Approach(models.Model):
    name = models.CharField(max_length=100)
    pattern = models.ForeignKey(Pattern, on_delete=models.CASCADE, related_name='approaches')
    description = models.TextField()
    time_complexity = models.CharField(max_length=10, null=True, blank=True)
    space_complexity = models.CharField(max_length=10, null=True, blank=True)
    code_example = models.TextField()
    code_result = models.TextField()

    def __str__(self):
        return self.name

# ---------------------------------------------------------
# Applicant Automation Models (JobTracker Phase 1)
# ---------------------------------------------------------

class ApplicantProfile(models.Model):
    first_name = models.CharField(max_length=128, default='')
    middle_name = models.CharField(max_length=128, blank=True, default='')
    last_name = models.CharField(max_length=128, default='')

    @property
    def full_name(self):
        return " ".join(filter(None, [self.first_name, self.middle_name, self.last_name]))
    designation = models.CharField(max_length=128, blank=True, help_text="Target role (e.g. 'Senior Backend Engineer')")
    email = models.EmailField()
    phone_number = models.CharField(max_length=32)
    linkedin_url = models.URLField(blank=True, null=True)
    github_url = models.URLField(blank=True, null=True)
    portfolio_url = models.URLField(blank=True, null=True)
    master_resume = models.FileField(upload_to='resumes/', blank=True, null=True)

    summary = models.TextField(blank=True, null=True)

    years_of_experience = models.IntegerField(default=0)
    current_location = models.CharField(max_length=128, blank=True)
    preferred_locations = models.JSONField(default=list, help_text="e.g. ['Bangalore', 'Remote']")
    last_job_location = models.CharField(max_length=128, blank=True)
    willingness_to_relocate = models.BooleanField(default=False)

    current_salary_fixed = models.PositiveIntegerField(default=0, help_text="Absolute fixed value (e.g. 1200000)")
    current_salary_variable = models.PositiveIntegerField(default=0, help_text="Absolute variable value (e.g. 300000)")
    expected_salary = models.PositiveIntegerField(default=0, help_text="Absolute total expected value (e.g. 2000000)")
    currency = models.CharField(max_length=3, default='INR', help_text="e.g. 'INR', 'USD'")

    notice_period_days = models.IntegerField(default=30)
    work_authorization = models.CharField(max_length=128, blank=True, help_text="e.g. 'Citizen', 'Visa Required'")

    core_skills = models.JSONField(default=list, help_text="e.g. ['Python', 'Django', 'AWS']")

class Education(models.Model):
    profile = models.ForeignKey(ApplicantProfile, on_delete=models.CASCADE, related_name='education')
    institution = models.CharField(max_length=128)
    degree = models.CharField(max_length=128)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

class Project(models.Model):
    profile = models.ForeignKey(ApplicantProfile, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=128)
    description = models.TextField()
    link = models.URLField(blank=True, null=True)
    tech_stacks = models.JSONField(default=list, help_text="e.g. ['Unity', 'C#']")
    bullet_points = GenericRelation('BulletPoint')

class WorkExperience(models.Model):
    profile = models.ForeignKey(ApplicantProfile, on_delete=models.CASCADE, related_name='experiences')
    company_name = models.CharField(max_length=128)
    job_title = models.CharField(max_length=128)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    is_current = models.BooleanField(default=False)
    tech_stacks = models.JSONField(default=list, help_text="e.g. ['Python', 'AWS']")
    bullet_points = GenericRelation('BulletPoint')

class BulletPoint(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    content = models.TextField()

class Answer(models.Model):
    profile = models.ForeignKey(ApplicantProfile, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey('Question', on_delete=models.CASCADE, related_name='answers')
    text = models.TextField()

class Question(models.Model):
    CATEGORY_CHOICES = [
        ('DEMOGRAPHIC', 'Demographic (Race, Gender, Veteran)'),
        ('SENSITIVE', 'Sensitive (Salary, Sponsorship)'),
        ('GENERAL', 'General (Notice period, Why us?)')
    ]
    pattern = models.CharField(max_length=255, help_text="e.g. 'What is your gender?'")
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES, default='GENERAL')

class Dealbreaker(models.Model):
    profile = models.ForeignKey(ApplicantProfile, on_delete=models.CASCADE, related_name='dealbreakers')
    phrase = models.CharField(max_length=255, help_text="e.g. 'Security Clearance', 'US Citizen Only'")
    is_active = models.BooleanField(default=True)
