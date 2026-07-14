from worker import app

@app.task
def example_task():
    return 'Task complete'