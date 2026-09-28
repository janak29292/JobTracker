import json
import time
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from job.models import Job, TechStack, UnknownTech
from services.ollama_client import OllamaClient
from services.logging_helpers import print_progress

def run(batch_size=None, dry_run=False, add_to_jobs=False):
    """Main entrypoint. Classifies unknown techs and optionally applies to DB."""
    classify(batch_size, dry_run)
    if not dry_run:
        apply_to_db(add_to_jobs)


# ─── Phase 1: Classify via Ollama ───────────────────────────────────────

def classify(batch_size=None, dry_run=False):
    """
    Classify unprocessed techs using Ollama.
    """
    known_techs = set(TechStack.objects.values_list('name', flat=True))

    unprocessed_qs = list(UnknownTech.objects.filter(processed=False))
    
    pending = {}  # {tech: job_id}
    for entry in unprocessed_qs:
        tech = (entry.tech or '').lower().strip()
        if tech and tech != 'unknown' and tech not in pending:
            pending[tech] = entry.job_id

    already_known = {t for t in pending if t in known_techs}
    to_classify = [
        (t, jid) for t, jid in pending.items()
        if t not in known_techs
    ]

    to_classify = to_classify[:batch_size]

    print(
        f"Pending (unprocessed): {len(pending)} unique techs\n"
        f"Already in parser (will auto-accept): {len(already_known)}\n"
        f"Need classification: {len(to_classify)} (limit: {batch_size})"
    )

    job_ids = set(jid for _, jid in to_classify)
    if job_ids:
        print(f"Loading {len(job_ids)} job descriptions...")
    job_map = {}
    for job in Job.objects.filter(id__in=job_ids).only('id', 'job_description'):
        job_map[job.id] = job.job_description or ''

    client = OllamaClient()
    classified = {}
    total = len(to_classify)
    for idx, (tech, job_id) in enumerate(to_classify, 1):
        description = job_map.get(job_id, '')

        try:
            parsed = client.curate_technology(tech, description)
            if not parsed:
                raise ValueError("Empty or invalid JSON response")
        except Exception as e:
            print(f"Ollama request failed for '{tech}': {e}")
            client.failed += 1
            client.progress_log(idx, total, f"{tech}")
            continue

        decision = parsed.get('decision', '').lower().strip()
        corrected = parsed.get('keyword', tech).lower().strip()
        is_required = parsed.get('is_required')

        classified[tech] = {
            'decision': 'accept' if decision != 'reject' else 'reject',
            'corrected': corrected,
            'is_required': is_required
        }
        
        client.processed += 1
        client.progress_log(idx, total, f"{tech}")

    for tech in already_known:
        classified[tech] = {'decision': 'already_known', 'corrected': tech}

    accepted_count = 0
    rejected_count = 0
    skipped_count = 0

    if not dry_run:
        for tech, result in classified.items():
            decision = result['decision']
            corrected = result.get('corrected', tech)
            
            if decision == 'reject':
                UnknownTech.objects.filter(tech__iexact=tech, processed=False).update(
                    processed=True, accepted=False
                )
                rejected_count += 1
            elif decision == 'already_known':
                UnknownTech.objects.filter(tech__iexact=tech, processed=False).update(
                    processed=True, accepted=True, mapped_to=tech, added_to_parser=True
                )
                skipped_count += 1
            else: # accept
                update_fields = {
                    'processed': True,
                    'accepted': True,
                    'mapped_to': corrected,
                }
                if isinstance(result.get('is_required'), bool):
                    update_fields['required'] = result['is_required']
                
                UnknownTech.objects.filter(tech__iexact=tech, processed=False).update(**update_fields)
                accepted_count += 1

    accepted_list = sorted(set(
        res['corrected'] for tech, res in classified.items() if res['decision'] == 'accept'
    ))
    rejected_list = sorted(set(
        tech for tech, res in classified.items() if res['decision'] == 'reject'
    ))

    client.final_log(
        f"\n{'=' * 60}\n"
        f"Classified: {accepted_count} accepted | "
        f"{rejected_count} rejected | "
        f"{skipped_count} already known | "
        f"{client.failed} failed\n"
        f"{'=' * 60}"
    )

    print(f"--- ACCEPTED ({len(accepted_list)}) ---")
    print(f"[{', '.join(accepted_list)}]")

    print(f"--- REJECTED ({len(rejected_list)}) ---")
    print(f"[{', '.join(rejected_list)}]")

    if dry_run:
        print("[DRY RUN] DB was NOT updated.")


# ─── Phase 2: Apply to DB ──────────────────────────────────────────────

def apply_to_db(add_to_jobs=False):
    """
    Apply accepted techs to TechStack and optionally to Jobs.
    All DB writes are wrapped in a single transaction.
    """
    needs_parser = list(UnknownTech.objects.filter(accepted=True, added_to_parser=False))
    
    needs_job = []
    if add_to_jobs:
        needs_job = list(UnknownTech.objects.filter(accepted=True, added_to_job=False))

    if not needs_parser and not needs_job:
        print("Nothing to apply to DB.")
        return

    print(
        f"Applying: {len(needs_parser)} to TechStack, "
        f"{len(needs_job)} to jobs"
    )

    new_tech_names = set(
        e.mapped_to for e in needs_parser if e.mapped_to
    )

    modified_jobs = {}

    try:
        with transaction.atomic():
            if new_tech_names:
                TechStack.objects.bulk_create(
                    [TechStack(name=t) for t in new_tech_names],
                    ignore_conflicts=True
                )
            
            if needs_parser:
                UnknownTech.objects.filter(id__in=[e.id for e in needs_parser]).update(added_to_parser=True)

            if needs_job:
                job_ids = set(e.job_id for e in needs_job if e.job_id)
                jobs_in_db = Job.objects.filter(id__in=job_ids).in_bulk()

                for entry in needs_job:
                    if not entry.job_id:
                        continue
                        
                    job = jobs_in_db.get(entry.job_id)
                    if not job:
                        continue

                    mapped_to = entry.mapped_to
                    if not mapped_to:
                        continue

                    all_new = json.loads(job.tech_stack_all_new) if job.tech_stack_all_new else []
                    if mapped_to not in all_new:
                        all_new.append(mapped_to)
                    job.tech_stack_all_new = json.dumps(all_new)

                    if entry.required:
                        primary_new = json.loads(
                            job.tech_stack_primary_new) if job.tech_stack_primary_new else []
                        if mapped_to not in primary_new:
                            primary_new.append(mapped_to)
                        job.tech_stack_primary_new = json.dumps(primary_new)

                    modified_jobs[job.id] = job

                if needs_job:
                    UnknownTech.objects.filter(id__in=[e.id for e in needs_job]).update(added_to_job=True)

                if modified_jobs:
                    Job.objects.bulk_update(
                        modified_jobs.values(),
                        ['tech_stack_all_new', 'tech_stack_primary_new']
                    )

    except Exception as e:
        print(f"DB transaction failed: {e}")
        return

    parser_count = len(needs_parser)
    job_count = len(modified_jobs)
    print(f"Applied: {parser_count} to TechStack, {job_count} jobs updated")
