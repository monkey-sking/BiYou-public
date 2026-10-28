import requests


class DouyinApiClient:
    API_ROOT = "https://open.douyin.com"

    def __init__(self, access_token: str, session: requests.Session | None = None):
        self.access_token = access_token
        self.session = session or requests.Session()

    def upload_video(self, video_path: str) -> dict:
        with open(video_path, "rb") as fh:
            response = self.session.post(
                f"{self.API_ROOT}/video/create/upload/",
                headers={"access-token": self.access_token},
                files={"video": fh},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()["data"]["video"]

    def create_video(self, *, video_id: str, text: str) -> dict:
        response = self.session.post(
            f"{self.API_ROOT}/video/create/",
            headers={
                "access-token": self.access_token,
                "Content-Type": "application/json",
            },
            json={"video_id": video_id, "text": text},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["data"]
