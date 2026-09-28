# from django_filters import OrderingFilter
from collections import defaultdict, Counter
from datetime import timedelta
from job.tasks import resume_application
import json
from pathlib import Path
from rest_framework import status
from rest_framework.exceptions import ValidationError

from dateutil.relativedelta import relativedelta
from django.db.models import DateField, Count, Q, FloatField, ExpressionWrapper, F, OuterRef, Subquery, IntegerField, Case, When, Value, Avg, DurationField, Exists
from django.db.models.functions import Now, Trunc, TruncDate, Coalesce, Round, Cast
from django.utils import timezone
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ViewSet

from job.filters import JobFilter, TechStackFilter, PostingFilter
from job.models import JobUrl, JobRaw, Job, TechStack, Posting, JobMatch, UnknownTech, ApplicationRun
from job.serializers import UrlSerializer, RawJobSerializer, JobSerializer, TechStackSerializer, PostingSerializer, JobMatchSerializer, ApplicationRunSerializer


# Create your views here.


class URLViewSet(ModelViewSet):
    queryset = JobUrl.objects.all()
    serializer_class = UrlSerializer


class RawJobViewSet(ModelViewSet):
    queryset = JobRaw.objects.all()
    serializer_class = RawJobSerializer


class JobViewSet(ModelViewSet):
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = JobFilter

    def get_queryset(self):
        latest_match_score = Subquery(
            JobMatch.objects.filter(job=OuterRef('pk')).order_by('-id').values('match_score')[:1]
        )
        latest_decision = Subquery(
            JobMatch.objects.filter(job=OuterRef('pk')).order_by('-id').values('decision')[:1]
        )
        return Job.objects.all().annotate(
            latest_match_score=latest_match_score,
            latest_decision=latest_decision
        )

    @action(detail=False, methods=["get"], url_path="status-trends")
    def status_trends(self, request):

        # You can dynamically change this to 'day', 'week', 'month', 'quarter', or 'year'
        start_date = request.GET.get("date_from") or Now() - timedelta(days=7)
        end_date = request.GET.get("date_to") or Now()
        frequency = request.GET.get("frequency") or 'day'


        base_jobs = self.get_queryset().exclude(status__in=['NA', 'IG'])

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
            "applied_today": self.get_queryset().filter(
                applied_on=TruncDate(Now())
            ).count()
        })

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        base_jobs = self.get_queryset().exclude(status='IG')

        is_applied = Q(applied_on__isnull=False)
        is_response = Q(first_response_date__isnull=False)
        # is_response = is_applied & ~Q(status__in=['AF', 'NA', 'RE'])
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

        avg_response = base_jobs.filter(is_response).aggregate(
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
            "avg_days_to_response": round(avg_response['avg_duration'].total_seconds() / 86400, 1),
            "interview_conversion_rate": metrics['interview_conversion_rate'],
        })

    @action(detail=False, methods=["get"], url_path="channels")
    def channels(self, request):
        base_jobs = self.get_queryset().exclude(status='IG')

        is_applied = Q(applied_on__isnull=False)
        
        # is_response = ~Q(status__in=['AF', 'NA', 'RE'])
        is_interview = Q(status__in=['IS', 'NE', 'OR'])

        is_response = Q(first_response_date__isnull=False)

        channel_stats = base_jobs.filter(is_applied).annotate(name=F('platform')).values('name').annotate(
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
        base_jobs = self.get_queryset().exclude(status='IG')
        
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
            # # Current week starting Sunday (isoweekday: Mon=1, Sun=7)
            # days_since_sunday = now.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
            # start_date = now - timedelta(days=days_since_sunday)

            # Current week starting Monday (isoweekday: Mon=1, Sun=7)
            days_since_monday = now.isoweekday() - 1  # Mon=0, Tue=1, ..., Sun=6
            start_date = now - timedelta(days=days_since_monday)
        elif period == 'month':
            start_date = now - relativedelta(months=1)
        else:
            start_date = now - timedelta(days=6) # fallback
            
        applied_jobs = self.get_queryset().filter(applied_on__range=(start_date, now))\
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
        
        total_ghosted = self.get_queryset().filter(
            status='AF',
            applied_on__lte=cutoff_date
        ).count()

        base_jobs = self.get_queryset().exclude(status='IG')
        status_breakdown = base_jobs.values('status').annotate(count=Count('id'))

        return Response({
            "threshold_days": days_threshold,
            "total_ghosted": total_ghosted,
            "status_breakdown": status_breakdown
        })

class TechStackViewSet(ModelViewSet):
    serializer_class = TechStackSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend]
    filterset_class = TechStackFilter

    def get_queryset(self):
        return TechStack.objects.all().annotate(
            has_context=Exists(UnknownTech.objects.filter(mapped_to=OuterRef('name')))
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        tech = self.get_object()
        tech_name = tech.name
        
        # 1. Clean up UnknownTech references
        UnknownTech.objects.filter(mapped_to=tech_name).update(
            mapped_to=None, 
            accepted=False,
            added_to_parser=False,
            added_to_job=False
        )
        
        # 2. Clean up Job JSON strings (techs are stored as serialized lists like '["react", "data"]')
        jobs_with_tech = list(Job.objects.filter(
            Q(tech_stack_all__icontains=f'"{tech_name}"') |
            Q(tech_stack_primary__icontains=f'"{tech_name}"') |
            Q(tech_stack_all_new__icontains=f'"{tech_name}"') |
            Q(tech_stack_primary_new__icontains=f'"{tech_name}"')
        ))
        
        for job in jobs_with_tech:
            def remove_from_json_str(json_str):
                if not json_str: return json_str
                try:
                    lst = json.loads(json_str)
                    if tech_name in lst:
                        lst.remove(tech_name)
                    return json.dumps(lst)
                except:
                    return json_str

            job.tech_stack_all = remove_from_json_str(job.tech_stack_all)
            job.tech_stack_primary = remove_from_json_str(job.tech_stack_primary)
            job.tech_stack_all_new = remove_from_json_str(job.tech_stack_all_new)
            job.tech_stack_primary_new = remove_from_json_str(job.tech_stack_primary_new)
            
        if jobs_with_tech:
            Job.objects.bulk_update(jobs_with_tech, [
                'tech_stack_all', 'tech_stack_primary', 
                'tech_stack_all_new', 'tech_stack_primary_new'
            ], batch_size=100)
        
        # 3. Proceed with standard deletion
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='context')
    def get_context(self, request):
        tech_id = request.GET.get('id')
        if not tech_id:
            return Response({"error": "id parameter is required"}, status=400)
        try:
            tech = TechStack.objects.get(id=tech_id)
        except TechStack.DoesNotExist:
            return Response({"error": "TechStack not found"}, status=404)

        unknowns = UnknownTech.objects.filter(mapped_to=tech.name).select_related('job')
        contexts = []
        for u in unknowns:
            if u.job:
                contexts.append({
                    'raw_tech': u.tech,
                    'job_id': u.job.id,
                    'job_description': u.job.job_description
                })
        return Response(contexts)



