from celery import Celery

app = Celery(
    'info_os_worker',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0',
    include=['tasks']
)

if __name__ == '__main__':
    app.start()