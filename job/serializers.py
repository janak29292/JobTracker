from rest_framework import serializers
from django.utils import timezone

from job.models import JobUrl, JobRaw, Job, TechStack, Posting


class UrlSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobUrl
        # fields = ['url']
        fields = '__all__'
        # extra_kwargs = {'url': {'write_only': True}}
        # extra_kwargs = {'scanned': {'read_only': True}}


class RawJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobRaw
        fields = '__all__'


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = '__all__'
        extra_kwargs = {
            'company': {'read_only': True},
            'position': {'read_only': True},
            'job_description': {'read_only': True},
            'job_location': {'read_only': True},
            'apply_url': {'read_only': True},
            'tech_stack_primary': {'read_only': True},
            'tech_stack_all': {'read_only': True},
        }

    def update(self, instance, validated_data):

        # Check if status is being updated to 'AF' (Applied)
        new_status = validated_data.get('status')
        if new_status == 'AF' and instance.status != 'AF':
            validated_data['applied_on'] = timezone.localtime(timezone.now()).date()

        # Set first_response_date once, on first meaningful response
        response_statuses = {'CR', 'IS', 'RP', 'NE', 'RE', 'OR'}
        if new_status in response_statuses and not instance.first_response_date:
            validated_data['first_response_date'] = timezone.localtime(timezone.now()).date()
        return super().update(instance, validated_data)


class TechStackSerializer(serializers.ModelSerializer):
    class Meta:
        model = TechStack
        fields = '__all__'


class PostingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Posting
        fields = '__all__'
