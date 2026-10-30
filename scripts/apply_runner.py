"""
Auto-apply entry point (v2).

Called by Celery tasks or run directly from the command line to process
queued applications or resume parked/interrupted applications.

Sets up the ApplyContext (Playwright page, OllamaClient, profile, question
catalog, applicant data bundle) before invoking the LangGraph agent.
"""
import argparse
import logging
import os
import re
import sys

from django.conf import settings
from django.utils import timezone

from job.models import Job, ApplicationRun, JobMatch, StatusEvent
from user.models import ApplicantProfile, Question
from services.ollama_client import OllamaClient
from services.parser import Parser
from services.apply_agent.graph import get_compiled_graph
from services.apply_agent.state import ApplyState
from services.apply_agent.context import ApplyContext, set_context, clear_context
from services.apply_agent.field_mapper import get_applicant_data_bundle

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

logger = logging.getLogger(__name__)


def normalize_apply_url(url: str) -> str:
    """
    Deterministic normalization of apply URLs.
    
    Extracts the numeric job ID from any LinkedIn search, collection, or view URL
    and converts it directly to the canonical: https://www.linkedin.com/jobs/view/{id}/
    """
    if not url:
        return ''
    
    url_lower = url.lower()
    if 'linkedin.com' in url_lower:
        match = re.search(r'(?:/jobs/view/|(?:currentJobId|jobId)=)(\d+)', url)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"

    return url


