import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.db import transaction
from job.models import Job, TechStack, UnknownTech, SENIORITY_LEVEL, ROLE_CATEGORY
from services.ollama_client import OllamaClient, OllamaUnavailableError
from services.logging_helpers import print_progress
from rapidfuzz import fuzz, process

BUFFER_DIR = Path('buffer')
BUFFER_DIR.mkdir(exist_ok=True)

OVERRIDES_FILE = BUFFER_DIR / 'normalization_overrides.jsonl'


def run(batch_size=None, platform=None, dry_run=False, resume=False, job_ids=None):
    """Main entrypoint. Extracts JD fields and saves to DB."""
    extract(batch_size, platform, dry_run, resume, job_ids)


def extract(batch_size=None, platform=None, dry_run=False, resume=False, job_ids=None):
    """
    Extract structured fields from job descriptions using Ollama and save to DB.
    """
    # Build queryset
    jobs = Job.objects.all().order_by('id')
    if platform:
        jobs = jobs.filter(platform=platform)
    if job_ids:
        ids = [int(x.strip()) for x in job_ids.split(',')]
        jobs = jobs.filter(id__in=ids)
    if resume:
        jobs = jobs.filter(tech_stack_all_new__isnull=True)

    total = jobs.count()
    jobs = list(jobs[:batch_size]) if batch_size else list(jobs)

    print(f"Jobs to extract: {len(jobs)} / {total} total")

    # Build known tech whitelist from TechStack DB table
    known_techs_list = list(TechStack.objects.values_list('name', flat=True))
    known_techs_set = set(known_techs_list)

    client = OllamaClient()
    new_unknowns_count = 0

    try:
        for i, job in enumerate(jobs, 1):

            try:
                result = client.extract_jd_fields(
                    job.job_description,
                    seniority_levels=list(SENIORITY_LEVEL.keys()),
                    role_categories=list(ROLE_CATEGORY.keys())
                )
                entry = {
                    'job_id': job.id,
                    'saved': False,
                    **result,
                }
                client.progress_log(i, len(jobs), f"Job #{job.id} ({job.company})")

                update_fields, new_unknowns = _validate_entry(
                    client, entry, f"{i}/{len(jobs)}", known_techs_set, known_techs_list
                )
                entry = {**entry, **update_fields}
                new_unknowns_count += len(new_unknowns)

                if not dry_run:
                    allowed_keys = [
                        'role_summary', 'seniority_level', 'role_category',
                        'role_title', 'tech_stack_primary_new', 'tech_stack_all_new',
                        'experience_min', 'experience_max'
                    ]
                    for k in allowed_keys:
                        if k in entry:
                            setattr(job, k, entry[k])
                    job.save(update_fields=allowed_keys)

                client.processed += 1

            except OllamaUnavailableError as e:
                print(f"Ollama unavailable: {e}")
                break
            except TimeoutError as e:
                print(f"{e}")
                client.failed += 1
            except Exception as e:
                print(f"Error on job #{job.id}: {e}")
                client.failed += 1

            # client.progress_log(i, len(jobs), f"Job #{job.id} ({job.company})")

    except KeyboardInterrupt:
        print("Interrupted")

    client.final_log(f"Done. Extracted: {client.processed} | Failed: {client.failed} | New unknown techs: {new_unknowns_count}")


# ─── Internal helpers ──────────────────────────────────────────────────

def _inline_log(batch_start, extracted, jobs, i, job, gpu_temp):
    elapsed = time.time() - batch_start
    avg = elapsed / max(extracted, 1)
    eta = timedelta(seconds=int(avg * (len(jobs) - i)))
    done_at = (datetime.now() + timedelta(seconds=avg * (len(jobs) - i))).strftime('%-I:%M %p')
    line = f"[{i}/{len(jobs)}] Job #{job.id} ({job.company}) | GPU: {gpu_temp or '?'}°C | ETA: {eta} (~{done_at})"
    print_progress(line)


def _final_log(batch_start, extracted, gpu_temps, failed, new_unknowns_count):
    batch_time = timedelta(seconds=int(time.time() - batch_start))
    avg_per_job = (time.time() - batch_start) / max(extracted, 1)
    temp_str = ''
    if gpu_temps:
        temp_str = f" | GPU: {min(gpu_temps)}/{sum(gpu_temps) // len(gpu_temps)}/{max(gpu_temps)}°C"
    print(
        f"Done. Extracted: {extracted} | Failed: {failed} | New unknown techs: {new_unknowns_count} | "
        f"Time: {batch_time} | Avg: {avg_per_job:.1f}s/job{temp_str}"
    )


