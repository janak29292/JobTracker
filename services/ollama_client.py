import json
import logging
import re
import subprocess
import time
from datetime import timedelta, datetime

import requests
from django.conf import settings
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field, ValidationError
import instructor
from openai import OpenAI
from job.models import Job
from services.logging_helpers import print_progress
from user.models import ApplicantProfile

logger = logging.getLogger(__name__)

class OllamaUnavailableError(Exception):
    """Raised when the Ollama service is unreachable or errors out repeatedly."""
    pass

class OllamaClient:
    GPU_TEMP_THRESHOLD = 85  # °C — pause if above this

    def __init__(self):
        self.base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
        self.model = getattr(settings, 'OLLAMA_MODEL', 'mistral:7b')
        self.timeout = 300  # seconds — Gemma4 needs more time for complex JDs
        self.last_raw_response = None  # stored for debugging failed parses
        self.last_raw_payload = None
        # We use the OpenAI Python client because Ollama natively supports the OpenAI API spec.
        # This points STRICTLY to your local machine (http://localhost:11434).
        # Absolutely NO data leaves your machine, and you are not paying for any API.
        self.instructor_client = instructor.from_openai(
            OpenAI(
                base_url=f"{self.base_url}/v1",
                api_key="ollama", # Required by the library, but completely ignored by local Ollama
            ),
            mode=instructor.Mode.JSON,
        )
        self.batch_start = time.time()
        self.last_log_time = time.time()
        self.processed = 0
        self.failed = 0
        self.gpu_temps = []

    def progress_log(self, i, n, text):
        gpu_temp = self.get_gpu_temp()
        if gpu_temp:
            self.gpu_temps.append(gpu_temp)

        now = time.time()
        elapsed = now - self.batch_start
        job_duration = now - self.last_log_time
        self.last_log_time = now
        
        avg = elapsed / max(self.processed, 1)
        eta = timedelta(seconds=int(avg * (n - i)))
        done_at = (datetime.now() + timedelta(seconds=avg * (n - i))).strftime('%-I:%M %p')
        line = f"[{i}/{n}] {text} | Took: {job_duration:.1f}s | GPU: {gpu_temp or '?'}°C | Elapsed: {timedelta(seconds=int(elapsed))} | ETA: {eta} (~{done_at})"
        print_progress(line)

    def final_log(self, text):
        batch_time = timedelta(seconds=int(time.time() - self.batch_start))
        avg_per_job = (time.time() - self.batch_start) / max(self.processed, 1)
        temp_str = ''
        if self.gpu_temps:
            temp_str = f" | GPU: {min(self.gpu_temps)}/{sum(self.gpu_temps) // len(self.gpu_temps)}/{max(self.gpu_temps)}°C"
        print(
            f"{text} | "
            f"Time: {batch_time} | Avg: {avg_per_job:.1f}s/job{temp_str}"
        )


    def get_gpu_temp(self) -> Optional[int]:
        """Read NVIDIA GPU temperature. Returns None if unavailable."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return int(result.stdout.strip().split('\n')[0])
        except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
            pass
        return None

    def _thermal_check(self):
        """Pause if GPU temperature exceeds threshold. Returns (paused, temp)."""
        temp = self.get_gpu_temp()
        if temp and temp >= self.GPU_TEMP_THRESHOLD:
            print(f"GPU at {temp}°C, cooling down for 30s...")
            time.sleep(30)
            return True, temp
        return False, temp

    def generate(self, prompt: str, max_retries: int = 3) -> dict:
        """
        Send a prompt to Ollama and return the parsed JSON response.
        Raises OllamaUnavailableError on connection failure after max_retries.
        Returns empty dict on JSON parse failure after max_retries.
        """
        self._thermal_check()

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "num_predict": 4096,
                "temperature": 0
            }
        }

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                result = response.json()

                content = result.get('response', '{}').strip()
                self.last_raw_response = response
                self.last_raw_payload = payload
                
                # Extract just the JSON block, ignoring <think> tags or markdown ```json wrappers
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)

                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # Try to salvage truncated JSON by appending closing braces
                    # Ollama sometimes runs out of tokens before closing all objects
                    last_brace = content.rfind('}')
                    if last_brace > 0:
                        truncated = content[:last_brace + 1]
                        for extra in ['', '}', '}}', '}}}']:
                            try:
                                return json.loads(truncated + extra)
                            except json.JSONDecodeError:
                                continue
                        # Also try closing any open arrays first
                        for extra in [']}}', ']}', '"]}', '"]}}']: 
                            try:
                                return json.loads(truncated + extra)
                            except json.JSONDecodeError:
                                continue
                    
                    if attempt == max_retries - 1:
                        logger.error("Failed to parse Ollama JSON response after maximum retries")
                        return {}
                    time.sleep(2)
                    continue

            except requests.exceptions.ReadTimeout as e:
                if attempt == max_retries - 1:
                    logger.error(f"Ollama request timed out after {self.timeout}s: {e}")
                    raise TimeoutError(f"Ollama timed out after {self.timeout}s") from e
                time.sleep(2)
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to communicate with Ollama: {e}")
                    raise OllamaUnavailableError(f"Ollama service unavailable at {self.base_url}") from e
                time.sleep(2)

        return {}

    def call_model_structured(self, prompt: str, response_model: type[BaseModel], method: str = "instructor") -> tuple[BaseModel, any]:
        """
        Calls Ollama and returns a validated Pydantic model.
        method: "instructor" (uses OpenAI /v1/chat/completions with Instructor)
                "raw_generate" (uses /api/generate with native format: json and schema injection)
        """
        self._thermal_check()
        
        if method == "instructor":
            result, raw_response = self.instructor_client.chat.completions.create_with_completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_model=response_model,
                temperature=0,
            )
            return result, raw_response
            
        elif method == "raw_generate":
            schema_str = json.dumps(response_model.model_json_schema(), indent=2)
            augmented_prompt = f"{prompt}\n\nIMPORTANT: You must output ONLY valid JSON that exactly matches this JSON schema:\n{schema_str}"
            
            payload = {
                "model": self.model,
                "prompt": augmented_prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0}
            }
            
            response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            raw_response_str = data.get("response", "")
            
            try:
                result = response_model.model_validate_json(raw_response_str)
            except ValidationError:
                # The LLM sometimes outputs the literal string "null" instead of the primitive null
                # when instructed to "output null" outside of Instructor's guardrails.
                cleaned_str = re.sub(r'":\s*"null"', '": null', raw_response_str)
                result = response_model.model_validate_json(cleaned_str)
            return result, raw_response_str
            
        else:
            raise ValueError(f"Unknown method {method}")

    def extract_tech_stack(self, description: str) -> Dict[str, List[str]]:
        """
        Sends the job description to Ollama to extract and classify tech stacks.
        Returns a dict: {"required": ["react", "python"], "preferred": ["aws"]}
        """
        prompt = f"""You are a job description tech stack analyzer. Read the following job description and extract ALL technologies, tools, frameworks, programming languages, databases, cloud services, and technical skills mentioned.

