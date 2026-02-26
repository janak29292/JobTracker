from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from user.models import Category, Pattern, Problem, Approach, Unstructured
from user.serializers import CategorySerializer, PatternSerializer, ProblemSerializer, ApproachSerializer, \
    UnstructuredSerializer


class UnstructuredViewSet(ModelViewSet):
    # queryset = Unstructured.objects.prefetch_related('children').filter(parent=None)
    serializer_class = UnstructuredSerializer
    pagination_class = None

    def get_queryset(self):
        # self.action == 'list' corresponds to the GET / route (fetching the root tree)
        if self.action == 'list':
            return Unstructured.objects.prefetch_related('children').filter(parent=None)

        # For 'retrieve', 'update', 'partial_update', and 'destroy', we need to
        # access ANY note in the table, not just the root notes.
        return Unstructured.objects.prefetch_related('children').all()

# Create your views here.

# ---
#
# ## 💻 Tech Proficiency Tracker Module
#
# ### Data Model: Technology
# ```json
# {
#   "id": 1,
#   "name": "Python",
#   "type": "Technology",
#   "proficiency": 5,
#   "last_revised": "2024-12-01",
#   "notes": "Strong in async programming, FastAPI",
#   "resource_link": "https://link-to-notes.com",
#   "created_at": "2024-01-15"
# }
# ```
#
# **Field Descriptions**:
# - `type`: "Technology" or "Tool"
# - `proficiency`: Integer 1-5 (1=Beginner, 5=Expert)

class TechTrackerViewSet(viewsets.ViewSet):

    def list(self, request):
        """
        ### 1. Get All Technologies
        **Endpoint**: `GET /tech-tracker`

        **Query Parameters**:
        - `type` (optional) - Filter by "Technology" or "Tool"
        - `min_proficiency` (optional) - Minimum proficiency level
        """
        return Response(
            {
                "technologies": [
                    {
                        "id": 1,
                        "name": "Python",
                        "type": "Technology",
                        "proficiency": 5,
                        "last_revised": "2024-12-01",
                        "notes": "Strong in async programming",
                        "resource_link": None
                    },
                    {
                        "id": 2,
                        "name": "FastAPI",
                        "type": "Technology",
                        "proficiency": 4,
                        "last_revised": "2024-11-28",
                        "notes": "Comfortable with routing and middleware",
                        "resource_link": "https://fastapi.tiangolo.com"
                    },
                    {
                        "id": 3,
                        "name": "Docker",
                        "type": "Tool",
                        "proficiency": 3,
                        "last_revised": "2024-11-20",
                        "notes": "Can containerize apps, need more practice with compose",
                        "resource_link": None
                    },
                    {
                        "id": 4,
                        "name": "Kubernetes",
                        "type": "Tool",
                        "proficiency": 2,
                        "last_revised": "2024-11-10",
                        "notes": "Basic understanding, need more practice",
                        "resource_link": "https://my-k8s-notes.com"
                    }
                ]
            }
        )

    def create(self, request):
        """
        ### 2. Add New Technology
        **Endpoint**: `POST /tech-tracker`
        """
        return Response(
            {
                "id": 15,
                "name": "Kubernetes",
                "type": "Tool",
                "proficiency": 2,
                "last_revised": "2024-12-03",
                "notes": "Basic understanding, need more practice",
                "resource_link": "https://my-k8s-notes.com",
                "created_at": "2024-12-03"
            },
            status=201
        )

    def update(self, request, pk=None):
        """
        ### 3. Update Technology
        **Endpoint**: `PUT /tech-tracker/{id}`
        """
        return Response(
            {
                "id": int(pk),
                "name": "Kubernetes",
                "type": "Tool",
                "proficiency": 3,
                "last_revised": "2024-12-03",
                "notes": "Practiced deployment, better understanding now",
                "resource_link": "https://my-k8s-notes.com",
                "created_at": "2024-12-01"
            }
        )

    def destroy(self, request, pk=None):
        """
        ### 4. Delete Technology
        **Endpoint**: `DELETE /tech-tracker/{id}`
        """
        return Response({"message": "Technology deleted successfully"})


    @action(detail=False, methods=["post"], url_path="compare")
    def compare(self, request):
        """
        ### 5. Compare Tech with Job
        **Endpoint**: `POST /tech-tracker/compare`
        """
        return Response(
            {
                "job_id": 123,
                "required_tech": ["Python", "FastAPI", "Docker", "Kubernetes"],
                "comparison": [
                    {
                        "name": "Python",
                        "required": True,
                        "proficiency": 5,
                        "status": "strong"
                    },
                    {
                        "name": "FastAPI",
                        "required": True,
                        "proficiency": 4,
                        "status": "strong"
                    },
                    {
                        "name": "Docker",
                        "required": True,
                        "proficiency": 3,
                        "status": "needs_improvement"
                    },
                    {
                        "name": "Kubernetes",
                        "required": True,
                        "proficiency": 2,
                        "status": "needs_improvement"
                    },
                    {
                        "name": "MongoDB",
                        "required": True,
                        "proficiency": None,
                        "status": "missing"
                    }
                ],
                "match_percentage": 75.0,
                "gaps": ["MongoDB"]
            }
        )


