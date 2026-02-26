import django_filters
from django.db.models import Q
from django import forms
from django_filters import rest_framework as filters, BaseInFilter

from job.models import Job, TechStack, Posting


class SearchFilter(django_filters.Filter):

    field_class = forms.Field

    def filter(self, queryset, value):
        if not value:
            return queryset
        # Construct the Q object to apply the 'icontains' lookup with OR conditions
        return queryset.filter(
            Q(tech_stack_all__icontains=value) |
            Q(company__icontains=value) |
            Q(job_location__icontains=value)
        )


class JSONListFilter(django_filters.Filter):
    """
    Filters a JSON list field to contain any of the provided values (OR logic).
    Values should be provided as a comma-separated string, e.g., "postgresql,docker".
    """
    field_class = forms.Field  # Can also use a custom ListField for multi-select widget

    def filter(self, qs, value):
        if not value:
            return qs

        values = [v.strip() for v in value.split(',') if v.strip()]

        q_objects = Q()
        for v in values:
            q_objects &= Q(**{f"{self.field_name}__contains": v})

        # Apply the filter
        filtered_qs = qs.filter(q_objects)
        return filtered_qs


class JobFilter(filters.FilterSet):
    status__not__in = BaseInFilter(
        field_name="status",
        lookup_expr="in",
        exclude=True
    )
    tech_stacks = JSONListFilter(
        field_name='tech_stack_all'
    )
    search = SearchFilter(
        field_name='search'
    )

    class Meta:
        model = Job
        # fields = '__all__'
        fields = {
            # # 'price': ['lt', 'gt'],
            # # 'last_posted': ['exact', 'year__gt'],
            # 'company': ['icontains'],
            'tech_stack_all': ['icontains'],
            'status': ['in'],
            'experience_min': ['gte'],
            'experience_max': ['lte'],
        }


class TechStackFilter(filters.FilterSet):

    class Meta:
        model = TechStack
        fields = {
            'name': ['icontains']
        }


class PostingFilter(filters.FilterSet):
    class Meta:
        model = Posting
        fields = '__all__'
