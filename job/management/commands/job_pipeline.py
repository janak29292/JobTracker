from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import linkedin_filtered_job_pipeline_caller, linkedin_recommended_job_pipeline_caller


class Command(BaseCommand):
    help = "Scan and Store URLs for Jobs"

    def add_arguments(self, parser):
        self.parser = parser
        parser.add_argument(
            '-s',
            '--source',
            type=str,
            choices=['linkedin'],
            required=True,
            help='Job source to scan',
        )
        parser.add_argument(
            '-t',
            '--type',
            type=str,
            choices=['recommended', 'filtered'],
            help='Job type to scan and search',
        )

    def handle(self, *args, **options):
        if options["source"] == 'linkedin':
            if not options["type"]:
                # raise CommandError("Type is required: (--type or -t)")
                self.parser.error("the following arguments are required: -t/--type")
            if options['type'] == 'recommended':
                linkedin_recommended_job_pipeline_caller()
                self.stdout.write(
                    self.style.SUCCESS('LinkedIn Recommended Pipeline Started')
                )
            if options['type'] == 'filtered':
                linkedin_filtered_job_pipeline_caller()
                self.stdout.write(
                    self.style.SUCCESS('LinkedIn Filtered Pipeline Started')
                )