Classify each technology as:
- "required": explicitly mandatory, must-have, essential, required, minimum qualification
- "preferred": nice-to-have, bonus, desired, beneficial, good-to-have, preferred, valued but not required

Return ONLY valid JSON, no explanation:
{{"required": ["tech1", "tech2"], "preferred": ["tech3"]}}

Rules:
- Use lowercase, canonical names (e.g., "react" not "ReactJS", "postgresql" not "Postgres").
- Do NOT include educational degrees (e.g., B.S., Ph.D.), certifications (e.g., AWS Certified, CISSP), or compliances (e.g., HIPAA, SOC2, ABC compliant).

Job Description:
{description}"""

        result = self.generate(prompt)
        return {
            "required": result.get("required", []),
            "preferred": result.get("preferred", [])
        }

    def extract_jd_fields(
        self, 
        description: str,
        seniority_levels: List[str] = None,
        role_categories: List[str] = None
    ) -> dict:
        """
        Extract structured fields from a job description in a single call.
        Returns: role_summary, role_title, seniority_level, role_category,
                 required_skills, preferred_skills
        """
        seniority_options = "/".join(seniority_levels) if seniority_levels else "intern/junior/mid/senior/lead/manager/director/vp/cto/other"
        role_options = "/".join(role_categories) if role_categories else "backend/frontend/fullstack/data_engineer/data_science/devops/mobile/qa/other"

        prompt = f"""You are a job description analyzer. Extract these fields from the description below.

Return ONLY valid JSON:
{{"role_summary": "1-2 sentence summary of the role",
"role_title": "short standardized title (e.g. Backend Developer, Data Scientist)",
"seniority_level": "<one of: {seniority_options}>",
"role_category": "<one of: {role_options}>",
"experience_min": "integer (minimum years of experience required)",
"experience_max": "integer (maximum years of experience required)",
"required_skills": ["tech1", "tech2"],
"preferred_skills": ["tech3"]}}

Rules:
- Use lowercase canonical tech names (e.g. "react" not "ReactJS", "postgresql" not "Postgres")
- Only include technologies, frameworks, tools, languages, databases, cloud services
- Do NOT include soft skills, methodologies, or generic terms
- Do NOT include educational degrees (e.g., B.S., Ph.D.), certifications (e.g., AWS Certified, CISSP), or compliances (e.g., HIPAA, SOC2, ABC compliant)
- If a field cannot be determined, use "unknown"

