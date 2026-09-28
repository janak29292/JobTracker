from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    # Tech Tracker
    TechTrackerViewSet,
    # DSA
    DSACategoryViewSet,
    DSAPatternViewSet,
    DSAProblemViewSet,
    DSAApproachViewSet,
    CodeExecutorViewSet,
    # Interview Prep
    InterviewChecklistViewSet,
    # Answer Bank
    AnswerBankViewSet,
    UnstructuredViewSet,
    # Applicant Automation
    ApplicantProfileViewSet,
    WorkExperienceViewSet,
    EducationViewSet,
    ProjectViewSet,
    DealbreakerViewSet,
    QuestionViewSet,
    ProfileAnswerViewSet,
    BulletPointViewSet,
)

router = DefaultRouter()

router.register(r"unstructured", UnstructuredViewSet, basename="unstructured")

# 💻 Tech Tracker
router.register(r"tech-tracker", TechTrackerViewSet, basename="tech-tracker")

# 🧠 DSA
router.register(r"dsa", DSACategoryViewSet, basename="dsa-categories")
router.register(r"dsa-pattern", DSAPatternViewSet, basename="dsa-patterns")
router.register(r"dsa-problems", DSAProblemViewSet, basename="dsa-problems")
router.register(r"dsa-approaches", DSAApproachViewSet, basename="dsa-approaches")

# ▶️ Code Executor
router.register(r"execute-code", CodeExecutorViewSet, basename="execute-code")

# 🎓 Interview Prep
router.register(r"interview-prep/generate-checklist", InterviewChecklistViewSet, basename="interview-checklist")

# 💬 Answer Bank
router.register(r"answer-bank", AnswerBankViewSet, basename="answer-bank")

# 🎓 Applicant Automation
router.register(r"profile", ApplicantProfileViewSet, basename="profile")
router.register(r"experiences", WorkExperienceViewSet, basename="work-experiences")
router.register(r"educations", EducationViewSet, basename="educations")
router.register(r"projects", ProjectViewSet, basename="projects")
router.register(r"dealbreakers", DealbreakerViewSet, basename="dealbreakers")
router.register(r"questions", QuestionViewSet, basename="questions")
router.register(r"answers", ProfileAnswerViewSet, basename="answers")
router.register(r"bullets", BulletPointViewSet, basename="bullets")

urlpatterns = [
    # path("", include(router.urls)),
    *router.urls,
]