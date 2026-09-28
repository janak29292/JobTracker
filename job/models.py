from django.db import models
from django.utils import timezone

JOB_STATUS = {
    "IG": "Ignored",
    "NA": "Not Applied",
    "MA": "Matched",
    "PR": "Pending Review",
    "AF": "Applied",
    "AS": "Assessment / Take-Home",
    "CR": "Call Received",
    "IS": "Interview Scheduled",
    "RP": "Response Pending",
    "NE": "Negotiating",
    "RE": "Rejected",
    "OR": "Offer Received",
    "AC": "Application Closed"
    # "WD": "Withdrawn"
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
    "cto": "cto",
    "other": "other"
}

ROLE_CATEGORY = {
    "backend": "backend",
    "frontend": "frontend",
    "fullstack": "fullstack",
    "data_engineer": "data_engineer",
    "data_science": "data_science",
    "devops": "devops",
    "mobile": "mobile",
    "qa": "qa",
    "other": "other"
}

# Create your models here.
class Job(models.Model):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_status = self.status

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

    class Meta:
        indexes = [
            # Homepage list ordering + date-range queries
            models.Index(fields=['-last_posted'], name='idx_job_last_posted'),

            # Date-range queries in status-trends + sort option
            models.Index(fields=['-last_interaction'], name='idx_job_last_interaction'),

            # Status filtering (most common filter)
            models.Index(fields=['status'], name='idx_job_status'),

            # Applied date for trends, velocity, daily-count, ghosting
            models.Index(fields=['applied_on'], name='idx_job_applied_on'),

            # Response date for summary calculations
            models.Index(fields=['first_response_date'], name='idx_job_first_resp'),

            # Compound: ghosting query (status + applied_on)
            models.Index(fields=['status', 'applied_on'], name='idx_job_status_applied'),

            # Platform for channel stats
            models.Index(fields=['platform'], name='idx_job_platform'),

            # Experience range filtering
            models.Index(fields=['experience_min', 'experience_max'], name='idx_job_experience'),
        ]

    def save(self, *args, **kwargs):
        self.last_interaction = timezone.localtime(timezone.now()).date()
        
        status_changed = self.pk is None or self.status != self._original_status
        
        super().save(*args, **kwargs)
        
        if status_changed:
            StatusEvent.objects.create(job=self, status=self.status)
            self._original_status = self.status

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


class StatusEvent(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='status_events')
    status = models.CharField(max_length=16, choices=JOB_STATUS)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.job.company} - {self.status}"



# Job.objects.annotate(
#     posting_count=Subquery(
#         Posting.objects.filter(job=OuterRef('pk')).values('job_id').annotate(p_c = Count('job_id')).values('p_c')[:1]
#     )
# ).values('posting_count').annotate(job_count=Count('posting_count'))

class UnknownTech(models.Model):
    tech = models.CharField(max_length=128, unique=True)
    job = models.ForeignKey(Job, on_delete=models.SET_NULL, null=True)
    processed = models.BooleanField(default=False)
    accepted = models.BooleanField(default=False)
    added_to_job = models.BooleanField(default=False)
    added_to_parser = models.BooleanField(default=False)
    required = models.BooleanField(default=False)
    mapped_to = models.CharField(max_length=128, null=True, blank=True)

    def __str__(self):
        return f"{self.tech} -> {self.mapped_to}"


