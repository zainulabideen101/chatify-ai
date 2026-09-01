# Custom JWT auth testing playbook

- Credentials: read `/app/memory/test_credentials.md`
- Login: `POST /api/auth/login`
- Session: `GET /api/auth/me`
- Backend service: supervisor-managed FastAPI on port 8001
- Frontend API base: `REACT_APP_BACKEND_URL` plus `/api`
- Verify auth with the Bearer token returned by login and with httpOnly cookies.