# ---
#
# ## 🎓 Interview Prep Module
#
# ### Data Model: Category
# ```json
# {
#   "id": 1,
#   "name": "Arrays",
#   "patterns": [Pattern]
# }
# ```
#
# ### Data Model: Pattern
# ```json
# {
#   "id": 1,
#   "name": "Two Pointers",
#   "category": 1,
#   "description": "Use two pointers to traverse array/string",
#   "when_to_use": "Sorted array + finding pair/triplet",
#   "problems": ["find pair with target sum", "remove duplicates"],
#   "approaches": [Approach]
# }
# ```
#
# ### Data Model: Approach
# ```json
# {
#   "id": 1,
#   "pattern": 1,
#   "name": "Opposite Ends",
#   "description": "Start from both ends, move inward",
#   "time_complexity": "O(n)",
#   "space_complexity": "O(1)",
#   "code_example": "...",
#   "code_result": "..."
# }
# ```
#
# ### Data Model: Problem
# ```json
# {
#   "id": 1,
#   "pattern": 1,
#   "phrase": "find pair with target sum",
#   "statement": "Given a sorted array, find two numbers that add up to a target value"
# }
# ```

class DSACategoryViewSet(ModelViewSet):
    queryset = Category.objects.prefetch_related(
        'patterns__problems',
        'patterns__approaches'
    ).all()
    serializer_class = CategorySerializer
    pagination_class = None


class DSAPatternViewSet(ModelViewSet):
    queryset = Pattern.objects.prefetch_related('problems', 'approaches').all()
    serializer_class = PatternSerializer


class DSAProblemViewSet(ModelViewSet):
    queryset = Problem.objects.all()
    serializer_class = ProblemSerializer


class DSAApproachViewSet(ModelViewSet):
    queryset = Approach.objects.all()
    serializer_class = ApproachSerializer

class CodeExecutorViewSet(viewsets.ViewSet):
    """
    Register at: user/execute-code/
    Executes Python code and returns stdout/stderr
    """

    def create(self, request):
        """
        ### POST /execute-code/ — Run Python code
        """
        import subprocess
        code = request.data.get("code", "")
        try:
            result = subprocess.run(
                ["python", "-c", code],
                capture_output=True, text=True, timeout=5
            )
            return Response({
                "output": result.stdout,
                "error": result.stderr
            })
        except subprocess.TimeoutExpired:
            return Response(
                {"output": "", "error": "Execution timed out (5s limit)"},
                status=408
            )


class InterviewChecklistViewSet(viewsets.ViewSet):
    """
    Register at: user/interview-prep/generate-checklist/
    """

    def create(self, request):
        """
        ### 7. Generate Prep Checklist
        **Endpoint**: `POST /interview-prep/generate-checklist`
        """
        return Response(
            {
                "job_id": 123,
                "interview_date": "2024-12-10",
                "days_until_interview": 7,
                "tech_to_revise": [
                    {
                        "name": "Docker",
                        "proficiency": 2,
                        "priority": "high",
                        "estimated_hours": 4
                    },
                    {
                        "name": "Kubernetes",
                        "proficiency": 2,
                        "priority": "high",
                        "estimated_hours": 6
                    }
                ],
                "patterns_to_revise": [
                    {
                        "name": "Two Pointers",
                        "last_revised": "2024-11-15",
                        "days_since_revision": 18,
                        "priority": "high"
                    },
                    {
                        "name": "Sliding Window",
                        "last_revised": "2024-11-20",
                        "days_since_revision": 13,
                        "priority": "medium"
                    }
                ],
                "suggested_problems": [
                    {
                        "id": 3,
                        "name": "3Sum",
                        "pattern": "Two Pointers",
                        "difficulty": "Medium"
                    },
                    {
                        "id": 5,
                        "name": "Minimum Window Substring",
                        "pattern": "Sliding Window",
                        "difficulty": "Hard"
                    }
                ],
                "recommended_schedule": {
                    "day_1": ["Revise Docker basics", "Practice 2 Two Pointer problems"],
                    "day_2": ["Revise Kubernetes", "Practice 2 Sliding Window problems"],
                    "day_3": ["Mock interview session", "Review weak areas"],
                    "day_4": ["Practice hard problems", "Revise system design"],
                    "day_5": ["Full mock interview", "Review answer bank"],
                    "day_6": ["Light revision", "Rest and prepare"],
                    "day_7": ["Interview day - good luck!"]
                }
            }
        )


# ---
#
# ## 💬 Answer Bank Module
#
# ### Data Model: Answer
# ```json
# {
#   "id": 1,
#   "category": "Career Gap",
#   "question": "Why did you leave your previous job?",
#   "answer": "I took a strategic break to upskill in modern technologies...",
#   "confidence": 4,
#   "practice_count": 12,
#   "last_practiced": "2024-12-01",
#   "notes": "Remember to mention specific projects",
#   "tags": ["gap", "career break"],
#   "created_at": "2024-06-15"
# }
# ```
#
# **Categories**:
# - "Career Gap"
# - "Behavioral"
# - "Technical"
# - "Salary Negotiation"
# - "Custom"

