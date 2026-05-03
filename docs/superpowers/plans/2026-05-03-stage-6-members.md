# Stage 6 — Members & Roles — Implementation Plan

> Inline execution. Spec: `docs/superpowers/specs/2026-05-03-stage-6-members-design.md`. Branch: `stage-6-members`.

**Goal:** server_members + server_invites tables; email-bound invite/accept; RBAC on /servers/{id}/* endpoints (viewer/operator/owner).

---

## Groupings

### Group 1: enum + migration `0004` + ORM models
- `gamehost_shared.enums.ServerRole` (OWNER/OPERATOR/VIEWER).
- `db/models/server_member.py`, `db/models/server_invite.py`, register in `__init__`.
- Migration `0004_members_invites`: both tables + partial unique index.
- conftest TRUNCATE list: prepend `server_invites, server_members`.

### Group 2: repositories + domain exceptions
- New `ServerMembersRepository`, `ServerInvitesRepository`.
- `domain/exceptions.py`: `MemberAlreadyExists` (409), `InviteAlreadyExists` (409), `InviteNotFound` (404), `InviteExpired` (410 → 401), `InviteEmailMismatch` (403), `NotServerMember` (404).
- `ServersRepository.list_for_user_or_member(user_id)` — UNION query.

### Group 3: access helper + require_server_role dep + servers RBAC
- `domain/access.py:get_server_role_for(session, server_id, user) -> "owner"|"operator"|"viewer"|None` (+ admin → "owner").
- `api/v1/deps.py:require_server_role(min_role)` factory.
- Apply dep to start/stop/restart (operator), patch/delete (owner), get/logs/* (viewer).
- Update `ServerService.list_for` to use new repo helper for members.

### Group 4: MemberService + schemas/members.py
- DTOs.
- `MemberService` with invite/accept/preview/revoke/remove/list.
- audit_log entries.

### Group 5: routes (members + invites)
- `api/v1/members.py`, `api/v1/invites.py`, wire into v1 router.

### Group 6: tests + README + push
- repos / service / routes / RBAC matrix tests.
- README "Stage 6" section.

---

## Self-review

- Spec coverage: 0004 migration G1, repos G2, RBAC dep + applied G3, MemberService + DTOs G4, routes G5, tests G6.
- Email type reused from `schemas/auth.py` (no duplication).
- Coverage gates kept ≥70% global, ≥85% domain.
