from django.db import models
from django.utils import timezone

JOB_STATUS = {
    "IG": "Ignored",
    "NA": "Not Applied",
    "AF": "Applied",
    "CR": "Call Received",
    "IS": "Interview Scheduled",
    "RP": "Response Pending",
    "NE": "Negotiating",
    "RE": "Rejected",
    "OR": "Offer Received",
    "AC": "Application Closed"
}

PLATFORM = {
    'LI': 'linkedIn',
    'NI': 'naukri',
    "M": 'manual',
    'RAW': 'raw_data'
}

SENIORITY_LEVEL = {
    "intern": "intern",
    "junior": "junior",
    "mid": "mid",
    "senior": "senior",
    "lead": "lead",
    "manager": "manager",
    "director": "director",
    "vp": "vp",
    "cto": "cto"
}

ROLE_CATEGORY = {
    "backend": "backend",
    "frontend": "frontend",
    "fullstack": "fullstack",
    "data_engineer": "data_engineer",
    "data_science": "data_science",
    "devops": "devops",
    "mobile": "mobile",
    "qa": "qa"
}

# Create your models here.
class Job(models.Model):
    platform = models.CharField(max_length=16, choices=PLATFORM)
    company = models.CharField(max_length=64)
    role_summary = models.TextField()
    seniority_level = models.CharField(
        max_length=16, choices=SENIORITY_LEVEL, null=True, blank=True
    )
    role_category = models.CharField(
        max_length=16, choices=ROLE_CATEGORY, null=True, blank=True
    )
    role_title = models.CharField(
        max_length=128, null=True, blank=True
    )
    recruiter = models.CharField(max_length=64, null=True)
    contact = models.CharField(max_length=16, null=True)
    agency = models.CharField(max_length=64, null=True)
    position = models.CharField(max_length=64)
    job_description = models.TextField()
    job_location = models.CharField(max_length=64)
    apply_url = models.CharField(max_length=256)
    status = models.CharField(
        max_length=16, choices=JOB_STATUS, default="NA"
    )
    experience_min = models.IntegerField(null=True)
    experience_max = models.IntegerField(null=True)
    tech_stack_primary = models.CharField(max_length=256)
    tech_stack_all = models.CharField(max_length=512)
    tech_stack_primary_new = models.CharField(max_length=256, null=True)
    tech_stack_all_new = models.CharField(max_length=512, null=True)
    salary = models.CharField(max_length=64, null=True)
    ratings = models.CharField(max_length=64, null=True)
    last_interaction = models.DateField(null=True)
    applied_on = models.DateField(null=True)
    first_response_date = models.DateField(null=True)
    last_posted = models.DateField()

    def save(self, *args, **kwargs):
        self.last_interaction = timezone.localtime(timezone.now()).date()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.company


class Posting(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='postings')
    platform_job_id = models.CharField(max_length=64)
    raw_text = models.CharField(max_length=64, null=True)
    ignore = models.BooleanField(default=False)
    date = models.DateField()

    def __str__(self):
        return self.job.company


class FormData(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    info = models.CharField(max_length=64)

    # def __str__(self):
    #     return f"{self.job.company}___{self.info}"


class Session(models.Model):
    platform = models.CharField(max_length=16, choices=PLATFORM, unique=True)
    data = models.TextField()

    def __str__(self):
        return self.platform


class JobUrl(models.Model):
    url = models.CharField(max_length=256)
    scanned = models.BooleanField(default=False)
    # updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.url


class JobRaw(models.Model):
    platform = models.CharField(max_length=16, choices=PLATFORM)
    job_id = models.CharField(max_length=64)
    description = models.TextField()
    company = models.CharField(max_length=64)
    job_title = models.CharField(max_length=64)
    job_info = models.CharField(max_length=256)
    job_url = models.CharField(max_length=256)
    parsed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company}: {self.job_title}"


class TechStack(models.Model):
    name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name


# Job.objects.annotate(
#     posting_count=Subquery(
#         Posting.objects.filter(job=OuterRef('pk')).values('job_id').annotate(p_c = Count('job_id')).values('p_c')[:1]
#     )
# ).values('posting_count').annotate(job_count=Count('posting_count'))