Job Description:
{description}"""

        result = self.generate(prompt)
        
        def parse_exp(val):
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        return {
            "role_summary": result.get("role_summary", "unknown"),
            "role_title": result.get("role_title", "unknown"),
            "seniority_level": result.get("seniority_level", "unknown"),
            "role_category": result.get("role_category", "unknown"),
            "experience_min": parse_exp(result.get("experience_min")),
            "experience_max": parse_exp(result.get("experience_max")),
            "required_skills": result.get("required_skills", []),
            "preferred_skills": result.get("preferred_skills", []),
        }

    def normalize_tech_stack(self, extracted_techs: List[str], known_techs: List[str]) -> Dict[str, List[str]]:
        """
        Use Ollama to map extracted tech names to canonical names from the known list.
        Returns: {"extracted_name": ["canonical_name"] or ["unknown"]}
        e.g. {"reactjs": ["react"], "aws sns/sqs": ["sns", "sqs"], "cvs": ["unknown"]}
        """
        prompt = f"""You are a tech stack normalizer. Given a list of extracted technology names and a list of known canonical names, map each extracted name to its matching canonical name.

If an extracted name is a variant, alias, or alternate spelling of a known name, map it to that known name.
If an extracted name contains MULTIPLE distinct technologies (e.g., "aws sns/sqs"), map it to a list of those known names.
If an extracted name does NOT match any known name (it is genuinely new, or a typo/misspelling), map it to "unknown".

Be careful with similar names:
- "nestjs" and "nextjs" are DIFFERENT technologies
- "react" and "react native" are DIFFERENT technologies
- "postgres" and "postgresql" are the SAME technology

Return ONLY valid JSON mapping each extracted name to its canonical match, a list of matches, or "unknown":
{{"extracted1": "canonical1", "extracted_multiple": ["canonical2", "canonical3"], "extracted3": "unknown"}}

Known canonical names:
{json.dumps(known_techs)}

Extracted names to normalize:
{json.dumps(extracted_techs)}"""

        print(f"\n[Normalization] Extracted techs: {extracted_techs}")
        print(f"[Normalization] Passed candidates: {len(known_techs)}")
        
        result = self.generate(prompt)
        
        print(f"[Normalization] LLM returned: {json.dumps(result)}")
        
        # Validate: every value must be a string or list of strings and either in known_techs or "unknown"
        known_set = set(known_techs)
        validated = {}
        overrides = []
        for ext, canonical in result.items():
            if isinstance(canonical, str):
                canonical = canonical.strip("'\" ")
                if canonical == "unknown" or canonical in known_set:
                    validated[ext] = [canonical]
                else:
                    overrides.append({"tech": ext, "ollama_returned": repr(canonical)})
                    validated[ext] = ["unknown"]
            elif isinstance(canonical, list):
                valid_items = []
                for c in canonical:
                    if isinstance(c, str):
                        c = c.strip("'\" ")
                        if c == "unknown" or c in known_set:
                            valid_items.append(c)
                
                if valid_items:
                    validated[ext] = valid_items
                else:
                    overrides.append({"tech": ext, "ollama_returned": repr(canonical)})
                    validated[ext] = ["unknown"]
            else:
                overrides.append({"tech": ext, "ollama_returned": repr(canonical)})
                validated[ext] = ["unknown"]

        return validated, overrides

    def curate_technology(self, tech: str, description: str) -> dict:
        """
        Evaluate an unknown technology against a job description.
        Returns a dict: {"keyword": "corrected_keyword", "decision": "accept_or_reject", "is_required": true/false}
        """
        prompt = f"""Given the following keyword extracted from a job description, decide:
- "accept" if it is a valid technology, tool, framework, library, platform, service, protocol, database,
    programming language, industry-specific domain, or technical concept worth tracking in a job search.
- "reject" if it clearly falls into one of these categories:
  1. Educational degrees (e.g., "b.s.", "m.tech", "phd", "mba")
  2. Professional certifications as credentials (e.g., "aws certified solutions architect", "pmp certification")
  3. Soft skills (e.g., "communication", "leadership", "teamwork", "problem-solving")
  4. Generic job description filler (e.g., "years of experience", "fast-paced environment", "strong analytical skills")
  5. Generic business practices not tied to any specific industry (e.g., "best practices", "knowledge management",
   "stakeholder management")
  6. The literal string "unknown"

Rules:
- Only accept the keyword if you can reasonably deduce from the context that it represents a hard skill, tool,
  methodology, or specific industry domain. If it is a generic noun (e.g., a location, a person, a company name, a color),
  reject it. When in doubt, lean towards rejecting unless it is clearly a "technical" requirement.
- Use the full job description to understand what the keyword actually refers to.
- Domain-specific terms like "reinsurance", "hvac", "underwriting" are VALID — accept them.
- Methodology names like "scrum", "kanban" are VALID — accept them.
- Short acronyms like "can", "r", "c", "ai" are VALID — accept them.
- Industry-specific software systems are VALID even if you don't recognize them — accept them.
- IMPORTANT: If you "accept" the keyword, you MUST correct any spelling mistakes or formatting
    errors in the keyword itself. Return the standard, widely-recognized spelling of the technology
    (e.g., if input is "javascrpt", return "javascript").
