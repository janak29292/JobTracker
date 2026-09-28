from job.models import StatusEvent
import json
from pathlib import Path
from django.db import transaction
from job.models import Job, JobMatch
from user.models import ApplicantProfile
import time
import random
from datetime import datetime, timedelta
from django.utils import timezone
from services.db_helpers import batch_update_job_status, save_job_match
from services.ollama_client import OllamaClient, JobMatchResult
from services.logging_helpers import print_progress
from services.parser import Parser

BUFFER_DIR = Path('buffer')
BUFFER_DIR.mkdir(exist_ok=True)

EXPIRY_BUFFER_FILE = BUFFER_DIR / 'expiry_buffer.jsonl'

def run(batch_size=None, job_ids=None, resume=True):
    extract(batch_size, job_ids, resume)

def extract(batch_size=None, job_ids=None, resume=True):
    
    profile = ApplicantProfile.objects.first()
    if not profile:
        print("No ApplicantProfile found. Please create one in the admin panel first.")
        return
        
    if resume:
        evaluated_count = JobMatch.objects.count()
        print(f"Resuming: {evaluated_count} jobs already evaluated in database")

    # We only qualify jobs that are still marked as "Not Applied" (NA)
    jobs = Job.objects.filter(status='NA').order_by('id')
    if job_ids:
        ids = [int(x.strip()) for x in job_ids.split(',')]
        jobs = jobs.filter(id__in=ids)
    if resume:
        # Exclude using a subquery instead of pulling all IDs into Python memory
        jobs = jobs.exclude(id__in=JobMatch.objects.values('job_id'))
    if batch_size:
        jobs = jobs[:batch_size]


    jobs_list = list(check_expiration(jobs))
    total_jobs = len(jobs_list)

    print(f"Starting Job Qualification Pipeline for {total_jobs} jobs...")

    client = OllamaClient()

    for i, job in enumerate(jobs_list, 1):
        print(f"\nEvaluating Job #{job.id}: {job.position} at {job.company}")

        try:
            llm_result, score, decision, contexts = JobMatchResult.run_match(
                client=client,
                profile=profile,
                job=job,
                method='instructor'
            )

            print(f"  -> Score: {score} | Decision: {decision}")
            print(f"  -> Reasoning: {llm_result.reasoning}")
            print(f"  -> Core Foundation:")
            core = llm_result.core_foundation
            print(f"     [CRITICAL] {core.area}: {core.coverage} | Job needs: {core.job_requires} | Candidate has: {core.candidate_has}")

            print(f"  -> Secondary Clusters:")
            for c in llm_result.secondary_clusters:
                print(f"     [{c.importance}] {c.area}: {c.coverage} | Job needs: {c.job_requires} | Candidate has: {c.candidate_has}")

            print(f"  -> Logistical Alignment:")
            print(f"     Role: {llm_result.role_relevance} | Candidate: {llm_result.candidate_level} | Job: {llm_result.job_level} | Salary: {llm_result.salary_alignment}")

            save_job_match(job, score, decision, llm_result, contexts)

            if decision == "APPLY":
                job.status = 'MA'
                job.save()

            
            client.processed += 1
            
        except Exception as e:
            client.failed += 1
            print(f"  -> Error communicating with Ollama: {e}")
            
        client.progress_log(i, total_jobs, f"Job #{job.id} ({job.company})")
    
    # Clear the expiry buffer now that the batch is completely finished matching
    open(EXPIRY_BUFFER_FILE, 'w').close()

    client.final_log(f"\nDone. Processed: {client.processed} | Failed: {client.failed}")


def check_expiration(jobs):
    expired_job_ids = set()
    skipped_job_ids = set()
    checked_job_ids = set()
        
    if EXPIRY_BUFFER_FILE.exists():
        with open(EXPIRY_BUFFER_FILE, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    checked_job_ids.add(entry['job_id'])
                    if entry.get('is_expired'):
                        expired_job_ids.add(entry['job_id'])
                except json.JSONDecodeError:
                    continue
        if checked_job_ids:
            print(f"Resuming Expiry Check: {len(checked_job_ids)} jobs already checked in previous run.")

    if jobs.count() > 0:
        print("Starting URL Expiration Pre-Pass...")
        job_id_list = list(jobs.values_list('id', flat=True))
        joblist = list(jobs)
        try:
            parser = Parser()
            for i, job in enumerate(joblist, 1):
                if job.id in checked_job_ids:
                    continue
                    
                if 'linkedin.com' not in job.apply_url and 'naukri.com' not in job.apply_url:
                    continue

                print(f"  [{i}/{jobs.count()}] Job ID: #{job.id} | Checking URL: {job.apply_url}")
                try:
                    time.sleep(random.uniform(1.5, 3.5))
                    is_exp = parser.is_expired(job.apply_url)
                    entry = {'job_id': job.id, 'is_expired': is_exp}
                    if is_exp:
                        print(f"    -> Expired: {job.company} - {job.position}")
                        expired_job_ids.add(job.id)
                    with open(EXPIRY_BUFFER_FILE, 'a') as f:
                        f.write(json.dumps(entry) + '\n')
                except Exception as e:
                    print(f"    -> Expired/Error: {job.company} - {job.position} ({e})")
                    skipped_job_ids.add(job.id)
                    parser.playwright.stop()
                    parser = Parser()
                    
        finally:
            try:
                parser.playwright.stop()
            except:
                pass
        if expired_job_ids:
            batch_update_job_status(list(expired_job_ids), status='AC')
            
        exclude_ids = expired_job_ids.union(skipped_job_ids)
        return Job.objects.filter(id__in=job_id_list).exclude(id__in=exclude_ids)
    return jobs

