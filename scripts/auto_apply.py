"""
Auto-apply entry point.

Called by Celery tasks to process queued applications or resume
parked applications after human review.

Sets up the ApplyContext (Playwright page, OllamaClient, profile)
before invoking the LangGraph agent, and cleans up after.
"""
import logging

from django.utils import timezone

from job.models import Job, ApplicationRun, JobMatch
from user.models import ApplicantProfile
from services.ollama_client import OllamaClient
from services.parser import Parser
from services.apply_agent.graph import get_compiled_graph
from services.apply_agent.state import ApplyState
from services.apply_agent.context import ApplyContext, set_context, clear_context

logger = logging.getLogger(__name__)


def run_batch(batch_size=None):
    """
    Process queued applications.
    
    Picks up jobs with decision='APPLY' (from JobMatch) that haven't been
    applied to yet, creates ApplicationRun entries, and runs them through
    the agent graph.
    """
    # Find matched jobs that need applying
    applied_job_ids = set(
        ApplicationRun.objects.filter(
            status__in=['SUBMITTED', 'IN_PROGRESS', 'PENDING_REVIEW', 'REVIEW_APPROVED']
        ).values_list('job_id', flat=True)
    )

    # matched_jobs = JobMatch.objects.filter(
    #     decision='APPLY'
    # ).exclude(
    #     job_id__in=applied_job_ids
    # ).select_related('job')
    #
    # if batch_size:
    #     matched_jobs = matched_jobs[:batch_size]
    #
    # jobs = [m.job for m in matched_jobs]

    jobs = Job.objects.filter(
        status='MA'
    )

    if batch_size:
        jobs = jobs[:batch_size]

    if not jobs:
        print("No jobs to apply to.")
        return

    print(f"Starting Auto-Apply for {len(jobs)} jobs...")

    # Initialize shared resources
    parser = Parser()
    client = OllamaClient()
    profile = ApplicantProfile.objects.first()

    if not profile:
        print("ERROR: No ApplicantProfile found. Create one first.")
        return

    graph = get_compiled_graph()

    try:
        # Parser.__init__ launches browser, loads cookies, creates page
        page = parser.page

        # Set the thread-local context for nodes to access
        ctx = ApplyContext(
            page=page,
            client=client,
            profile=profile,
            method='instructor',
        )
        set_context(ctx)

        for i, job in enumerate(jobs, 1):
            print(f"\n[{i}/{len(jobs)}] Applying to: {job.position} at {job.company}")
            print(f"  URL: {job.apply_url}")

            # Create the application run
            run = ApplicationRun.objects.create(
                job=job,
                status='QUEUED',
            )

            # Build initial state
            initial_state: ApplyState = {
                'job_id': job.id,
                'apply_url': job.apply_url,
                'run_id': run.id,
                'current_url': '',
                'step_number': 0,
                'screenshot_path': None,
                'dom_snapshot': None,
                'dom_signature': '',
                'field_labels': [],
                'apply_type': None,
                'had_fresh_decisions': False,
                'status': 'QUEUED',
                'escalation_reason': None,
                'error_log': None,
            }

            try:
                config = {"configurable": {"thread_id": f"apply-{run.id}"}}
                final_state = graph.invoke(initial_state, config=config)

                run.refresh_from_db()
                run.checkpoint_id = f"apply-{run.id}"
                run.save()

                print(f"  -> Status: {run.status}")
                if run.skip_reason:
                    print(f"  -> Skip reason: {run.skip_reason}")
                if run.escalation_reason:
                    print(f"  -> Escalation: {run.escalation_reason}")

            except Exception as e:
                logger.exception(f"Error applying to Job #{job.id}")
                run.status = 'SUBMIT_FAILED'
                run.error_log = str(e)
                run.save()
                print(f"  -> ERROR: {e}")

    finally:
        clear_context()
        try:
            parser.playwright.stop()
        except Exception:
            pass

    print(f"\nDone. Processed {len(jobs)} applications.")


def resume_run(run_id: int):
    """
    Resume a parked application after human review/approval.
    
    Loads the LangGraph checkpoint and continues from where it left off.
    """
    run = ApplicationRun.objects.get(id=run_id)

    if run.status != 'REVIEW_APPROVED':
        logger.warning(f"Run #{run_id} status is {run.status}, not REVIEW_APPROVED. Skipping.")
        return

    print(f"Resuming Run #{run_id} for {run.job.company}...")

    parser = Parser()
    client = OllamaClient()
    profile = ApplicantProfile.objects.first()

    graph = get_compiled_graph()

    try:
        page = parser.page

        ctx = ApplyContext(
            page=page,
            client=client,
            profile=profile,
            method='instructor',
        )
        set_context(ctx)

        config = {"configurable": {"thread_id": run.checkpoint_id}}

        # Resume from checkpoint
        final_state = graph.invoke(None, config=config)

        run.refresh_from_db()
        print(f"  -> Status: {run.status}")

    except Exception as e:
        logger.exception(f"Error resuming Run #{run_id}")
        run.status = 'SUBMIT_FAILED'
        run.error_log = str(e)
        run.save()
        print(f"  -> ERROR: {e}")

    finally:
        clear_context()
        try:
            parser.playwright.stop()
        except Exception:
            pass
