from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import parse_raw_jobs


class Command(BaseCommand):
    help = "Parse Raw Job data for use"

    def handle(self, *args, **options):
        parse_raw_jobs()
        self.stdout.write(
            self.style.SUCCESS('Parser Job added to Worker')
        )
