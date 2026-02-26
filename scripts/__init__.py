import os

import django

if __name__ == 'scripts':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JobTracker.settings')

    django.setup()

