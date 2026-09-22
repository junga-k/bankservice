"""인증 유틸: bcrypt 비밀번호 해싱 + JWT 발급/검증.

SECRET은 환경변수 JWT_SECRET에서 읽는다. 저장소가 공개되어 있으므로 상수로 두면
누구나 role:"admin" 토큰을 위조할 수 있다(= 관리자 비밀번호를 비공개로 돌려도 무의미).
미설정 시에는 로컬 개발용 폴백 상수를 쓴다 — 배포 환경에서는 반드시 넣어야 한다.

토큰은 프런트 localStorage에 저장(데모 편의). httpOnly 쿠키가 아니라 XSS에
노출될 수 있으므로 실서비스에서는 개선 대상이다.
"""
from __future__ import annotations

import os
import time

import bcrypt
import jwt
from fastapi import Header, HTTPException

from backend import db

# 배포: Vercel 환경변수 JWT_SECRET. 미설정 시 폴백(로컬 개발 전용).
# 값을 바꾸면 기존 발급 토큰이 전부 무효화되어 사용자는 로그인 화면으로 떨어진다.
_SECRET = os.environ.get("JWT_SECRET", "").strip() or "demo-secret-change-me"
_ALGO = "HS256"
_TTL = 60 * 60 * 24  # 24시간


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def make_token(user: dict) -> str:
    payload = {
        "sub": user["username"],
        "name": user.get("name", ""),
        "role": user.get("role", "user"),
        "exp": int(time.time()) + _TTL,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _SECRET, algorithms=[_ALGO])


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """Authorization: Bearer <token> 헤더에서 사용자 정보를 얻는다."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    # 최신 상태 확인(role 변경 등 반영)
    user = db.get_user_by_username(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    if not user.get("is_active", 1):
        raise HTTPException(status_code=401, detail="탈퇴한 계정입니다.")
    return {"id": user["id"], "username": user["username"], "name": user["name"], "role": user["role"]}


def require_admin(authorization: str | None = Header(default=None)) -> dict:
    user = get_current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
