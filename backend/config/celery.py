from celery import Celery

app = Celery("millennium")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