def run_batch(batch_size: int = None, include_pending: bool = False, job_id: int = None):
    """
    Process queued applications.
    
    Picks up jobs with status='MA' (and optionally 'PR'), creates ApplicationRun
    entries, and runs them through the agent graph.
    If job_id is provided, runs specifically for that job.
    """
    if job_id:
        jobs = Job.objects.filter(id=job_id)
        if not jobs.exists():
            print(f"ERROR: Job #{job_id} not found in database.")
            return
        filter_statuses = [jobs.first().status]
    else:
        filter_statuses = ['MA', 'PR'] if include_pending else ['MA']
        jobs = Job.objects.filter(status__in=filter_statuses).order_by('-last_posted')
        if batch_size:
            jobs = jobs[:batch_size]

    if not jobs:
        print(f"No jobs found with status in {filter_statuses}.")
        return

    print(f"\n========================================================")
    print(f" Starting Auto-Apply Pipeline (LLM-First v2)")
    print(f" Target Jobs: {len(jobs)} (Statuses: {filter_statuses})")
    print(f"========================================================\n")

    # Initialize shared resources
    parser = Parser()
    client = OllamaClient.get_reasoning_client()
    profile = ApplicantProfile.objects.first()

    if not profile:
        print("ERROR: No ApplicantProfile found in database. Create one first.")
        return

    # Preload lightweight question catalog (IDs + prompts only)
    question_catalog = [
        {'id': q['id'], 'text': q['pattern']}
        for q in Question.objects.values('id', 'pattern')
    ]
    logger.info(f"Preloaded {len(question_catalog)} questions into lightweight catalog.")

    # Preload structured applicant data bundle (Profile + Experience + Education + Projects)
    applicant_bundle = get_applicant_data_bundle(profile)
    logger.info("Preloaded full applicant data bundle.")

    graph = get_compiled_graph()

    try:
        page = parser.page

        ctx = ApplyContext(
            page=page,
            client=client,
            profile=profile,
            method='instructor',
            question_catalog=question_catalog,
            applicant_bundle=applicant_bundle,
        )
        if hasattr(parser, 'context') and parser.context:
            ctx.attach_tab_listener(parser.context)
        elif page and page.context:
            ctx.attach_tab_listener(page.context)

        set_context(ctx)

        for i, job in enumerate(jobs, 1):
            normalized_url = normalize_apply_url(job.apply_url)
            print(f"\n[{i}/{len(jobs)}] Processing: {job.position} at {job.company}")
            print(f"  URL: {normalized_url}")

            # Create or get active application run
            run = ApplicationRun.objects.create(
                job=job,
                status='QUEUED',
            )

            # Build initial state for StateGraph
            initial_state: ApplyState = {
                'job_id': job.id,
                'apply_url': normalized_url,
                'run_id': run.id,
                'current_url': '',
                'step_number': 0,
                'screenshot_path': None,
                'page_text': None,
                'dom_snapshot': None,
                'dom_signature': '',
                'page_type': None,
                'page_analysis': None,
                'alternative_apply_method': None,
                'alternative_apply_details': None,
                'captcha_attempts': 0,
                'checklist': [],
                'checklist_index': 0,
                'last_action_success': None,
                'last_action_error': None,
                'page_changed_after_action': None,
                'consecutive_action_failures': 0,
                'total_actions_executed': 0,
                'recent_actions': [],
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

                print(f"  -> Outcome: {run.status}")
                if run.skip_reason:
                    print(f"  -> Skip Reason: {run.skip_reason}")
                if run.escalation_reason:
                    print(f"  -> Escalation Details: {run.escalation_reason}")

            except KeyboardInterrupt:
                run.refresh_from_db()
                if run.status in ('IN_PROGRESS', 'QUEUED'):
                    run.status = 'INTERRUPTED'
                    run.error_log = 'Execution paused by user (KeyboardInterrupt)'
                    run.checkpoint_id = f"apply-{run.id}"
                    run.save()
                print(f"\n  -> PAUSED by user. State safely saved in checkpoint 'apply-{run.id}'.")
                print(f"  -> Resume with: python scripts/apply_runner.py --resume {run.id}\n")
                raise

            except Exception as e:
                logger.exception(f"Error applying to Job #{job.id}")
                run.refresh_from_db()
                if run.status == 'IN_PROGRESS':
                    run.status = 'FAILED'
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
    Resume a parked (PENDING_REVIEW) or INTERRUPTED application.
    
    Re-establishes the browser view, loads the checkpoint, and continues execution.
    """
    try:
        run = ApplicationRun.objects.get(id=run_id)
    except ApplicationRun.DoesNotExist:
        print(f"ERROR: ApplicationRun #{run_id} does not exist.")
        return

    if run.status not in ('PENDING_REVIEW', 'INTERRUPTED', 'REVIEW_APPROVED'):
        print(f"Run #{run_id} status is '{run.status}'. Only PENDING_REVIEW or INTERRUPTED runs can be resumed.")
        return

    print(f"\n========================================================")
    print(f" Resuming Run #{run_id} for {run.job.company} — {run.job.position}")
    print(f" Previous Status: {run.status}")
    print(f" Checkpoint Thread: apply-{run_id}")
    print(f"========================================================\n")

    parser = Parser()
    client = OllamaClient.get_reasoning_client()
    profile = ApplicantProfile.objects.first()

    if not profile:
        print("ERROR: No ApplicantProfile found.")
        return

    question_catalog = [
        {'id': q['id'], 'text': q['pattern']}
        for q in Question.objects.values('id', 'pattern')
    ]
    applicant_bundle = get_applicant_data_bundle(profile)

    graph = get_compiled_graph()

    try:
        page = parser.page
        normalized_url = normalize_apply_url(run.job.apply_url)

        # Re-establish browser view before invoking the graph
        print(f"Navigating browser to job application URL: {normalized_url}")
        page.goto(normalized_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        ctx = ApplyContext(
            page=page,
            client=client,
            profile=profile,
            method='instructor',
            question_catalog=question_catalog,
            applicant_bundle=applicant_bundle,
        )
        if hasattr(parser, 'context') and parser.context:
            ctx.attach_tab_listener(parser.context)
        elif page and page.context:
            ctx.attach_tab_listener(page.context)

        set_context(ctx)

        thread_id = run.checkpoint_id or f"apply-{run.id}"
        config = {"configurable": {"thread_id": thread_id}}

        # Resume state graph execution from SQLite checkpoint
        run.status = 'IN_PROGRESS'
        run.save()

        final_state = graph.invoke(None, config=config)

        run.refresh_from_db()
        print(f"\n  -> Resume Completed. Final Status: {run.status}")
        if run.escalation_reason:
            print(f"  -> Status Details: {run.escalation_reason}")

    except KeyboardInterrupt:
        run.refresh_from_db()
        run.status = 'INTERRUPTED'
        run.error_log = 'Paused again by user (KeyboardInterrupt)'
        run.save()
        print(f"\n  -> PAUSED again. Checkpoint preserved in '{thread_id}'.")

    except Exception as e:
        logger.exception(f"Error resuming Run #{run_id}")
        run.refresh_from_db()
        run.status = 'FAILED'
        run.error_log = str(e)
        run.save()
        print(f"  -> ERROR during resume: {e}")

    finally:
        clear_context()
        try:
            parser.playwright.stop()
        except Exception:
            pass


def reset_application(job_id: int = None, run_id: int = None, reset_all_failed: bool = False):
    """
    Reset job and application run states so that applications can be cleanly re-attempted.
    """
    if reset_all_failed:
        failed_runs = ApplicationRun.objects.filter(
            status__in=['FAILED', 'INTERRUPTED', 'ESCALATED', 'SUBMIT_FAILED', 'PENDING_REVIEW']
        )
        job_ids = list(failed_runs.values_list('job_id', flat=True).distinct())
        jobs_reset = Job.objects.filter(id__in=job_ids).exclude(status='AF').update(status='MA', applied_on=None)
        runs_count = failed_runs.count()
        failed_runs.delete()

        print(f"\n========================================================")
        print(f"  RESET COMPLETE")
        print(f"  Deleted {runs_count} failed/interrupted run(s).")
        print(f"  Restored {jobs_reset} job(s) back to status 'MA' (Matched).")
        print(f"========================================================")
        print("  Run next application with: python scripts/apply_runner.py --batch-size 1\n")
        return

    if job_id:
        job = Job.objects.filter(id=job_id).first()
        if not job:
            print(f"\n❌ Error: Job #{job_id} not found.")
            return
        StatusEvent.objects.filter(job=job, status='AF').delete()
        Job.objects.filter(id=job.id).update(status='MA', applied_on=None)
        deleted_runs, _ = ApplicationRun.objects.filter(job=job).delete()
        print(f"\n========================================================")
        print(f"  JOB RESET COMPLETE")
        print(f"  Job #{job.id}: {job.position} at {job.company}")
        print(f"  Status restored to 'MA'. Cleared {deleted_runs} past run(s) and any false AF events.")
        print(f"========================================================")
        print(f"  Test this job directly with: python scripts/apply_runner.py --job-id {job.id}\n")
        return

    if run_id:
        run = ApplicationRun.objects.filter(id=run_id).first()
        if not run:
            print(f"\n❌ Error: Run #{run_id} not found.")
            return
        job = run.job
        StatusEvent.objects.filter(job=job, status='AF').delete()
        Job.objects.filter(id=job.id).update(status='MA', applied_on=None)
        run.delete()
        print(f"\n========================================================")
        print(f"  RUN RESET COMPLETE")
        print(f"  Run #{run_id} deleted. Job #{job.id} restored to 'MA'.")
        print(f"========================================================")
        print(f"  Test this job directly with: python scripts/apply_runner.py --job-id {job.id}\n")
        return

    print("No reset target specified. Options: --reset-job <ID>, --reset-run <ID>, --reset-all-failed")


def print_application_status():
    """Display comprehensive diagnostics of application runs, errors, and current job queue."""
    from django.db.models import Count
    from job.models import Job, ApplicationRun, StepRecord

    print("\n" + "=" * 75)
    print("  AUTO-APPLY AGENT STATUS DASHBOARD")
    print("=" * 75)

    # 1. Summary of Application Runs
    runs_summary = (
        ApplicationRun.objects
        .values('status')
        .annotate(count=Count('id'))
        .order_by('status')
    )
    total_runs = sum(r['count'] for r in runs_summary)

    print("\n[Application Run Counts]")
    if total_runs == 0:
        print("  No application runs recorded yet.")
    else:
        for r in runs_summary:
            st = r['status']
            count = r['count']
            icon = "✅" if st == 'SUBMITTED' else ("⏸️" if st in ('PENDING_REVIEW', 'INTERRUPTED') else ("❌" if st in ('FAILED', 'SUBMIT_FAILED', 'ESCALATED') else "⏳"))
            print(f"  {icon} {st:<22} : {count}")
        print(f"  {'TOTAL RUNS':<24} : {total_runs}")

    # 2. Detailed Recent Runs (Last 10)
    recent_runs = ApplicationRun.objects.select_related('job').order_by('-id')[:10]
    if recent_runs.exists():
        print("\n[Recent Application Runs (Last 10)]")
        print("-" * 75)
        for run in recent_runs:
            job = run.job
            created = run.created_at.strftime('%Y-%m-%d %H:%M:%S') if run.created_at else ''
            step_count = StepRecord.objects.filter(run=run).count()
            print(f"  Run #{run.id} | Job #{job.id} | {job.company} — {job.position}")
            print(f"    Status: {run.status} | Job Status: {job.status} | Steps Taken: {step_count} | Started: {created}")
            if run.error_log:
                err_lines = [l.strip() for l in run.error_log.strip().split('\n') if l.strip()]
                last_err = err_lines[-1] if err_lines else ''
                print(f"    Error Details: {last_err[:120]}")
            if run.escalation_reason:
                print(f"    Escalation Reason: {run.escalation_reason[:120]}")
            if run.skip_reason:
                print(f"    Skip Reason: {run.skip_reason}")
            print(f"    Target URL: {job.apply_url}")
            print("-" * 75)

    # 3. Matched Jobs Queue (status='MA')
    matched_jobs = Job.objects.filter(status='MA').order_by('-last_posted')
    matched_count = matched_jobs.count()
    print(f"\n[Matched Jobs Queue (status='MA') -> Ready to Apply: {matched_count}]")
    if matched_count == 0:
        print("  No jobs currently in 'MA' status. Use --reset-all-failed or qualify more jobs.")
    else:
        for j in matched_jobs[:5]:
            print(f"  • Job #{j.id}: {j.position} at {j.company} ({j.platform})")
            print(f"    URL: {j.apply_url}")
        if matched_count > 5:
            print(f"    ... and {matched_count - 5} more queued.")

    # 4. Jobs Pending Review (status='PR')
    pr_jobs = Job.objects.filter(status='PR').order_by('-id')
    pr_count = pr_jobs.count()
    if pr_count > 0:
        print(f"\n[Jobs Parked in Pending Review (status='PR') -> Total: {pr_count}]")
        for j in pr_jobs[:5]:
            last_run = j.application_runs.order_by('-id').first()
            esc = last_run.escalation_reason if last_run else ''
            print(f"  • Job #{j.id}: {j.position} at {j.company}")
            if esc:
                print(f"    Reason: {esc[:120]}")

    # 5. Quick Commands
    print("\n" + "=" * 75)
    print(" 🛠️ QUICK ACTION COMMANDS:")
    print("  • Apply to next matched job:   python scripts/apply_runner.py --batch-size 1")
    print("  • Apply to specific Job ID:   python scripts/apply_runner.py --job-id <JOB_ID>")
    print("  • Reset all failed to 'MA':   python scripts/apply_runner.py --reset-all-failed")
    print("  • Reset specific Job to 'MA': python scripts/apply_runner.py --reset-job <JOB_ID>")
    print("  • Resume an interrupted run:  python scripts/apply_runner.py --resume <RUN_ID>")
    print("=" * 75 + "\n")