class JobMatch(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    core_foundation = models.OneToOneField('JobMatchCluster', on_delete=models.PROTECT, related_name='core_for_match')
    match_score = models.IntegerField()
    decision = models.CharField(max_length=16)
    reasoning = models.TextField()
    role_relevance = models.CharField(max_length=16, null=True, blank=True)
    candidate_level = models.CharField(max_length=16, null=True, blank=True)
    job_level = models.CharField(max_length=16, null=True, blank=True)
    salary_alignment = models.CharField(max_length=16, null=True, blank=True)
    applicant_context = models.TextField()
    job_context = models.TextField()
    prompt = models.TextField()

    def __str__(self):
        return f"{self.job.company}: {self.match_score}"
    
    
    def delete(self, *args, **kwargs):
        # 1. Grab a reference to the core cluster before the match is deleted
        core_cluster = self.core_foundation
        
        # 2. Call the standard Django deletion to delete the JobMatch (and its secondaries via CASCADE)
        super().delete(*args, **kwargs)
        
        # 3. Manually delete the orphaned core cluster
        if core_cluster:
            core_cluster.delete()

        # Written here as a reminder
        #     
        # JobMatchCluster.objects.filter(
        #     match__isnull=True, 
        #     core_for_match__isnull=True
        # ).delete()


class JobMatchCluster(models.Model):
    match = models.ForeignKey(JobMatch, on_delete=models.CASCADE, null=True, blank=True, related_name='secondary_clusters')
    area = models.CharField(max_length=128)
    job_requires = models.JSONField()
    candidate_has = models.JSONField()
    importance = models.CharField(max_length=16)
    coverage = models.CharField(max_length=16)

    def __str__(self):
        return (
            f"     [{self.importance}] {self.area}: {self.coverage} | "
            f"Job needs: {self.job_requires} | Candidate has: {self.candidate_has}"
        )


# ---------------------------------------------------------
# Auto-Apply Models
# ---------------------------------------------------------

APPLICATION_STATUS = {
    'QUEUED': 'Queued',
    'IN_PROGRESS': 'In Progress',
    'PENDING_REVIEW': 'Pending Review',
    'REVIEW_APPROVED': 'Review Approved',
    'SUBMITTED': 'Submitted',
    'SUBMIT_FAILED': 'Submit Failed',
    'ESCALATED': 'Escalated',
    'SKIPPED': 'Skipped',
    'CANCELLED': 'Cancelled',
}


class PlatformAccount(models.Model):
    """Tracks a login-capable account on a job platform or ATS."""
    platform = models.CharField(max_length=32, unique=True)  # 'linkedin', 'naukri', 'greenhouse', etc.
    username = models.CharField(max_length=128, blank=True)
    # Password stored in env vars, not here — this just tracks which accounts exist
    login_url = models.URLField(blank=True)
    is_healthy = models.BooleanField(default=True)  # flipped to False if account gets flagged/blocked
    last_used = models.DateTimeField(null=True)
    daily_count = models.IntegerField(default=0)  # reset by a daily cron/task
    daily_limit = models.IntegerField(default=50)  # configurable per platform
    notes = models.TextField(blank=True)  # e.g. "Got a warning on Sept 15"

    def __str__(self):
        return f"{self.platform} ({'healthy' if self.is_healthy else 'UNHEALTHY'})"


class ApplicationRun(models.Model):
    """One application attempt for one job."""
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='application_runs')
    status = models.CharField(max_length=24, choices=APPLICATION_STATUS, default='QUEUED')

    # Classification of the apply link (filled after first navigation)
    apply_type = models.CharField(max_length=24, null=True, blank=True)
    # One of: 'native_linkedin', 'native_naukri', 'ats_workday', 'ats_greenhouse',
    #         'ats_lever', 'ats_other', 'custom_site', 'email_only', 'no_link', 'expired'

    # LangGraph checkpoint ID for pause/resume
    checkpoint_id = models.CharField(max_length=128, null=True, blank=True)

    # Trust tracking
    had_fresh_decisions = models.BooleanField(default=False)  # True if any step was a cache miss
    auto_submit_eligible = models.BooleanField(default=False)

    # Bookkeeping
    skip_reason = models.CharField(max_length=128, null=True, blank=True)
    escalation_reason = models.TextField(null=True, blank=True)
    error_log = models.TextField(null=True, blank=True)

    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Run #{self.id} for {self.job.company} — {self.status}"


