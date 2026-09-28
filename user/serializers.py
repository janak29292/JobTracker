from rest_framework import serializers

from user.models import (
    Problem, Approach, Pattern, Category, Unstructured,
    ApplicantProfile, WorkExperience, Education, Project,
    Dealbreaker, Question, Answer, BulletPoint
)


class UnstructuredSerializer(serializers.ModelSerializer):
    # We leave 'children' out of the explicit fields here
    # and inject it in to_representation to avoid NameError.

    class Meta:
        model = Unstructured
        fields = ['id', 'info', 'parent']  # 'children' will be added dynamically

    def to_representation(self, instance):
        # 1. Get the standard representation (id, name, etc.)
        representation = super().to_representation(instance)

        # 2. Inject the nested children using 'self' (the same class)
        # many=True and read_only=True as requested
        representation['children'] = UnstructuredSerializer(
            instance.children.all(),
            many=True,
            context=self.context
        ).data

        return representation

class ProblemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Problem
        fields = ['id', 'pattern', 'phrase', 'statement']


class ApproachSerializer(serializers.ModelSerializer):
    class Meta:
        model = Approach
        fields = ['id', 'pattern', 'name', 'description', 'time_complexity',
                  'space_complexity', 'code_example', 'code_result']


class PatternSerializer(serializers.ModelSerializer):
    problems = ProblemSerializer(many=True, read_only=True)
    approaches = ApproachSerializer(many=True, read_only=True)

    class Meta:
        model = Pattern
        fields = ['id', 'category', 'name', 'description', 'use_cases', 'problems', 'approaches']


class CategorySerializer(serializers.ModelSerializer):
    patterns = PatternSerializer(many=True, read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'patterns']

# ---------------------------------------------------------
# Applicant Automation Serializers
# ---------------------------------------------------------

class BulletPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulletPoint
        fields = ['id', 'content']

class WorkExperienceSerializer(serializers.ModelSerializer):
    bullet_points = BulletPointSerializer(many=True, read_only=True)
    
    class Meta:
        model = WorkExperience
        fields = '__all__'

class EducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Education
        fields = '__all__'

class ProjectSerializer(serializers.ModelSerializer):
    bullet_points = BulletPointSerializer(many=True, read_only=True)
    
    class Meta:
        model = Project
        fields = '__all__'

class ApplicantProfileSerializer(serializers.ModelSerializer):
    experiences = WorkExperienceSerializer(many=True, read_only=True)
    education = EducationSerializer(many=True, read_only=True)
    projects = ProjectSerializer(many=True, read_only=True)

    class Meta:
        model = ApplicantProfile
        fields = '__all__'

class AnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Answer
        fields = '__all__'

class QuestionSerializer(serializers.ModelSerializer):
    answers = AnswerSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = '__all__'

class DealbreakerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dealbreaker
        fields = '__all__'
