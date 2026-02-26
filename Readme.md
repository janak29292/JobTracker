# JobTracker

A Django-based application for tracking jobs and related data structures & algorithms (DSA) preparation.

## Prerequisites
- Python 3.12 or higher (recommended)
- Redis Server (for Celery tasks, if applicable)

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
Install all the required Python packages from `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 4. Setting up Environment Variables in `.bashrc` (Optional but Recommended)
If you want to set environment variables permanently or configure aliases to make running the project easier, you can add them to your `~/.bashrc` (or `~/.zshrc` on macOS).

Open your `.bashrc` file:
```bash
nano ~/.bashrc
```

Add the following lines at the bottom (adjust paths according to your actual setup):
```bash
# LinkedIn Parser Variables
export LINKEDIN_USERNAME="your-linkedin-email@example.com"
export LINKEDIN_PASSWORD="your-linkedin-password"

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
The project includes several custom Django management commands to interact with the job tracking pipelines. These commands integrate with Celery to queue scraping and parsing tasks.

**1. Scan URLs (`scan_urls.py`)**  
Scans a job source for new URLs and adds them to the database.
```bash
# Scan LinkedIn recommended jobs
python manage.py scan_urls -s linkedin -t recommended

# Scan LinkedIn filtered jobs
python manage.py scan_urls -s linkedin -t filtered
```

**2. Scrape Jobs (`scrape_jobs.py`)**  
Takes all un-scraped URLs from the database and queues Celery tasks to scrape their raw data.
```bash
python manage.py scrape_jobs
```

**3. Parse Jobs (`parse_jobs.py`)**  
Takes all scraped raw job data and parses them into usable formats.
```bash
python manage.py parse_jobs
```

**4. Full Pipeline (`job_pipeline.py`)**  
Runs the full pipeline (scan -> scrape -> parse) for a specific source and type.
```bash
python manage.py job_pipeline -s linkedin -t recommended
python manage.py job_pipeline -s linkedin -t filtered
```