- IMPORTANT: Determine if the technology is mandatory or a plus/bonus. Set "is_required" to true ONLY if 
    the context explicitly indicates it is mandatory (e.g., "must have", "minimum qualification", "required").
    Set to false if it's "nice to have", "preferred", or ambiguous.

Return ONLY valid JSON:
{{"keyword": "corrected_keyword", "decision": "accept_or_reject", "is_required": true}}

Keyword: {tech}
Job Description: {description}"""

        return self.generate(prompt)

# ──────────────────────────────────────────────────────────────────────
# DOMAIN GENERALIZABILITY NOTE
# ──────────────────────────────────────────────────────────────────────
# The cluster/importance/coverage scoring architecture is domain-agnostic.
# It works for any profession — marketing, finance, nursing, design, etc.
#
# What IS domain-specific are the EXAMPLES in the Field descriptions
# and the prompt guidelines (e.g. "AWS covers GCP", "Celery covers
# RabbitMQ"). These SWE-specific examples ground the LLM and improve
# output reliability for software engineering roles.
#
# To expand to other domains in the future:
#   1. The Pydantic schema (TechCluster, JobMatchResult) stays the same.
#   2. The scoring logic (calculate_score, pre_compute) stays the same.
#   3. Only the EXAMPLES need to change:
#      - Field descriptions: swap SWE cluster examples for domain-relevant
#        ones (e.g. "Ad Platforms", "Accounting Standards", "Certifications")
#      - Prompt guidelines: swap equivalence examples
#        (e.g. "Google Ads experience covers Meta Ads concepts")
#   4. Optionally, add a `domain` field to ApplicantProfile and use it
#      to dynamically select the example set in the prompt builder.
#
# Do NOT try to make the examples generic "for all domains" — that
# reduces reliability for every domain. Keep them specific to the
# active domain and swap them when expanding.
# ──────────────────────────────────────────────────────────────────────
# class ExtractedJD(BaseModel):
#     role_summary: str = Field(description="1-2 sentence summary of the role")
#     role_title: str = Field(description="Short standardized title (e.g. Backend Developer)")
#     seniority_level: str
#     role_category: str
#     experience_min: Optional[int]
#     experience_max: Optional[int]
#     required_skills: List[str]
#     preferred_skills: List[str]
#
#     @classmethod
#     def extract_fields(cls, client: 'OllamaClient', description: str, seniority_levels: List[str] = None, role_categories: List[str] = None, method: str = "raw_generate") -> 'ExtractedJD':
#         seniority_options = "/".join(seniority_levels) if seniority_levels else "intern/junior/mid/senior/lead/manager/director/vp/cto/other"
#         role_options = "/".join(role_categories) if role_categories else "backend/frontend/fullstack/data_engineer/data_science/devops/mobile/qa/other"
#
#         prompt = f"""You are a job description analyzer. Extract these fields from the description below.
#
# seniority_level must be one of: {seniority_options}
# role_category must be one of: {role_options}
#
# Rules:
# - Use lowercase canonical tech names (e.g. "react" not "ReactJS", "postgresql" not "Postgres")
# - Only include technologies, frameworks, tools, languages, databases, cloud services
# - Do NOT include soft skills, methodologies, or generic terms
# - Do NOT include educational degrees (e.g., B.S., Ph.D.), certifications (e.g., AWS Certified, CISSP), or compliances (e.g., HIPAA, SOC2, ABC compliant)
# - If a field cannot be determined, use "unknown"
#
# Job Description:
# {description}"""
#         result, _ = client.call_model_structured(prompt, cls, method)
#         return result
#
#
# class TechMapping(BaseModel):
#     extracted_name: str
#     canonical_names: List[str]
#
# class NormalizationResult(BaseModel):
#     mappings: List[TechMapping]
#
#     @classmethod
#     def normalize(cls, client: 'OllamaClient', extracted_techs: List[str], known_techs: List[str], method: str = "raw_generate") -> tuple[Dict[str, List[str]], List[dict]]:
#         prompt = f"""You are a tech stack normalizer. Given a list of extracted technology names and a list of known canonical names, map each extracted name to its matching canonical name.
#
# If an extracted name is a variant, alias, or alternate spelling of a known name, map it to that known name.
# If an extracted name contains MULTIPLE distinct technologies (e.g., "aws sns/sqs"), map it to a list of those known names.
# If an extracted name does NOT match any known name (it is genuinely new, or a typo/misspelling), map it to ["unknown"].
#
# Be careful with similar names:
# - "nestjs" and "nextjs" are DIFFERENT technologies
# - "react" and "react native" are DIFFERENT technologies
# - "postgres" and "postgresql" are the SAME technology
#
# Known canonical names:
# {json.dumps(known_techs)}
#
# Extracted names to normalize:
# {json.dumps(extracted_techs)}"""
#         result, _ = client.call_model_structured(prompt, cls, method)
#
#         known_set = set(known_techs)
#         validated = {}
#         overrides = []
#
#         for mapping in result.mappings:
#             ext = mapping.extracted_name
#             valid_items = []
#             for c in mapping.canonical_names:
#                 c = c.strip("'\" ")
#                 if c == "unknown" or c in known_set:
#                     valid_items.append(c)
#
#             if valid_items:
#                 validated[ext] = valid_items
#             else:
#                 overrides.append({"tech": ext, "ollama_returned": repr(mapping.canonical_names)})
#                 validated[ext] = ["unknown"]
#
#         return validated, overrides
#
#
# class CurationResult(BaseModel):
#     keyword: str
#     decision: Literal["accept", "reject"]
#     is_required: bool
#
#     @classmethod
#     def curate(cls, client: 'OllamaClient', tech: str, description: str, method: str = "raw_generate") -> 'CurationResult':
#         prompt = f"""Given the following keyword extracted from a job description, decide:
# - "accept" if it is a valid technology, tool, framework, library, platform, service, protocol, database,
#     programming language, industry-specific domain, or technical concept worth tracking in a job search.
# - "reject" if it clearly falls into one of these categories:
#   1. Educational degrees (e.g., "b.s.", "m.tech", "phd", "mba")
#   2. Professional certifications as credentials (e.g., "aws certified solutions architect", "pmp certification")
#   3. Soft skills (e.g., "communication", "leadership", "teamwork", "problem-solving")
#   4. Generic job description filler (e.g., "years of experience", "fast-paced environment", "strong analytical skills")
#   5. Generic business practices not tied to any specific industry (e.g., "best practices", "knowledge management",
#    "stakeholder management")
#   6. The literal string "unknown"
#
# Rules:
# - Only accept the keyword if you can reasonably deduce from the context that it represents a hard skill, tool,
#   methodology, or specific industry domain. If it is a generic noun (e.g., a location, a person, a company name, a color),
#   reject it. When in doubt, lean towards rejecting unless it is clearly a "technical" requirement.
# - Use the full job description to understand what the keyword actually refers to.
# - Domain-specific terms like "reinsurance", "hvac", "underwriting" are VALID — accept them.
# - Methodology names like "scrum", "kanban" are VALID — accept them.
# - Short acronyms like "can", "r", "c", "ai" are VALID — accept them.
# - Industry-specific software systems are VALID even if you don't recognize them — accept them.
# - IMPORTANT: If you "accept" the keyword, you MUST correct any spelling mistakes or formatting
#     errors in the keyword itself. Return the standard, widely-recognized spelling of the technology
#     (e.g., if input is "javascrpt", return "javascript").
# - IMPORTANT: Determine if the technology is mandatory or a plus/bonus. Set "is_required" to true ONLY if
#     the context explicitly indicates it is mandatory (e.g., "must have", "minimum qualification", "required").
#     Set to false if it's "nice to have", "preferred", or ambiguous.
#
# Keyword: {tech}
# Job Description: {description}"""
#         result, _ = client.call_model_structured(prompt, cls, method)
#         return result

