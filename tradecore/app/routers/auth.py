"""Auth endpoints — register, login, refresh, logout, me, email/password reset, Google OAuth."""
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.rate_limit import limiter
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)
from app.services import auth_service, google_oauth

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_context(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def register(request: Request, payload: RegisterRequest, db: DBSession):
    user = await auth_service.register_user(
        db, payload.email, payload.password, payload.full_name
    )
    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/15minutes")
async def login(request: Request, payload: LoginRequest, db: DBSession):
    user = await auth_service.authenticate(db, payload.email, payload.password)
    ip, ua = _client_context(request)
    access, refresh, ttl = await auth_service.issue_tokens(db, user.id, ip, ua)
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("60/minute")
async def refresh(request: Request, payload: RefreshRequest, db: DBSession):
    ip, ua = _client_context(request)
    access, new_refresh, ttl = await auth_service.rotate_refresh_token(
        db, payload.refresh_token, ip, ua
    )
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=new_refresh, expires_in=ttl)


@router.post("/logout", response_model=MessageResponse)
async def logout(payload: RefreshRequest, db: DBSession, user: CurrentUser):
    await auth_service.invalidate_refresh_token(db, payload.refresh_token)
    await db.commit()
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return user


@router.post("/verify-email", response_model=UserResponse)
@limiter.limit("20/hour")
async def verify_email(request: Request, payload: VerifyEmailRequest, db: DBSession):
    return await auth_service.verify_email(db, payload.token)


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("5/hour")
async def forgot_password(request: Request, payload: ForgotPasswordRequest, db: DBSession):
    await auth_service.request_password_reset(db, payload.email)
    # Always 200 to prevent email enumeration
    return MessageResponse(message="If that email exists, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/hour")
async def reset_password(request: Request, payload: ResetPasswordRequest, db: DBSession):
    await auth_service.reset_password(db, payload.token, payload.new_password)
    return MessageResponse(message="Password updated")


# ---------- Google OAuth ----------

@router.get("/google")
async def google_login():
    state = secrets.token_urlsafe(24)
    url = google_oauth.build_authorization_url(state)
    response = RedirectResponse(url=url)
    # Store state in a short-lived signed cookie (httponly). 5 min.
    response.set_cookie(
        "oauth_state", state, max_age=300, httponly=True,
        secure=settings.is_production, samesite="lax",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: DBSession,
    code: str = Query(...),
    state: str = Query(...),
):
    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        raise AppError(400, "Invalid OAuth state", "OAUTH_STATE_MISMATCH")

    userinfo = await google_oauth.exchange_code_for_userinfo(code)
    user = await google_oauth.find_or_create_google_user(db, userinfo)

    ip, ua = _client_context(request)
    access, refresh, ttl = await auth_service.issue_tokens(db, user.id, ip, ua)
    await db.commit()

    # Redirect back to frontend with tokens in URL fragment (not logged server-side).
    params = urlencode({
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": ttl,
    })
    frontend = settings.frontend_url.rstrip("/")
    resp = RedirectResponse(url=f"{frontend}/auth/callback#{params}")
    resp.delete_cookie("oauth_state")
    return resp