class AnswerBankViewSet(viewsets.ViewSet):

    def list(self, request):
        """
        ### 1. Get All Answers
        **Endpoint**: `GET /answer-bank`

        **Query Parameters**:
        - `category` (optional) - Filter by category
        - `search` (optional) - Search in questions/answers
        - `min_confidence` (optional) - Minimum confidence level
        """
        return Response(
            {
                "answers": [
                    {
                        "id": 1,
                        "category": "Career Gap",
                        "question": "Why did you leave your previous job?",
                        "answer": "I took a strategic break to upskill in modern technologies...",
                        "confidence": 4,
                        "practice_count": 12,
                        "last_practiced": "2024-12-01",
                        "tags": ["gap"]
                    },
                    {
                        "id": 2,
                        "category": "Behavioral",
                        "question": "Tell me about a time you handled a conflict at work.",
                        "answer": "In my previous role, I encountered a disagreement with a team member...",
                        "confidence": 3,
                        "practice_count": 5,
                        "last_practiced": "2024-11-28",
                        "tags": ["behavioral", "conflict"]
                    },
                    {
                        "id": 3,
                        "category": "Salary Negotiation",
                        "question": "What are your salary expectations?",
                        "answer": "Based on my research and experience, I'm targeting a range of...",
                        "confidence": 3,
                        "practice_count": 3,
                        "last_practiced": "2024-11-20",
                        "tags": ["salary", "negotiation"]
                    }
                ]
            }
        )

    def retrieve(self, request, pk=None):
        """
        ### 2. Get Answer by ID
        **Endpoint**: `GET /answer-bank/{id}`
        """
        return Response(
            {
                "id": int(pk),
                "category": "Career Gap",
                "question": "Why did you leave your previous job?",
                "answer": "I took a strategic break to upskill in modern technologies...",
                "confidence": 4,
                "practice_count": 12,
                "last_practiced": "2024-12-01",
                "notes": "Remember to mention specific projects",
                "tags": ["gap", "career break"],
                "created_at": "2024-06-15"
            }
        )

    def create(self, request):
        """
        ### 3. Add New Answer
        **Endpoint**: `POST /answer-bank`
        """
        return Response(
            {
                "id": 4,
                "category": "Career Gap",
                "question": "What did you do during your career gap?",
                "answer": "During my 2.5 year break, I focused on:\n1. Deep-diving into Python and FastAPI\n2. Building personal projects including this job tracker\n3. Contributing to open source\n4. Handling personal commitments",
                "confidence": 3,
                "practice_count": 0,
                "last_practiced": None,
                "notes": "Practice speaking this more confidently",
                "tags": ["gap", "upskilling"],
                "created_at": "2024-12-03"
            },
            status=201
        )

    def update(self, request, pk=None):
        """
        ### 4. Update Answer
        **Endpoint**: `PUT /answer-bank/{id}`
        """
        return Response(
            {
                "id": int(pk),
                "category": "Career Gap",
                "question": "Why did you leave your previous job?",
                "answer": "Updated answer text...",
                "confidence": 4,
                "practice_count": 12,
                "last_practiced": "2024-12-01",
                "notes": "Feeling more confident now",
                "tags": ["gap", "career break"],
                "created_at": "2024-06-15"
            }
        )

    @action(detail=False, methods=["patch"], url_path="practice/(?P<pk>[^/.]+)")
    def practice(self, request, pk=None):
        """
        ### 5. Track Practice
        **Endpoint**: `PATCH /answer-bank/practice/{id}`

        **Description**: Increments practice count and updates last practiced date.
        """
        return Response(
            {
                "id": int(pk),
                "practice_count": 13,
                "last_practiced": "2024-12-03"
            }
        )

    def destroy(self, request, pk=None):
        """
        ### 6. Delete Answer
        **Endpoint**: `DELETE /answer-bank/{id}`
        """
        return Response({"message": "Answer deleted successfully"})

    @action(detail=False, methods=["get"], url_path="for-job/(?P<job_id>[^/.]+)")
    def for_job(self, request, job_id=None):
        """
        ### 7. Get Answers for Job
        **Endpoint**: `GET /answer-bank/for-job/{job_id}`

        **Description**: Suggests relevant answers based on job type/company.
        """
        return Response(
            {
                "job_id": int(job_id),
                "suggested_answers": [
                    {
                        "id": 1,
                        "question": "Why did you leave your previous job?",
                        "relevance": "high",
                        "reason": "Career gap question likely for this role"
                    },
                    {
                        "id": 2,
                        "question": "Tell me about a time you handled a conflict at work.",
                        "relevance": "medium",
                        "reason": "Common behavioral question for senior roles"
                    },
                    {
                        "id": 3,
                        "question": "What are your salary expectations?",
                        "relevance": "high",
                        "reason": "Salary negotiation relevant for all applications"
                    }
                ]
            }
        )