class TechCluster(BaseModel):
    """A group of related technologies from the job requirements."""
    area: str = Field(
        description="Short name for this technology cluster. Examples: "
        "'Primary Language & Framework', 'Cloud Platform', 'Databases & Caching', "
        "'Message Queues & Event Systems', 'DevOps & Infrastructure', "
        "'Frontend', 'Data & ML', 'Observability & Monitoring', 'Code Quality & Tooling'"
    )
    job_requires: List[str] = Field(
        default_factory=list,
        description="The specific technologies the job requires in this cluster"
    )
    candidate_has: List[str] = Field(
        default_factory=list,
        description="Technologies the candidate has that are relevant to this cluster, "
        "including equivalent/comparable tools even if not an exact name match"
    )
    importance: str = Field(
        description="How important this cluster is to the job. One of: "
        "CRITICAL - the job cannot be done without these skills "
        "(e.g. primary language, core framework). "
        "IMPORTANT - significant requirements but a strong candidate could ramp up "
        "(e.g. specific cloud provider, database variant, secondary tooling). "
        "NICE_TO_HAVE - preferred/bonus skills, not blocking "
        "(e.g. code quality tools, specific IDE, CI/CD preferences)"
    )
    coverage: str = Field(
        description="How well the candidate covers this cluster. One of: "
        "EXACT_MATCH - candidate has the exact skills listed "
        "(e.g. job requires Python/Django and candidate knows Python/Django). "
        "COVERED - candidate has direct equivalents that fully transfer "
        "(e.g. AWS experience covers a GCP requirement, Celery covers RabbitMQ concepts). "
        "BUILDABLE - candidate has the underlying concepts and could learn these specific tools quickly "
        "(e.g. knows SQL databases but hasn't used this specific NoSQL variant). "
        "PARTIAL - candidate has some skills in this cluster but is missing key ones that require "
        "real learning effort, not just a tool swap "
        "(e.g. knows Docker but not Kubernetes/Terraform, or knows basic Python but not advanced async patterns). "
        "GAP - candidate lacks both the specific tools AND the underlying concepts "
        "(e.g. backend dev with no ML/data science foundation for an ML cluster). "
        "WRONG_STACK - this cluster's core technology is from a fundamentally different ecosystem "
        "and the candidate would need a major retraining "
        "(e.g. Java/Spring Boot cluster for a Python-only developer)"
    )


