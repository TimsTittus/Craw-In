from locust import HttpUser, task, between
import json

class CrawInUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def query_timstittus(self):
        payload = {"action": "run_agent", "prompt": "Find the top project on https://example.com"}
        with self.client.websocket_connect("/ws/tasks/loadtest") as ws:
            ws.send(json.dumps(payload))
            for message in ws.iter_text():
                data = json.loads(message)
                if data.get("type") == "result":
                    break