def _validate_entry(client, entry, progress, known_techs_set, known_techs_list):
    job_id = entry['job_id']
    print_progress(f"  [{progress}] Job #{job_id} - Validating")

    # Validate seniority/category against model choices
    seniority = entry.get('seniority_level') if entry.get('seniority_level') in SENIORITY_LEVEL else None
    role_cat = entry.get('role_category') if entry.get('role_category') in ROLE_CATEGORY else None

    # Ollama-based tech normalization
    all_extracted = []
    for t in entry.get('required_skills', []) + entry.get('preferred_skills', []):
        t_clean = t.lower().strip()
        if t_clean:
            all_extracted.append(t_clean)

    # Separate already-known (exact match) from needing normalization
    needs_normalization = [t for t in all_extracted if t not in known_techs_set]
    already_known = [t for t in all_extracted if t in known_techs_set]

    normalized_map = {}
    normalization_overrides = []

    if needs_normalization:
        # Pre-filter: narrow known techs to ~25 candidates per unknown tech
        all_candidates = set()
        for tech in needs_normalization:
            matches = process.extract(tech, known_techs_list, scorer=fuzz.WRatio, limit=25)
            # matches = process.extract(tech, known_techs_list, scorer=fuzz.token_set_ratio, limit=25)
            all_candidates.update(match[0] for match in matches)
        candidates_list = sorted(all_candidates)
        print(candidates_list)
        try:
            normalized_map, normalization_overrides = client.normalize_tech_stack(needs_normalization, candidates_list)
        except Exception as e:
            print(f"  Normalization failed for job #{job_id}: {e}")
            # Fall through — unknowns will be logged as-is

    # Pre-fetch logged unknowns from DB to avoid duplication
    logged_unknowns = set(UnknownTech.objects.values_list('tech', flat=True))

    required_extracted = [t.lower().strip() for t in entry.get('required_skills', []) if t.lower().strip()]

    new_unknowns = []
    # Build final tech lists
    resolved_techs = set(already_known)
    for ext_tech, canonicals in normalized_map.items():
        is_all_unknown = True
        for canonical in canonicals:
            if canonical != 'unknown':
                resolved_techs.add(canonical)
                is_all_unknown = False

        if is_all_unknown and ext_tech not in logged_unknowns:
            new_unknowns.append(
                UnknownTech(
                    tech=ext_tech,
                    job_id=job_id,
                    processed=False,
                    accepted=False,
                    added_to_job=False,
                    added_to_parser=False,
                    required=ext_tech in required_extracted,
                    mapped_to=None
                )
            )
            logged_unknowns.add(ext_tech)

    # Bulk create new unknowns
    if new_unknowns:
        UnknownTech.objects.bulk_create(new_unknowns, ignore_conflicts=True)

    # Append normalization overrides (Ollama returned invalid values)
    if normalization_overrides:
        with open(OVERRIDES_FILE, 'a') as f:
            for o in normalization_overrides:
                o['job_id'] = job_id
                f.write(json.dumps(o) + '\n')

    # Split back into required/preferred based on resolved names
    required_resolved = []
    for t in entry.get('required_skills', []):
        t_clean = t.lower().strip()
        canonicals = normalized_map.get(t_clean, [t_clean])
        if isinstance(canonicals, str):
            canonicals = [canonicals]
        for canonical in canonicals:
            if canonical in resolved_techs and canonical not in required_resolved:
                required_resolved.append(canonical)

    preferred_resolved = []
    for t in entry.get('preferred_skills', []):
        t_clean = t.lower().strip()
        canonicals = normalized_map.get(t_clean, [t_clean])
        if isinstance(canonicals, str):
            canonicals = [canonicals]
        for canonical in canonicals:
            if canonical in resolved_techs and canonical not in preferred_resolved:
                preferred_resolved.append(canonical)

    return {
        'role_summary': entry.get('role_summary', ''),
        'seniority_level': seniority,
        'role_category': role_cat,
        'role_title': entry.get('role_title', ''),
        'tech_stack_primary_new': json.dumps(required_resolved),
        'tech_stack_all_new': json.dumps(required_resolved + preferred_resolved),
    }, new_unknowns
