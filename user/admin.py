from django.contrib import admin
from .models import (
    User, Unstructured, Category, Pattern, Problem, Approach,
    ApplicantProfile, Education, Project, WorkExperience,
    BulletPoint, Answer, Question, Dealbreaker
)

admin.site.register(User)
admin.site.register(Unstructured)
admin.site.register(Category)
admin.site.register(Pattern)
admin.site.register(Problem)
admin.site.register(Approach)

# Phase 1 Applicant Models
admin.site.register(ApplicantProfile)
admin.site.register(Education)
admin.site.register(Project)
admin.site.register(WorkExperience)
admin.site.register(BulletPoint)
admin.site.register(Answer)
admin.site.register(Question)
admin.site.register(Dealbreaker)