import webbrowser
import sys
import os
import django

# Add the parent directory to sys.path so python can find the 'JobTracker' module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set the DJANGO_SETTINGS_MODULE to your project's settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "JobTracker.settings")
django.setup()

from job.models import Job
from services.parser import Parser


def open_in_browser(url):
    webbrowser.open(url)


def get_browser():
    var = webbrowser.get()
    breakpoint()


def run(login_url, username, password):
    cookies = []
    jobs = [
        Job.objects.filter(apply_url__icontains='naukri').first().apply_url,
        Job.objects.filter(apply_url__icontains='linkedin').first().apply_url
    ]
    parser = Parser()
    try:
        for job in jobs:
            print(f"{parser.is_expired(job)}: {job}")
        # cookies = parser.login(login_url, username, password)
        parser.playwright.stop()
    except Exception as e:
        print(e)
        parser.playwright.stop()
    return cookies

if __name__ == '__main__':
    run(None, None, None)