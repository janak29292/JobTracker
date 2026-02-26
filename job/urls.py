from django.urls import path
from rest_framework.routers import DefaultRouter

from job.views import URLViewSet, RawJobViewSet, JobViewSet, TechStackViewSet, DailyCountView, PostingViewSet, \
    AnalyticViewSet, AnalticsChannelViewSet, AnalyticsTechDemandViewSet, AnalyticsVelocityViewSet, \
    AnalyticsGhostingViewSet

router = DefaultRouter()
router.register(r'urls', URLViewSet, basename='url')
router.register(r'raw', RawJobViewSet, basename='raw')
router.register(r'jobs', JobViewSet, basename='jobs')
router.register(r'techs', TechStackViewSet, basename='techs')
router.register(r'postings', PostingViewSet, basename='postings')

# 📊 Analytics
router.register(r"analytics/summary", AnalyticViewSet, basename="analytics-summary")
router.register(r"analytics/channels", AnalticsChannelViewSet, basename="analytics-channels")
router.register(r"analytics/tech-demand", AnalyticsTechDemandViewSet, basename="analytics-tech-demand")
router.register(r"analytics/velocity", AnalyticsVelocityViewSet, basename="analytics-velocity")
router.register(r"analytics/ghosting", AnalyticsGhostingViewSet, basename="analytics-ghosting")


urlpatterns = [
    path('daily-count/', DailyCountView.as_view()),
    *router.urls
]
# urlpatterns =