class PostingViewSet(ModelViewSet):
    queryset = Posting.objects.all()
    serializer_class = PostingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = PostingFilter

UNKNOWN_TECHS_FILE = Path('buffer/unknown_techs.jsonl')

class UnknownTechViewSet(ViewSet):
    def list(self, request):
        status_param = request.GET.get('status', 'pending')
        
        items = []
        if UNKNOWN_TECHS_FILE.exists():
            with open(UNKNOWN_TECHS_FILE, 'r') as f:
                for line in f:
                    if not line.strip(): continue
                    data = json.loads(line)
                    
                    if status_param == 'pending':
                        if not data.get('processed', False):
                            items.append(data)
                    elif status_param == 'pending_parser':
                        if data.get('accepted', False) and not data.get('added_to_parser', False):
                            items.append(data)
                    elif status_param == 'pending_job':
                        if data.get('added_to_parser', False) and not data.get('added_to_job', False):
                            items.append(data)
        
        # Sort items by job_id
        items.sort(key=lambda x: x.get('job_id', 0))

        # Fetch job_description context
        job_ids = [item['job_id'] for item in items]
        jobs = Job.objects.filter(id__in=job_ids).values('id', 'job_description')
        job_map = {job['id']: job['job_description'] for job in jobs}
        
        for item in items:
            item['job_description'] = job_map.get(item['job_id'], '')

        return Response(items)

    @action(detail=False, methods=['get'], url_path='counts')
    def counts(self, request):
        counts = {
            "total": 0,
            "pending": 0,
            "pending_parser": 0,
            "pending_job": 0
        }
        if UNKNOWN_TECHS_FILE.exists():
            with open(UNKNOWN_TECHS_FILE, 'r') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                    except Exception as e:
                        print(line)
                        raise e
                    counts["total"] += 1
                    
                    if not data.get('processed', False):
                        counts["pending"] += 1
                    elif data.get('accepted', False) and not data.get('added_to_parser', False):
                        counts["pending_parser"] += 1
                    elif data.get('added_to_parser', False) and not data.get('added_to_job', False):
                        counts["pending_job"] += 1
                        
        return Response(counts)

    @action(detail=False, methods=['post'], url_path='action')
    def handle_action(self, request):
        payload = request.data
        if not payload:
            return Response({"error": "Payload required"}, status=status.HTTP_400_BAD_REQUEST)
        
        target_tech = payload.get('tech')
        target_job_id = payload.get('job_id')
        is_accepted = payload.get('accepted', False)
        mapped_to = payload.get('mapped_to')

        if is_accepted and not mapped_to:
            raise ValidationError("mapped_to is required when accepting.")

        updated_lines = []
        found = False
        if UNKNOWN_TECHS_FILE.exists():
            with open(UNKNOWN_TECHS_FILE, 'r') as f:
                lines = f.readlines()
            
            for line in lines:
                if not line.strip(): continue
                data = json.loads(line)
                if data.get('tech') == target_tech and data.get('job_id') == target_job_id:
                    # Merge payload into data (keep JSON consistency)
                    for k, v in payload.items():
                        data[k] = v
                    found = True
                updated_lines.append(json.dumps(data) + '\n')
            
            if found:
                with open(UNKNOWN_TECHS_FILE, 'w') as f:
                    f.writelines(updated_lines)

        if not found:
            return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response({"status": "success"})

    @action(detail=False, methods=['post'], url_path='add-to-jobs')
    def add_to_jobs(self, request):
        items = request.data
        if not isinstance(items, list):
            return Response({"error": "Expected a list"}, status=status.HTTP_400_BAD_REQUEST)

        if not UNKNOWN_TECHS_FILE.exists():
            return Response({"error": "File not found"}, status=status.HTTP_404_NOT_FOUND)

        with open(UNKNOWN_TECHS_FILE, 'r') as f:
            lines = f.readlines()

        item_map = {(i.get('tech'), i.get('job_id')): i for i in items if isinstance(i, dict)}
        updated_lines = []

        # Step 1: Collect valid job IDs for bulk fetching
        job_ids = set()
        for line in lines:
            if not line.strip(): continue
            data = json.loads(line)
            key = (data.get('tech'), data.get('job_id'))
            if key in item_map and data.get('added_to_parser', False) and not data.get('added_to_job', False):
                job_ids.add(data.get('job_id'))

        # Step 2: Fetch all needed jobs at once
        jobs_map = Job.objects.in_bulk(job_ids)
        modified_jobs = {}

        for line in lines:
            if not line.strip(): continue
            data = json.loads(line)
            key = (data.get('tech'), data.get('job_id'))
            
            if key in item_map:
                req_item = item_map[key]
                if data.get('added_to_parser', False) and not data.get('added_to_job', False):
                    job_id = data.get('job_id')
                    job = jobs_map.get(job_id)
                    if job:
                        mapped_to = data.get('mapped_to') or req_item.get('mapped_to')
                        if mapped_to:
                            all_new = json.loads(job.tech_stack_all_new) if job.tech_stack_all_new else []
                            if mapped_to not in all_new:
                                all_new.append(mapped_to)
                            job.tech_stack_all_new = json.dumps(all_new)

                            if data.get('required') or req_item.get('required'):
                                primary_new = json.loads(job.tech_stack_primary_new) if job.tech_stack_primary_new else []
                                if mapped_to not in primary_new:
                                    primary_new.append(mapped_to)
                                job.tech_stack_primary_new = json.dumps(primary_new)

                            modified_jobs[job.id] = job
                            data['added_to_job'] = True
                            # if not data.get('mapped_to'):
                            #     data['mapped_to'] = mapped_to
            
            updated_lines.append(json.dumps(data) + '\n')

        # Step 3: Bulk save to avoid SQLite disk I/O locking
        if modified_jobs:
            Job.objects.bulk_update(modified_jobs.values(), ['tech_stack_all_new', 'tech_stack_primary_new'])

        with open(UNKNOWN_TECHS_FILE, 'w') as f:
            f.writelines(updated_lines)

        return Response({"status": "success"})