class JobMatchResult(BaseModel):
    """Structured job match evaluation using technology clusters."""
    
    core_foundation: TechCluster = Field(
        description="The absolute, non-transferable core technical foundation of the role "
        "(e.g., Primary Programming Language(s) for a developer, Core Medical Licenses for a nurse). "
        "If the job strictly requires multiple core technologies, group them all here. "
        "This represents the critical technical prerequisite(s) for the job."
    )
    
    secondary_clusters: List[TechCluster] = Field(
        default_factory=list,
        description="Group the remaining technical requirements into logical technology clusters. "
        "Do not include the core foundation here. "
        "Typically a job has 2-5 secondary clusters. "
        "Include ONLY technical skills — not soft skills, experience years, education,"
        " abstract competencies, general responsibilities, architectural concepts etc."
    )
    
    @property
    def clusters(self) -> List[TechCluster]:
        return [self.core_foundation] + self.secondary_clusters
    
    # Role-level assessments
    candidate_level: Optional[Literal["UNKNOWN", "INTERN", "JUNIOR", "MID", "SENIOR", "LEAD", "MANAGER", "DIRECTOR", "VP", "CXO"]] = Field(
        default=None,
        description="The hierarchical seniority level of the candidate based strictly on their explicit titles (ignore years of experience). "
        "Output UNKNOWN only if there is absolutely no way to determine the level."
    )
    
    job_level: Optional[Literal["UNKNOWN", "INTERN", "JUNIOR", "MID", "SENIOR", "LEAD", "MANAGER", "DIRECTOR", "VP", "CXO"]] = Field(
        default=None,
        description="The hierarchical seniority level required by the job, based on the job title. "
        "Only use years of experience to deduce the level if the title is completely ambiguous. "
        "Output UNKNOWN only if the job description provides zero clues about seniority."
    )
    
    salary_alignment: Optional[Literal["EXCEEDS", "MEETS", "BELOW_EXPECTED", "MODERATE_CUT", "SEVERE_CUT"]] = Field(
        default=None,
        description="Evaluate the alignment between the job's offered salary and the candidate's compensation expectations. "
        "If Expected Salary is missing/negotiable, benchmark primarily against Current CTC. "
        "EXCEEDS - Job salary is more than the candidate's Current CTC or Expected Salary (whichever is higher). "
        "MEETS - If Expected < CTC, job salary is between Expected and CTC (inclusive). If Expected is missing or equal, job salary matches CTC. "
        "BELOW_EXPECTED - Only used if Expected > CTC, and the job salary falls strictly between the two. (This is still a raise from CTC). "
        "MODERATE_CUT - Job salary is less than both Expected and CTC. If Expected is present, up to a 30% drop from Expected. If Expected is missing, up to a 40% drop from CTC. "
        "SEVERE_CUT - Job salary drop is worse than MODERATE_CUT. "
        "If the job salary is missing, undisclosed, or just a buzzword (e.g. 'Competitive', 'DOE'), output null."
    )
    
    role_relevance: Optional[Literal["UNKNOWN", "CORE", "ADJACENT", "STRETCH", "UNRELATED"]] = Field(
        default=None,
        description="How relevant this job's function is to the candidate's career trajectory. "
        "CRITICAL RULE: Evaluate the core function, NOT the domain or secondary tech. If the core titles match (e.g., Python Developer -> Python Developer), it MUST be CORE, even if the new role is in a different domain (like Networking or Finance). "
        "CORE - The job's primary function perfectly matches the candidate's current/past roles "
        "(e.g., Backend Developer -> Backend Developer, RN -> RN). "
        "ADJACENT - A fundamentally different function, but within the same core discipline "
        "(e.g., Backend Developer -> Data Engineer, or ER Nurse -> ICU Nurse). "
        "STRETCH - A significant career pivot into a different discipline, but a realistic/common transition path "
        "(e.g., Backend Developer -> Product Management, or Individual Contributor -> Management). "
        "UNRELATED - A completely different field with no realistic professional bridge "
        "(e.g., Backend Developer -> HR/Sales, or Nurse -> Financial Accountant). "
        "Output UNKNOWN only if the job description is completely missing functional details."
    )
    
    # Free-text output
    reasoning: str = Field(
        description="2-3 sentence assessment covering: the single biggest strength, "
        "the single biggest concern, and whether this is worth the candidate's time to apply."
    )


    @staticmethod
    def build_applicant_context(profile: ApplicantProfile) -> str:
        context = f"CANDIDATE: {profile.full_name}\n"
        if profile.designation:
            context += f"CURRENT DESIGNATION: {profile.designation}\n"
        if profile.summary:
            context += f"SUMMARY: {profile.summary}\n"
        context += f"YEARS OF EXPERIENCE: {profile.years_of_experience}\n"
        context += f"CORE SKILLS: {json.dumps(profile.core_skills)}\n"
        
        # Compensation
        current_ctc = profile.current_salary_fixed + profile.current_salary_variable
        if current_ctc > 0:
            context += f"CURRENT CTC: {current_ctc} {profile.currency}\n"
        
        expected = profile.expected_salary
        if expected > 0:
            context += f"EXPECTED SALARY: {expected} {profile.currency}\n\n"
        else:
            context += "EXPECTED SALARY: Undisclosed / Negotiable\n\n"
        
        context += "WORK EXPERIENCE:\n"
        for exp in profile.experiences.all().order_by('-start_date'):
            end = exp.end_date if not exp.is_current else "Present"
            context += f"- {exp.job_title} at {exp.company_name} ({exp.start_date} to {end})\n"
            context += f"  Skills: {json.dumps(exp.tech_stacks)}\n"
            for bp in exp.bullet_points.all():
                context += f"  * {bp.content}\n"
            
        context += "\nPROJECTS:\n"
        for proj in profile.projects.all():
            context += f"- {proj.name}: {proj.description}\n"
            context += f"  Skills: {json.dumps(proj.tech_stacks)}\n"
            for bp in proj.bullet_points.all():
                context += f"  * {bp.content}\n"
            
        context += "\nEDUCATION:\n"
        for edu in profile.education.all():
            context += f"- {edu.degree} from {edu.institution}\n"
            
        return context

    @staticmethod
    def build_job_context(job: Job) -> str:
        context = f"COMPANY: {job.company}\n"
        if job.ratings:
            context += f"RATINGS & REVIEWS: {job.ratings}\n"
        context += f"POSITION: {job.position}\n"
        context += f"LOCATION: {job.job_location}\n"
        # Only include salary if actually disclosed
        if job.salary and job.salary.lower() not in ('not disclosed', 'none', '', 'not mentioned'):
            context += f"SALARY: {job.salary}\n"
        # Only include experience if actually disclosed
        if job.experience_min:
            context += f"EXPERIENCE REQUIRED: {job.experience_min} to {job.experience_max or '?'} years\n"
        context += "\n"
        
        context += "--- AI ENRICHED DATA ---\n"
        context += f"ROLE TITLE: {job.role_title}\n"
        if job.role_category:
            context += f"ROLE CATEGORY: {job.role_category}\n"
        context += f"SENIORITY: {job.seniority_level}\n"
        context += f"REQUIRED SKILLS: {job.tech_stack_primary_new}\n"
        context += f"PREFERRED SKILLS: {job.tech_stack_all_new}\n\n"
        
        context += "--- FULL JOB DESCRIPTION ---\n"
        context += f"{job.job_description}\n"
        return context

    @staticmethod
    def calculate_score(result: 'JobMatchResult') -> tuple[int, str]:
        """Calculate final score and decision from LLM output using Harmonic Gate architecture."""
        
        # ── 1. Base Tech Score (0-100) ──
        COVERAGE_SCORES = {
            "EXACT_MATCH": 100.0,
            "COVERED": 90.0,
            "BUILDABLE": 75.0,
            "PARTIAL": 60.0,
            "GAP": 35.0,
            "WRONG_STACK": 0.0
        }
        
        core_score = COVERAGE_SCORES.get(result.core_foundation.coverage, 50.0)
        
        secondary_score = None
        if result.secondary_clusters:
            sec_tech_score = 0
            max_possible = 0
            for cluster in result.secondary_clusters:
                weight = 5 if getattr(cluster, "importance", "") == "NICE_TO_HAVE" else 20
                sec_tech_score += weight * COVERAGE_SCORES.get(cluster.coverage, 50.0)
                max_possible += weight
            secondary_score = sec_tech_score / max(max_possible, 1)

        if secondary_score is not None:
            base_tech = (core_score * 0.70) + (secondary_score * 0.30)
        else:
            base_tech = core_score
            
        # ── 2. Logistical Multipliers ──
        ROLE_MULTIPLIERS = {"CORE": 1.0, "ADJACENT": 0.80, "STRETCH": 0.50, "UNRELATED": 0.05}
        role_mult = ROLE_MULTIPLIERS.get(result.role_relevance) if result.role_relevance else None
        
        SALARY_MULTIPLIERS = {
            "EXCEEDS": 1.10,
            "BELOW_EXPECTED": 1.0,
            "MEETS": 1.0,
            "MODERATE_CUT": 0.60,
            "SEVERE_CUT": 0.05
        }
        salary_mult = SALARY_MULTIPLIERS.get(result.salary_alignment) if result.salary_alignment else None
        
        LEVEL_MAP = {
            "INTERN": 0, "JUNIOR": 1, "MID": 2, "SENIOR": 3, 
            "LEAD": 4, "MANAGER": 5, "DIRECTOR": 6, "VP": 7, "CXO": 8
        }
        
        c_lvl = LEVEL_MAP.get(result.candidate_level) if result.candidate_level else None
        j_lvl = LEVEL_MAP.get(result.job_level) if result.job_level else None
        
        if c_lvl is None or j_lvl is None:
            diff = None
        else:
            diff = j_lvl - c_lvl

        if diff is None:
            sen_mult = None
        elif diff == 0:
            sen_mult = 1.0
        elif diff == 1:
            sen_mult = 0.90
        elif diff == -1 or diff == 2:
            sen_mult = 0.50
        elif diff >= 3 or diff <= -2:
            sen_mult = 0.05
        else:
            sen_mult = None # fallback to neutral ignore

        # ── 3. Harmonic Gate (Weighted) ──
        # Weights: Role=10, Seniority=8, Salary=2
        components = [
            (role_mult, 10.0),
            (sen_mult, 8.0),
            (salary_mult, 2.0)
        ]
        
        valid_components = [(m, w) for m, w in components if m is not None]
        
        if valid_components:
            # Check for zero to avoid division by zero just in case
            if any(m <= 0 for m, w in valid_components):
                harmonic_gate = 0.0
            else:
                total_weight = sum(w for m, w in valid_components)
                sum_weighted_reciprocals = sum(w / m for m, w in valid_components)
                harmonic_gate = total_weight / sum_weighted_reciprocals
        else:
            harmonic_gate = 1.0
            
        # ── 4. Final Score ──
        score = base_tech * harmonic_gate
        score = max(0, min(100, int(score)))
        
        if score >= 70:
            decision = "APPLY"
        elif score >= 45:
            decision = "MANUAL_REVIEW"
        else:
            decision = "REJECT"
            
        return score, decision

    @classmethod
    def run_match(cls, client: 'OllamaClient', profile: ApplicantProfile, job: Job, method: str):
        applicant_context = cls.build_applicant_context(profile)
        job_context = cls.build_job_context(job)
        
        prompt = f"""You are evaluating a job posting against a candidate profile.

Your task:
1. Identify the core, non-transferable technical foundation of the role (e.g. Primary Languages/Frameworks). If multiple are strictly required, group them all here.
2. Group the remaining technical requirements into logical secondary technology clusters.
3. For each cluster, determine its importance to THIS specific job and how well the candidate covers it.
4. Assess whether the role is relevant to the candidate's career.
5. Calculate the hierarchical level gap (level_difference) between the candidate's seniority and the job's required seniority.
6. Evaluate the alignment between the job's offered salary and the candidate's compensation expectations.

Guidelines:
- If the candidate has the EXACT skill listed, use EXACT_MATCH
- A candidate who knows AWS can cover GCP requirements (COVERED — direct equivalent, not EXACT_MATCH)
- A candidate who knows Celery/SQS understands message queue concepts (BUILDABLE for Kafka/RabbitMQ)
- A candidate who knows Docker but not Kubernetes = PARTIAL (has some cluster skills but missing key ones)
- A candidate who knows Python/Django but the job needs Java/Spring Boot = WRONG_STACK for that cluster
- The IMPORTANCE of a cluster depends on THIS job: a "Kafka Engineer" role → Message Queues is CRITICAL; a "Python Backend Dev" role → Message Queues might be IMPORTANT or NICE_TO_HAVE

CANDIDATE PROFILE:
{applicant_context}

JOB POSTING:
{job_context}
"""
        
        # Change method to "raw_generate" to test Ollama's native endpoint speed
        llm_result, raw_response = client.call_model_structured(
            prompt=prompt, 
            response_model=cls, 
            method=method
        )

        # print("\n" + "="*60)
        # print("INSTRUCTOR JSON SCHEMA (INJECTED INTO REQUEST):")
        # print("="*60)
        # import json
        # print(json.dumps(cls.model_json_schema(), indent=2))
        
        # print("\n" + "="*60)
        # print("RAW RESPONSE FROM OLLAMA (BEFORE PARSING):")
        # print("="*60)
        # print(raw_response.model_dump_json(indent=2))
        # print("="*60 + "\n")

        # Compute deterministic score
        score, decision = cls.calculate_score(llm_result)
        
        contexts = {
            "applicant_context": applicant_context,
            "job_context": job_context,
            "prompt": prompt
        }
        
        return llm_result, score, decision, contexts
