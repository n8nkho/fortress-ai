"""WSGI entrypoint for gunicorn/uwsgi: gunicorn -b 0.0.0.0:8050 wsgi:application"""

from dashboard.ai_command_center import app as application