class JobMatchViewSet(ModelViewSet):
    queryset = JobMatch.objects.all().prefetch_related('core_foundation', 'secondary_clusters')
    serializer_class = JobMatchSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['job']
    ordering_fields = ['-id']


class ApplicationRunViewSet(ModelViewSet):
    """ViewSet for reviewing and managing auto-apply runs."""
    queryset = ApplicationRun.objects.all().select_related('job').prefetch_related('steps')
    serializer_class = ApplicationRunSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['job', 'status']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approve a pending review application and trigger resume."""
        run = self.get_object()
        if run.status != 'PENDING_REVIEW':
            return Response(
                {'error': f'Run is {run.status}, not PENDING_REVIEW'},
                status=status.HTTP_400_BAD_REQUEST
            )
        run.status = 'REVIEW_APPROVED'
        run.save()

        # Trigger async resume
        resume_application.delay(run.id)

        return Response({'status': 'approved', 'run_id': run.id})

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Reject a pending review application."""
        run = self.get_object()
        if run.status != 'PENDING_REVIEW':
            return Response(
                {'error': f'Run is {run.status}, not PENDING_REVIEW'},
                status=status.HTTP_400_BAD_REQUEST
            )
        run.status = 'CANCELLED'
        run.save()

        # Revert job status back to Matched
        job = run.job
        job.status = 'MA'
        job.save()

        return Response({'status': 'rejected', 'run_id': run.id})

    @action(detail=False, methods=['get'], url_path='pending')
    def pending_reviews(self, request):
        """Get all runs pending review — used by the review tab."""
        pending = self.queryset.filter(status='PENDING_REVIEW')
        serializer = self.get_serializer(pending, many=True)
        return Response(serializer.data)
