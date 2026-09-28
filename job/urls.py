from django.urls import path
from rest_framework.routers import DefaultRouter

from job.views import URLViewSet, RawJobViewSet, JobViewSet, TechStackViewSet, PostingViewSet, UnknownTechViewSet, JobMatchViewSet, ApplicationRunViewSet

router = DefaultRouter()
router.register(r'urls', URLViewSet, basename='url')
router.register(r'raw', RawJobViewSet, basename='raw')
router.register(r'jobs', JobViewSet, basename='jobs')
router.register(r'techs', TechStackViewSet, basename='techs')
router.register(r'postings', PostingViewSet, basename='postings')
router.register(r'unknown-techs', UnknownTechViewSet, basename='unknown-techs')
router.register(r'job-matches', JobMatchViewSet, basename='job-matches')
router.register(r'application-runs', ApplicationRunViewSet, basename='application-runs')

urlpatterns = [
    *router.urls
]
