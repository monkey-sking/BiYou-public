import requests


class KuaishouApiClient:
    API_ROOT = "https://open.kuaishou.com"

    def __init__(self, access_token: str, app_id: str, session: requests.Session | None = None):
        self.access_token = access_token
        self.app_id = app_id
        self.session = session or requests.Session()

    def start_upload(self) -> dict:
        response = self.session.post(
            f"{self.API_ROOT}/openapi/photo/start_upload",
            params={"access_token": self.access_token, "app_id": self.app_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "upload_token": payload["upload_token"],
            "endpoint": payload["endpoint"],
        }

    def publish_photo(
        self,
        *,
        caption: str,
        video_path: str,
        cover_path: str,
        upload_token: str,
    ) -> dict:
        with open(video_path, "rb") as video_fh, open(cover_path, "rb") as cover_fh:
            response = self.session.post(
                f"{self.API_ROOT}/openapi/photo/publish",
                params={
                    "access_token": self.access_token,
                    "app_id": self.app_id,
                    "upload_token": upload_token,
                },
                files={"video": video_fh, "cover": cover_fh},
                data={"caption": caption},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()
