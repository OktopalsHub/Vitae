from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_users.exceptions import UserAlreadyExists

from app.auth import (
    UserCreate,
    auth_backend,
    current_active_user,
    get_jwt_strategy,
    get_user_manager,
    optional_current_user,
)
from app.config import project_path
from app.models import User
from app.web_helpers import (
    redirect_with_auth_cookie,
    safe_next_path,
    template_ctx,
)

router = APIRouter(tags=["auth-pages"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: User | None = Depends(optional_current_user),
):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        template_ctx(
            request,
            None,
            error=request.query_params.get("error", ""),
            next=safe_next_path(request.query_params.get("next"), "/"),
        ),
    )


@router.post("/login")
async def login_form(
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    user_manager=Depends(get_user_manager),
):
    class _Creds:
        def __init__(self, username: str, password: str):
            self.username = username
            self.password = password

    dest = safe_next_path(next, "/")
    user = await user_manager.authenticate(_Creds(email.strip().lower(), password))
    if user is None or not user.is_active:
        return RedirectResponse(
            url=f"/login?error={quote('Invalid email or password')}&next={quote(dest)}",
            status_code=303,
        )
    login_response = await auth_backend.login(get_jwt_strategy(), user)
    return redirect_with_auth_cookie(dest, login_response)


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    user: User | None = Depends(optional_current_user),
):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "register.html",
        template_ctx(request, None, error=request.query_params.get("error", "")),
    )


@router.post("/register")
async def register_form(
    email: str = Form(...),
    password: str = Form(...),
    user_manager=Depends(get_user_manager),
):
    try:
        user = await user_manager.create(
            UserCreate(email=email.strip().lower(), password=password, full_name=""),
            safe=True,
        )
    except UserAlreadyExists:
        return RedirectResponse(
            url=(
                "/login?error="
                + quote(
                    "That email is already registered. Sign in with password, Google, or GitHub."
                )
            ),
            status_code=303,
        )
    login_response = await auth_backend.login(get_jwt_strategy(), user)
    return redirect_with_auth_cookie("/onboarding", login_response)


@router.post("/logout")
async def logout_form(request: Request, user: User = Depends(current_active_user)):
    token = request.cookies.get("jobmatch_auth") or ""
    logout_response = await auth_backend.logout(get_jwt_strategy(), user, token)
    return redirect_with_auth_cookie("/login", logout_response)
