import webbrowser
import sys
import os

# Add the parent directory to sys.path so python can find the 'JobTracker' module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set the DJANGO_SETTINGS_MODULE to your project's settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "JobTracker.settings")
import django
django.setup()

from pydantic import BaseModel, Field
from services.ollama_client import OllamaClient
from job.models import Job
from services.parser import Parser


class TestStructuredOutput(BaseModel):
    message: str = Field(description="A friendly greeting or response message")
    sentiment: str = Field(description="Sentiment of the message: positive, neutral, or negative")
    keywords: list[str] = Field(description="List of 2-3 key words or topics mentioned")


def open_in_browser(url):
    webbrowser.open(url)


def get_browser():
    var = webbrowser.get()
    breakpoint()


def run(login_url=None, username=None, password=None):
    client = OllamaClient()

    print("=== 1. Testing Raw Generate Call ===")
    response = client.generate("Hello, how is the weather today?")
    client.progress_log(1, 1, client.last_raw_payload)
    client.final_log(client.last_raw_response)
    print("----------------------------------")

    print("=== 2. Testing Instructor Structured Call ===")
    prompt = "Hello, how is the weather today?"
    result, raw_resp = client.call_model_structured(
        prompt=prompt,
        response_model=TestStructuredOutput,
        method="instructor"
    )
    client.progress_log(1, 1, prompt)
    client.final_log(raw_resp)
    print("\nInstructor Result (Pydantic model):", result)
    print("\nParsed JSON / Dict:", result.model_dump())
    print("----------------------------------")
    return result, response

    # cookies = []
    # jobs = [
    #     Job.objects.filter(apply_url__icontains='naukri').first().apply_url,
    #     Job.objects.filter(apply_url__icontains='linkedin').first().apply_url
    # ]
    # parser = Parser()
    # try:
    #     for job in jobs:
    #         print(f"{parser.is_expired(job)}: {job}")
    #     # cookies = parser.login(login_url, username, password)
    #     parser.playwright.stop()
    # except Exception as e:
    #     print(e)
    #     parser.playwright.stop()
    # return cookies

if __name__ == '__main__':
    run(None, None, None)