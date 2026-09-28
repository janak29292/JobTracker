# JobTracker

A Django-based application for tracking jobs and related data structures & algorithms (DSA) preparation.

## System Requirements & Prerequisites

**Hardware Requirements**
- **RAM:** Minimum 16GB (32GB recommended when running local LLMs and concurrent browser scraping tasks).
- **Storage:** ~10GB free space (needed for Python packages, Playwright binaries, and Ollama model weights).
- **GPU (Recommended):** A dedicated GPU (e.g., Nvidia RTX) is highly recommended for running Ollama (Gemma 4). Running the LLM solely on CPU will be extremely slow.

**Software Prerequisites**
- Python 3.12 or higher (recommended)
- Redis Server (required for Celery task queuing)

## Setup Instructions

### 1. Clone the repository
Navigate to your desired directory and clone the project:
```bash
# Clone the repository
git clone <repository-url>
cd JobTracker
```

### 2. Create and Activate a Virtual Environment
It's recommended to use a virtual environment to isolate project dependencies.
```bash
# Create a virtual environment
python -m venv venv

# Activate it
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Linux/macOS:
source venv/bin/activate
```

### 3. Install Dependencies

**Python Packages**  
Install all the required Python packages from `requirements.txt`:
```bash
pip install -r requirements.txt
```

**Playwright Browsers**  
This project uses Playwright for scraping. You need to install the browser binaries:
```bash
playwright install
```

**Ollama (for AI Features)**  
To use the AI-powered extraction and job matching features, you need to install [Ollama](https://ollama.com/) locally and pull the required model (currently configured to use `gemma4`):
```bash
# After installing Ollama from their website, run:
ollama pull gemma4
```

### 4. Setting up Environment Variables in `.bashrc` (Optional but Recommended)
If you want to set environment variables permanently or configure aliases to make running the project easier, you can add them to your `~/.bashrc` (or `~/.zshrc` on macOS).

Open your `.bashrc` file:
```bash
nano ~/.bashrc
```

Add the following lines at the bottom (adjust paths according to your actual setup):
```bash
# LinkedIn Credentials
export LINKEDIN_USERNAME="your-linkedin-email@example.com"
export LINKEDIN_PASSWORD="your-linkedin-password"

# Naukri Credentials
export NAUKRI_USERNAME="your-naukri-email@example.com"
export NAUKRI_PASSWORD="your-naukri-password"

# Optional alias to quickly navigate to the project and activate venv
alias jobtracker="cd /path/to/JobTracker && source venv/bin/activate"
```

Save and exit, then reload your `.bashrc`:
```bash
source ~/.bashrc
```

### 5. Run Database Migrations
Apply the initial database migrations to set up the SQLite databases (`db.sqlite3` and `db2.sqlite3`):
```bash
python manage.py migrate
python manage.py migrate --database=job
```

### 6. Populate the Database (Initial DSA Data)
To populate the database with the initial Data Structures and Algorithms questions, run the `populate_dsa.py` script from the root directory:
```bash
python populate_dsa.py
```
This script will clear any existing DSA data and repopulate the `Category`, `Pattern`, `Problem`, and `Approach` tables with the predefined data.

### 7. Run the Development Server
Start the Django development server:
```bash
python manage.py runserver
```
The application will be accessible at [http://localhost:8000](http://localhost:8000).

### 8. Run Celery Worker
This project uses Celery (with Redis as a broker) for asynchronous tasks like the LinkedIn parser. To run the celery worker:
```bash
# Make sure your Redis server is running first!
celery -A JobTracker worker -l info
```

### 9. Run Custom Management Commands
The project includes several custom Django management commands to interact with the job tracking pipelines. These commands integrate with Celery to queue scraping and parsing tasks. All commands support a `--sync` flag to run synchronously without Celery.

**1. Scan URLs (`scan_urls`)**  
Scans a job source for new URLs and adds them to the database. Supports scanning multiple sources at once.
```bash
# Scan LinkedIn (requires -t flag)
python manage.py scan_urls -s linkedin -t recommended
python manage.py scan_urls -s linkedin -t filtered

# Scan Naukri
python manage.py scan_urls -s naukri

# Multiple sources
python manage.py scan_urls -s linkedin naukri -t filtered

# Run synchronously (without Celery)
python manage.py scan_urls -s naukri --sync
```

**2. Scrape Jobs (`scrape_jobs`)**  
Takes all un-scraped URLs from the database and queues Celery tasks to scrape their raw data.
```bash
python manage.py scrape_jobs
python manage.py scrape_jobs --sync
```

**3. Parse Jobs (`parse_jobs`)**  
Takes all scraped raw job data and parses them into usable formats using the `AdvancedJobParser` (spaCy-powered NLP) to extract tech stack, experience, education, salary, and job type.
```bash
python manage.py parse_jobs
python manage.py parse_jobs --sync
```

**4. Full Pipeline (`job_pipeline`)**  
Runs the full pipeline (scan → scrape → parse) for a specific source and type. Can optionally run the AI extraction and auto-curation stages. The `-s` flag is optional — if not provided, the pipeline skips the scan step and only runs scrape and parse on existing un-processed data.

Available flags:
- `--sync`: Run synchronously instead of dispatching to Celery.
- `--ai`: Include the AI extraction and auto-curation tasks (requires GPU/Ollama).
- `--purge`: Purge the Celery queue of any unacknowledged tasks before starting.
- `--shutdown`: Shut down the computer after the pipeline finishes.

```bash
# Standard pipeline (scan -> scrape -> parse)
python manage.py job_pipeline -s linkedin -t recommended
python manage.py job_pipeline -s naukri

# Run with AI extraction and curation
python manage.py job_pipeline -s naukri --ai

# Purge queue before running, then shutdown when finished
python manage.py job_pipeline -s linkedin naukri -t filtered --ai --purge --shutdown

# Scrape + parse only (no scan)
python manage.py job_pipeline

# Run synchronously
python manage.py job_pipeline -s naukri --sync
```

**5. Extract JD Fields (`extract_jd_fields`)**  
Extracts structured fields from job descriptions (like role summary, seniority level, required skills) using the local Ollama LLM and stores them in a buffer for review before applying to the database.
```bash
# Extract in batches (writes to buffer)
python manage.py extract_jd_fields --batch-size 50 --resume

# Run synchronously
python manage.py extract_jd_fields --batch-size 50 --resume --sync
```

**6. Match Jobs (`match_jobs`)**  
Runs the Ollama-based AI job matching pipeline to qualify jobs against your applicant profile and dealbreakers. This scores and filters jobs based on how well they fit your preferences.
```bash
# Evaluate a batch of jobs
python manage.py match_jobs --batch-size 10

# Test specific jobs
python manage.py match_jobs --job-ids 1,2,3

# Run synchronously
python manage.py match_jobs --batch-size 10 --sync
```
