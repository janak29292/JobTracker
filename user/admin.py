from django.contrib import admin

from user.models import User, Unstructured, Category, Pattern, Problem, Approach

# Register your models here.
admin.site.register(User)
admin.site.register(Unstructured)
admin.site.register(Category)
admin.site.register(Pattern)
admin.site.register(Problem)
admin.site.register(Approach)