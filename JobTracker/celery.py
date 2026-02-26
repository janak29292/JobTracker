import os

from celery import Celery
from django.apps import apps

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JobTracker.settings')

app = Celery('JobTracker')

app.config_from_object('django.conf:settings', namespace='CELERY')

# app.autodiscover_tasks()
app.autodiscover_tasks(lambda: [n.name for n in apps.get_app_configs()])


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
