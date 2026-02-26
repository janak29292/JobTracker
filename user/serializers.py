from rest_framework import serializers

from user.models import Problem, Approach, Pattern, Category, Unstructured


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
