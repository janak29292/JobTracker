import json
from django.contrib import admin, messages
from django.db.models import Q

from job.models import Job, Posting, FormData, Session, JobUrl, JobRaw, StatusEvent, TechStack, UnknownTech

# Register your models here.
admin.site.register(Job)
admin.site.register(Posting)
admin.site.register(FormData)
admin.site.register(Session)
admin.site.register(JobUrl)
admin.site.register(JobRaw)
admin.site.register(StatusEvent)
admin.site.register(TechStack)

@admin.register(UnknownTech)
class UnknownTechAdmin(admin.ModelAdmin):
    list_display = ('tech', 'mapped_to', 'processed', 'accepted', 'added_to_parser', 'added_to_job')
    list_filter = ('processed', 'accepted', 'added_to_parser', 'added_to_job')
    search_fields = ('tech', 'mapped_to')
    actions = ['reject_and_scrub']

    @admin.action(description="Reject and scrub from Jobs & TechStack")
    def reject_and_scrub(self, request, queryset):
        techs_to_scrub = set()
        for ut in queryset:
            if ut.mapped_to:
                techs_to_scrub.add(ut.mapped_to)
            else:
                techs_to_scrub.add(ut.tech)
        
        if not techs_to_scrub:
            self.message_user(request, "No keywords to scrub.", messages.WARNING)
            return

        # 1. Reject in UnknownTech
        queryset.update(accepted=False, processed=True)
        
        # 2. Delete from TechStack
        TechStack.objects.filter(name__in=techs_to_scrub).delete()
        
        # 3. Scrub from Jobs
        q_objects = Q()
        for t in techs_to_scrub:
            q_objects |= Q(tech_stack_all_new__icontains=f'"{t}"')
            q_objects |= Q(tech_stack_primary_new__icontains=f'"{t}"')
            
        jobs_to_scrub = Job.objects.filter(q_objects)
        jobs_updated = 0
        
        for job in jobs_to_scrub:
            modified = False
            
            try:
                all_new = json.loads(job.tech_stack_all_new) if job.tech_stack_all_new else []
                original_len = len(all_new)
                all_new = [t for t in all_new if t not in techs_to_scrub]
                if len(all_new) != original_len:
                    job.tech_stack_all_new = json.dumps(all_new)
                    modified = True
            except json.JSONDecodeError:
                pass
                
            try:
                primary_new = json.loads(job.tech_stack_primary_new) if job.tech_stack_primary_new else []
                original_len = len(primary_new)
                primary_new = [t for t in primary_new if t not in techs_to_scrub]
                if len(primary_new) != original_len:
                    job.tech_stack_primary_new = json.dumps(primary_new)
                    modified = True
            except json.JSONDecodeError:
                pass
                
            if modified:
                job.save(update_fields=['tech_stack_all_new', 'tech_stack_primary_new'])
                jobs_updated += 1
                
        self.message_user(request, f"Rejected {queryset.count()} terms. Scrubbed them out of {jobs_updated} jobs and removed from TechStack.", messages.SUCCESS)
