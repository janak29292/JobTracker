from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import add_raw_jobs


class Command(BaseCommand):
    help = "Scrape Job data from URLs"

    # def add_arguments(self, parser):
    #     print(parser)
    #     parser.add_argument(
    #         '-s',
    #         '--source',
    #         type=str,
    #         choices=['linkedin'],
    #         required=True,
    #         help='Job source to scan',
    #     )
    #     parser.add_argument(
    #         '-t',
    #         '--type',
    #         type=str,
    #         choices=['recommended'],
    #         help='Job type to scan and search',
    #     )

    def handle(self, *args, **options):
        add_raw_jobs()
        self.stdout.write(
            self.style.SUCCESS('Scraper Job added to Worker')
        )
        # if options["source"] == 'linkedin':
        #     if not options["type"]:
        #         raise CommandError("Type is required: (--type or -t)")
        #     if options['type'] == 'recommended':
        #         scan_job_urls()
        #         self.stdout.write(
        #             self.style.SUCCESS('Scan Job Added to Worker')
        #         )