class StepRecord(models.Model):
    """One step in an application run — the full observe/act log."""
    run = models.ForeignKey(ApplicationRun, on_delete=models.CASCADE, related_name='steps')
    step_number = models.IntegerField()

    # Observation
    page_url = models.URLField(max_length=512)
    screenshot = models.ImageField(upload_to='apply_screenshots/', null=True, blank=True)
    dom_signature = models.CharField(max_length=128)  # hash of the form state

    # Decision
    source = models.CharField(max_length=16)  # 'cache_hit', 'llm_decision', 'profile_lookup'
    action_type = models.CharField(max_length=24)
    # One of: 'fill_field', 'click', 'select_dropdown', 'upload_file', 'navigate',
    #         'drag', 'submit', 'wait', 'escalate', 'skip'
    action_detail = models.JSONField()  # {"selector": "...", "value": "...", "field_label": "..."}

    # Result
    success = models.BooleanField(default=True)
    post_action_url = models.URLField(max_length=512, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['run', 'step_number']

    def __str__(self):
        return f"Step {self.step_number} [{self.source}] {self.action_type}"


class ActionSnippet(models.Model):
    """
    One atomic, LLM-generated browser interaction — cached for reuse.
    
    Each snippet represents ONE browser action (locate + interact + verify).
    Snippets are composable: the same "fill text input" interact snippet
    works regardless of platform, while the "locate first name on Workday"
    locate snippet is platform-specific.
    
    The LLM generates these as structured data (not arbitrary code).
    A fixed executor interprets them using a vocabulary of strategies,
    actions, and checks.
    
    Signature = hash of: (platform, field_label, snippet_type)
    Two Workday forms both asking for "First Name" share the same locate snippet.
    """
    # Identity
    signature = models.CharField(max_length=256, unique=True, db_index=True)
    snippet_type = models.CharField(max_length=16)
    # 'locate' — finds an element on the page
    # 'interact' — performs an action on a found element
    
    # Context
    platform = models.CharField(max_length=32, blank=True)  # 'workday', 'greenhouse', etc.
    field_label = models.CharField(max_length=128, blank=True)  # human-readable: "First Name"
    page_url_pattern = models.CharField(max_length=256, blank=True)  # URL with IDs stripped
    
    # The structured action (interpreted by the executor, NOT exec'd)
    action = models.JSONField()
    # For 'locate' snippets:
    # {
    #   "strategy": "css",              # css | role | text | label | xpath | id
    #   "selector": "input[name='firstName']",
    #   "fallbacks": ["#first-name", "[aria-label='First Name']"],
    # }
    #
    # For 'interact' snippets:
    # {
    #   "action": "fill",               # fill | click | select | upload | check | uncheck
    #   "value_source": "profile.first_name",  # resolved at runtime from profile/Q&A bank
    #   "input_type": "text",           # text | email | tel | file | select | checkbox | radio
    # }
    
    # LLM-generated verification (also structured, not code)
    verify = models.JSONField()
    # {
    #   "check": "element_exists",       # element_exists | value_matches | is_checked | 
    #                                    # page_changed | element_visible | option_selected
    #   "expected_tag": "input",         # optional: expected HTML tag
    #   "expected_attrs": {"type": "text"},  # optional: expected attributes
    #   "compare": "input_value",        # optional: what to compare against
    # }
    
    # Trust stats
    total_uses = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    consecutive_fails = models.IntegerField(default=0)  # reset on success
    last_used = models.DateTimeField(null=True)
    
    # Generation context
    llm_reasoning = models.TextField(null=True, blank=True)
    dom_context = models.TextField(null=True, blank=True)  # the DOM fragment the LLM saw when generating this
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['platform', 'field_label'], name='idx_snippet_platform_label'),
            models.Index(fields=['snippet_type'], name='idx_snippet_type'),
        ]
    
    def __str__(self):
        return (
            f"[{self.snippet_type}] {self.platform}:{self.field_label} "
            f"— {self.success_count}/{self.total_uses} success"
        )

