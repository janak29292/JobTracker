# from django_filters import OrderingFilter
from collections import defaultdict, Counter
from datetime import timedelta
import json

from dateutil.relativedelta import relativedelta
from django.db.models import DateField, Count, Q, FloatField, ExpressionWrapper, F, OuterRef, Subquery, IntegerField, Case, When, Value, Avg, DurationField
from django.db.models.functions import Now, Trunc, TruncDate, Coalesce, Round, Cast
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ViewSet

from job.filters import JobFilter, TechStackFilter, PostingFilter
from job.models import JobUrl, JobRaw, Job, TechStack, Posting
from job.serializers import UrlSerializer, RawJobSerializer, JobSerializer, TechStackSerializer, PostingSerializer


# Create your views here.


class URLViewSet(ModelViewSet):
    queryset = JobUrl.objects.all()
    serializer_class = UrlSerializer


class RawJobViewSet(ModelViewSet):
    queryset = JobRaw.objects.all()
    serializer_class = RawJobSerializer


class JobViewSet(ModelViewSet):
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = JobFilter

    @action(detail=False, methods=["get"], url_path="status-trends")
    def status_trends(self, request):

        # You can dynamically change this to 'day', 'week', 'month', 'quarter', or 'year'
        start_date = request.GET.get("date_from") or Now() - timedelta(days=7)
        end_date = request.GET.get("date_to") or Now()
        frequency = request.GET.get("frequency") or 'day'


        base_jobs = self.queryset.exclude(status__in=['NA', 'IG'])

        # 2. Execute the queries using your DB expressions
        # We wrap them in list() to force the DB to execute and give us the data now
        applied_data = list(
            base_jobs
            .filter(applied_on__range=(start_date, end_date))
            .annotate(period=Trunc('applied_on', kind=frequency, output_field=DateField()))
            .values('period')
            .annotate(applied_count=Count('id'))
        )

        other_status_data = list(
            base_jobs
            .exclude(status='AF')
            .filter(last_interaction__range=(start_date, end_date))
            .annotate(period=Trunc('last_interaction', kind=frequency, output_field=DateField()))
            .values('period', 'status')
            .annotate(status_count=Count('id'))
        )

        # 3. Extract all the dates the database calculated for us
        all_returned_dates = [
            item['period'] for item in applied_data + other_status_data if item['period']
        ]

        timeline = defaultdict(lambda: {"status_breakdown": {}})

        # 4. Generate the continuous timeline in Python based on DB results
        if all_returned_dates:
            # Get the earliest and latest dates from our DB results
            current_date = min(all_returned_dates)
            actual_end_date = max(all_returned_dates)

            # --- THE TRULY DYNAMIC STEP ---
            if not frequency:
                step = relativedelta(days=1)
            elif frequency == 'quarter':
                step = relativedelta(months=3)
            else:
                step = relativedelta(**{f'{frequency}s': 1})

            while current_date <= actual_end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                timeline[date_str] = {"status_breakdown": {}}
                current_date += step

        # 5. Populate the timeline just like before
        for item in applied_data:
            if not item['period']: continue
            period_str = item['period'].strftime('%Y-%m-%d')
            timeline[period_str]["status_breakdown"]["AF"] = item['applied_count']

        for item in other_status_data:
            if not item['period']: continue
            period_str = item['period'].strftime('%Y-%m-%d')
            timeline[period_str]["status_breakdown"][item['status']] = item['status_count']

        final_timeline = [{"period": key, **value } for key, value in dict(timeline).items()]
        return Response(final_timeline)

    @action(detail=False, methods=["get"], url_path="daily-count")
    def daily_count(self, request):
        return Response({
            "applied_today": self.queryset.filter(
                applied_on=TruncDate(Now())
            ).count()
        })

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        base_jobs = self.queryset.exclude(status='IG')

        is_applied = Q(applied_on__isnull=False)
        is_response = is_applied & ~Q(status__in=['AF', 'NA', 'RE'])
        is_offer = Q(status='OR')

        metrics = base_jobs.aggregate(
            total_applications=Count('id', filter=is_applied),
            response_rate=Coalesce(
                Round(
                    ExpressionWrapper(
                        Count('id', filter=is_response) * 100.0 / Count('id', filter=is_applied),
                        output_field=FloatField()
                    ), 1
                ), 0.0
            ),
            interview_conversion_rate=Coalesce(
                Round(
                    ExpressionWrapper(
                        Count('id', filter=is_offer) * 100.0 / Count('id', filter=is_applied),
                        output_field=FloatField()
                    ), 1
                ), 0.0
            )
        )

        avg_response = base_jobs.filter(
            applied_on__isnull=False,
            first_response_date__isnull=False,
        ).filter(is_response).aggregate(
            avg_duration=Coalesce(
                Avg(ExpressionWrapper(
                    F('first_response_date') - F('applied_on'),
                    output_field=DurationField()
                )),
                Value(timedelta(0))
            )
        )

        return Response({
            "total_applications": metrics['total_applications'],
            "response_rate": metrics['response_rate'],
            "avg_days_to_response": avg_response['avg_duration'].days,
            "interview_conversion_rate": metrics['interview_conversion_rate'],
        })

    @action(detail=False, methods=["get"], url_path="channels")
    def channels(self, request):
        base_jobs = self.queryset.exclude(status='IG').filter(applied_on__isnull=False)
        
        is_response = ~Q(status__in=['AF', 'NA', 'RE'])
        is_interview = Q(status__in=['IS', 'NE', 'OR'])

        channel_stats = base_jobs.annotate(name=F('platform')).values('name').annotate(
            applications=Count('id'),
            responses=Count('id', filter=is_response),
            interviews=Count('id', filter=is_interview),
            response_rate=Coalesce(
                Round(
                    ExpressionWrapper(
                        Count('id', filter=is_response) * 100.0 / Count('id'),
                        output_field=FloatField()
                    ), 1
                ), 0.0
            )
        )

        return Response({"channels": list(channel_stats)})

    @action(detail=False, methods=["get"], url_path="tech-demand")
    def tech_demand(self, request):
        
        limit = int(request.GET.get('limit', 20))
        base_jobs = self.queryset.exclude(status='IG')
        
        # Calculate denominator for percentages
        total_jobs_with_tech = base_jobs.exclude(tech_stack_all__isnull=True).exclude(tech_stack_all__exact='').count()

        # Fetch all target techs to check
        all_techs = TechStack.objects.values_list('name', flat=True)

        aggregation_kwargs = {}
        alias_to_name_map = {}

        # Build dynamic pivot columns for single database pass calculation
        for i, tech_name in enumerate(all_techs):
            safe_alias = f"t_{i}"
            alias_to_name_map[safe_alias] = tech_name
            aggregation_kwargs[safe_alias] = Count(
                Case(
                    When(tech_stack_all__icontains=f'"{tech_name}"', then=1),
                    output_field=IntegerField()
                )
            )

        # Single-pass execution on Database (takes ~0.5s instead of 190s)
        tech_counts_raw = base_jobs.aggregate(**aggregation_kwargs)

        whens = []
        for safe_alias, count in tech_counts_raw.items():
            if count and count > 0:
                tech_name = alias_to_name_map[safe_alias]
                whens.append(When(name=tech_name, then=Value(count)))
        
        if whens:
            
            top_techs = TechStack.objects.annotate(
                count=Case(*whens, default=Value(0), output_field=IntegerField())
            ).filter(count__gt=0).annotate(
                percentage=Round(
                    Cast(F('count') * 100, FloatField()) / Value(total_jobs_with_tech if total_jobs_with_tech > 0 else 1, output_field=FloatField()), 
                    1
                )
            ).order_by('-count')[:limit]
        else:
            top_techs = TechStack.objects.none()

        # # Format the response
        # response_data = []
        # for tech in top_techs:
        #     response_data.append({
        #         "name": tech.name,
        #         "count": tech.count,
        #         "percentage": round(tech.percentage, 1) if tech.percentage else 0.0
        #     })
            
        return Response(
            {"tech_demand": top_techs.values('name', 'count', 'percentage')}
        )

    @action(detail=False, methods=["get"], url_path="velocity")
    def velocity(self, request):
        period = request.GET.get('period', 'week')
        
        now = timezone.localtime(timezone.now()).date()
        
        # Target logic: assume a default target or param. E.g., 20 per day.
        daily_target = 20
        
        if period == 'week':
            # Current week starting Sunday (isoweekday: Mon=1, Sun=7)
            days_since_sunday = now.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
            start_date = now - timedelta(days=days_since_sunday)
        elif period == 'month':
            start_date = now - relativedelta(months=1)
        else:
            start_date = now - timedelta(days=6) # fallback
            
        applied_jobs = self.queryset.filter(applied_on__range=(start_date, now))\
            .values('applied_on')\
            .annotate(applications=Count('id'))\
            .order_by('applied_on')
            
        # Map to dict
        actual_data = {item['applied_on']: item['applications'] for item in applied_jobs}
        
        # Calculate end of period
        if period == 'week':
            end_date = start_date + timedelta(days=6)  # Full Sun-Sat
        elif period == 'month':
            end_date = now
        else:
            end_date = now

        daily_breakdown = []
        current_date = start_date
        total_actual = 0
        
        while current_date <= end_date:
            if current_date <= now:
                apps = actual_data.get(current_date, 0)
                total_actual += apps
            else:
                apps = None  # Future day
            daily_breakdown.append({
                "date": current_date.strftime('%Y-%m-%d'),
                "applications": apps,
                "target": daily_target
            })
            current_date += timedelta(days=1)
            
        total_days = (end_date - start_date).days + 1
        total_target = daily_target * total_days

        return Response({
            "period": period,
            "target": total_target,
            "actual": total_actual,
            "daily_breakdown": daily_breakdown
        })

    @action(detail=False, methods=["get"], url_path="ghosting")
    def ghosting(self, request):
        days_threshold = int(request.GET.get('days_threshold', 14))
        
        cutoff_date = timezone.localtime(timezone.now()).date() - timedelta(days=days_threshold)
        
        total_ghosted = self.queryset.filter(
            status='AF',
            applied_on__lte=cutoff_date
        ).count()

        base_jobs = self.queryset.exclude(status='IG')
        status_breakdown = base_jobs.values('status').annotate(count=Count('id'))

        return Response({
            "threshold_days": days_threshold,
            "total_ghosted": total_ghosted,
            "status_breakdown": status_breakdown
        })

class TechStackViewSet(ModelViewSet):
    queryset = TechStack.objects.all()
    serializer_class = TechStackSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend]
    filterset_class = TechStackFilter


class PostingViewSet(ModelViewSet):
    queryset = Posting.objects.all()
    serializer_class = PostingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = PostingFilter

