from django.contrib import admin

from job.models import Job, Posting, FormData, Session, JobUrl, JobRaw

# Register your models here.
admin.site.register(Job)
admin.site.register(Posting)
admin.site.register(FormData)
admin.site.register(Session)
admin.site.register(JobUrl)
admin.site.register(JobRaw)
