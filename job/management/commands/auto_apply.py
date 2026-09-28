"""
Management command for the auto-apply system.

Usage:
    ./manage.py auto_apply --sync --batch-size 3
    ./manage.py auto_apply --resume 42
    ./manage.py auto_apply --status
"""
from django.core.management.base import BaseCommand

from django.db.models import Count
from job.models import ApplicationRun
from job.tasks import auto_apply_batch, resume_application
from scripts.auto_apply import run_batch, resume_run


class Command(BaseCommand):
    help = "Run the auto-apply agent to submit applications for matched jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            '--sync', action='store_true', default=False,
            help='Run synchronously (not via Celery)'
        )
        parser.add_argument(
            '--batch-size', type=int, default=None,
            help='Max number of jobs to process'
        )
        parser.add_argument(
            '--resume', type=int, default=None, metavar='RUN_ID',
            help='Resume a specific parked application run by ID'
        )
        parser.add_argument(
            '--status', action='store_true', default=False,
            help='Show status of all application runs'
        )

    def handle(self, *args, **options):
        if options['status']:
            self._show_status()
            return

        if options['resume']:
            run_id = options['resume']
            run = ApplicationRun.objects.filter(id=run_id).first()
            if not run:
                self.stderr.write(f"Run #{run_id} not found.")
                return
            if run.status != 'PENDING_REVIEW':
                self.stderr.write(
                    f"Run #{run_id} status is '{run.status}', not 'PENDING_REVIEW'. "
                    f"Set status to 'REVIEW_APPROVED' first."
                )
                return

            run.status = 'REVIEW_APPROVED'
            run.save()

            if options['sync']:
                resume_run(run_id)
            else:
                resume_application.delay(run_id)
                self.stdout.write(self.style.SUCCESS(f"Queued resume for Run #{run_id}"))
            return

        # Normal batch run
        if options['sync']:
            run_batch(batch_size=options['batch_size'])
        else:
            auto_apply_batch.delay(batch_size=options['batch_size'])
            self.stdout.write(self.style.SUCCESS("Auto-apply batch queued."))

    def _show_status(self):
        """Print a summary of all application runs."""
        summary = (
            ApplicationRun.objects
            .values('status')
            .annotate(count=Count('id'))
            .order_by('status')
        )

        self.stdout.write("\n  Application Run Status Summary:")
        self.stdout.write("  " + "-" * 40)
        total = 0
        for row in summary:
            self.stdout.write(f"  {row['status']:<20} {row['count']}")
            total += row['count']
        self.stdout.write("  " + "-" * 40)
        self.stdout.write(f"  {'TOTAL':<20} {total}\n")

        # Show pending review details
        pending = ApplicationRun.objects.filter(status='PENDING_REVIEW').select_related('job')
        if pending.exists():
            self.stdout.write("  Pending Review:")
            for run in pending:
                self.stdout.write(
                    f"    Run #{run.id}: {run.job.position} at {run.job.company} "
                    f"(type: {run.apply_type})"
                )
            self.stdout.write("")
