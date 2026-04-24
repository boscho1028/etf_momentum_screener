"""KIS Open API OAuth 토큰 발급/갱신/캐싱."""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}

_TOKEN_CACHE_DIR = Path(os.getenv("KIS_TOKEN_CACHE_DIR", str(Path(__file__).parent.parent.parent / "tokens")))
_TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class KisAuth:
    """KIS Access Token 관리.

    토큰을 파일로 캐싱하여 1일간 재사용합니다.
    만료 30분 전에 자동 갱신합니다.

    Args:
        mode: "paper"(모의투자) 또는 "real"(실전투자).
    """

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode or os.getenv("KIS_MODE", "paper")
        if self.mode not in ("paper", "real"):
            raise ValueError(f"mode must be 'paper' or 'real', got: {self.mode}")

        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")

        if not self.app_key or not self.app_secret:
            raise EnvironmentError("KIS_APP_KEY / KIS_APP_SECRET not set in .env")

        self.base_url = _BASE_URLS[self.mode]
        self._cache_path = _TOKEN_CACHE_DIR / f"token_{self.mode}.json"
        self._token: str | None = None
        self._expires_at: datetime | None = None

    @property
    def token(self) -> str:
        """유효한 Access Token 반환. 만료 임박 시 자동 갱신."""
        if self._is_valid():
            return self._token  # type: ignore[return-value]
        self._load_or_issue()
        return self._token  # type: ignore[return-value]

    def _is_valid(self) -> bool:
        if self._token is None or self._expires_at is None:
            return False
        return datetime.now() < self._expires_at - timedelta(minutes=30)

    def _load_or_issue(self) -> None:
        if self._load_from_cache():
            return
        self._issue_token()

    def _load_from_cache(self) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() < expires_at - timedelta(minutes=30):
                self._token = data["access_token"]
                self._expires_at = expires_at
                logger.debug("토큰 캐시 로드 성공 (만료: %s)", expires_at)
                return True
        except Exception as e:
            logger.warning("토큰 캐시 읽기 실패: %s", e)
        return False

    def _issue_token(self) -> None:
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._expires_at = datetime.now() + timedelta(seconds=expires_in)

        cache_data = {
            "access_token": self._token,
            "expires_at": self._expires_at.isoformat(),
            "mode": self.mode,
        }
        try:
            self._cache_path.write_text(
                json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("토큰 캐시 저장 실패 (무시하고 계속): %s", e)
        logger.info("KIS 토큰 발급 완료 (mode=%s, 만료: %s)", self.mode, self._expires_at)

    def get_headers(self, tr_id: str, extra: dict | None = None) -> dict[str, str]:
        """공통 요청 헤더 반환.

        Args:
            tr_id: KIS TR ID (예: "FHKST03010100").
            extra: 추가 헤더 항목.

        Returns:
            헤더 딕셔너리.
        """
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            headers.update(extra)
        return headers
