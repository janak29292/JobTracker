# from django_filters import OrderingFilter
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
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


class DailyCountView(APIView):

    def get(self, request, *args, **kwargs):
        return Response({
            "applied_today": Job.objects.filter(applied_on=timezone.localtime(timezone.now()).date()).count()
        })


## 📊 Analytics Module



class AnalyticViewSet(ViewSet):

    def list(self, request, pk=None):
        """
        ### 1. Get Analytics Summary

        **Endpoint**: `GET /analytics/summary`

        **Description**: Returns overall statistics for the analytics dashboard.
        """
        return Response(
            {
                "total_applications": 245,
                "response_rate": 18.5,
                "avg_days_to_response": 7.2,
                "interview_conversion_rate": 12.3,
                "status_breakdown": {
                    "IG": 45,
                    "NA": 120,
                    "AF": 60,
                    "CR": 12,
                    "IS": 5,
                    "RP": 2,
                    "RE": 30,
                    "OR": 1
                }
            }
        )


class AnalticsChannelViewSet(ViewSet):

    def list(self, request):
        """
        ### 2. Get Channel Performance
        **Endpoint**: `GET /analytics/channels`

        **Description**: Performance metrics by application channel.
        """
        return Response(
            {
                "channels": [
                    {
                        "name": "LinkedIn",
                        "applications": 120,
                        "responses": 25,
                        "response_rate": 20.8,
                        "interviews": 8
                    },
                    {
                        "name": "Naukri",
                        "applications": 80,
                        "responses": 12,
                        "response_rate": 15.0,
                        "interviews": 3
                    },
                    {
                        "name": "Direct",
                        "applications": 30,
                        "responses": 8,
                        "response_rate": 26.7,
                        "interviews": 4
                    },
                    {
                        "name": "Referral",
                        "applications": 15,
                        "responses": 10,
                        "response_rate": 66.7,
                        "interviews": 6
                    }
                ]
            }
        )


class AnalyticsTechDemandViewSet(ViewSet):

    def list(self, request):
        """
        ### 3. Get Tech Stack Demand
        **Endpoint**: `GET /analytics/tech-demand`

        **Description**: Most requested technologies across all job postings.

        **Query Parameters**:
        - `limit` (optional, default: 20) - Number of top technologies to return
        """
        return Response(
            {
                "tech_demand": [
                    {
                        "name": "Python",
                        "count": 180,
                        "percentage": 73.5
                    },
                    {
                        "name": "React",
                        "count": 95,
                        "percentage": 38.8
                    },
                    {
                        "name": "Docker",
                        "count": 85,
                        "percentage": 34.7
                    },
                    {
                        "name": "FastAPI",
                        "count": 70,
                        "percentage": 28.6
                    },
                    {
                        "name": "Kubernetes",
                        "count": 60,
                        "percentage": 24.5
                    }
                ]
            }
        )


class AnalyticsVelocityViewSet(ViewSet):

    def list(self, request):
        """
        ### 4. Get Application Velocity
        **Endpoint**: `GET /analytics/velocity`

        **Description**: Daily/weekly application tracking.

        **Query Parameters**:
        - `period` (optional, default: "week") - "day", "week", or "month"
        """
        return Response(
            {
                "period": "week",
                "target": 140,
                "actual": 132,
                "daily_breakdown": [
                    {
                        "date": "2024-12-01",
                        "applications": 22,
                        "target": 20
                    },
                    {
                        "date": "2024-12-02",
                        "applications": 18,
                        "target": 20
                    },
                    {
                        "date": "2024-12-03",
                        "applications": 25,
                        "target": 20
                    },
                    {
                        "date": "2024-12-04",
                        "applications": 19,
                        "target": 20
                    },
                    {
                        "date": "2024-12-05",
                        "applications": 21,
                        "target": 20
                    },
                    {
                        "date": "2024-12-06",
                        "applications": 15,
                        "target": 20
                    },
                    {
                        "date": "2024-12-07",
                        "applications": 12,
                        "target": 20
                    }
                ]
            }
        )


class AnalyticsGhostingViewSet(ViewSet):

    def list(self, request):
        """
        ### 5. Get Ghosting Analysis
        **Endpoint**: `GET /analytics/ghosting`

        **Description**: Jobs with no response after specified days.

        **Query Parameters**:
        - `days_threshold` (optional, default: 14) - Days without response
        """
        return Response(
            {
                "threshold_days": 14,
                "total_ghosted": 87,
                "ghosted_jobs": [
                    {
                        "id": 123,
                        "company": "TechCorp",
                        "position": "Senior Developer",
                        "applied_on": "2024-11-15",
                        "days_since_application": 18,
                        "channel": "LinkedIn"
                    },
                    {
                        "id": 124,
                        "company": "StartupXYZ",
                        "position": "Backend Engineer",
                        "applied_on": "2024-11-10",
                        "days_since_application": 23,
                        "channel": "Naukri"
                    },
                    {
                        "id": 125,
                        "company": "FinTech Inc",
                        "position": "Python Developer",
                        "applied_on": "2024-11-08",
                        "days_since_application": 25,
                        "channel": "Direct"
                    }
                ]
            }
        )

