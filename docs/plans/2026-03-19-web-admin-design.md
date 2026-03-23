# 2026-03-19 Web Admin Design

## Goal
Build a web admin console inside the current Python bot project.
The first version must:
- use Telegram Login for authentication
- reuse the existing bot permission model based on Telegram user id
- write to the same config storage as the Telegram bot
- apply changes immediately after save
- keep a light-blue visual theme
- keep double-column action buttons in both sidebar navigation and Telegram-style preview areas
- provide one unified rich media message editor

The first version does not include:
- membership pages
- clone bot launch pages
- runtime monitoring dashboards
- draft/publish workflow
- replacing Telegram group-side execution logic

## Chosen Approach
Recommended approach: a Python monolith web admin plus a lightweight SPA.

Why:
- the repo is currently Python-only and has no Node build chain
- the bot already has stable config storage and permission logic
- the first goal is configuration sync, not frontend infrastructure
- a lightweight SPA is enough for forms, previews, and dual-column navigation

Alternatives considered:
- server-rendered templates only: simpler backend but weaker interactive editing
- fully separated frontend app: better long-term DX, but much higher initial complexity

## Architecture
Add a web entrypoint in the current project and keep bot execution unchanged.

Backend areas:
- `api/web.py`: web HTTP entrypoint
- `bot/web/auth.py`: Telegram Login verification, session issue/read
- `bot/web/permissions.py`: reusable group/module permission checks
- `bot/web/service.py`: stable web-facing config read/write wrappers
- `bot/web/schemas.py`: request/response normalization and validation

Frontend areas:
- `web/index.html`
- `web/app.js`
- `web/theme.css`
- `web/components/*`

Existing storage remains the source of truth:
- `group:{group_id}`
- `group:{group_id}:targets`
- `group:{group_id}:auto_replies`
- `group:{group_id}:auto_delete`
- `group:{group_id}:auto_ban`
- `group:{group_id}:auto_mute`
- `group:{group_id}:auto_warn`
- `group:{group_id}:anti_spam`
- `user:{user_id}`

This keeps Telegram private-chat settings and web settings naturally synchronized.

## Auth And Permissions
Authentication uses Telegram Login only.

Flow:
1. User signs in with Telegram.
2. Backend verifies Telegram Login signature.
3. Backend issues a signed session cookie.
4. Every API request resolves `telegram_user_id` from session.
5. Backend checks group access using the same logic as the bot:
   - super admin
   - owner/admin rules
   - `admin_access.mode`
   - bot membership in the target group

The web UI must hide groups/modules the user cannot manage.
The backend must still re-check permissions for every write request.

## Layout And UX
Use a three-column layout while preserving double-column interaction semantics.

Left column:
- current user card
- current group selector
- 24 module entries in a two-column grid, in the same order as the bot main menu
- each tile shows a small state summary such as enabled/disabled, rule count, or default command

Middle column:
- module explanation card
- settings form and rule editors
- save/reset actions

Right column:
- Telegram-style live preview
- preview cards for bot private-chat menus and group-facing messages
- double-column inline button preview at all times

Visual direction:
- light-blue overall theme
- white cards with soft blue shadows
- semi-transparent light-blue button panels
- green/red/orange only as functional state colors

## Unified Rich Media Editor
All outgoing bot messages should use one shared editor model.

Editor fields:
- `text`
- `photo_file_id`
- `buttons`
- `parse_mode`
- `delete_after_sec`
- `preview_context`

Editor panels:
- text formatting and variable insertion
- media binding
- double-column button editor
- live Telegram preview

Mapping rules:
- welcome message -> `welcome_*`
- verify message -> `verify_messages[mode]`
- auto reply rules -> per-rule message payload
- other text-only notices reuse the same editor shell with reduced capabilities

## Save And Sync Rules
Save is immediate.

Rules:
- Web reads the latest storage on open.
- Save writes back to the current storage keys immediately.
- Telegram private chat later reads the same updated values.
- No separate event bus in v1.
- Save scope is module-level, not whole-group overwrite.

## API Shape
Initial endpoints:
- `GET /api/web/me`
- `GET /api/web/groups/{group_id}/summary`
- `GET /api/web/groups/{group_id}/module/{module_key}`
- `POST /api/web/groups/{group_id}/module/{module_key}`
- `POST /api/web/render-preview`

Error categories:
- unauthorized
- forbidden
- bot_not_in_group
- validation_error
- storage_error
- limit_exceeded

## Delivery Order
1. Write design doc.
2. Add Telegram Login and session handling.
3. Add group summary and permission APIs.
4. Build shell page with light-blue layout and 24-module dual-grid navigation.
5. Build unified preview area.
6. Build unified rich media editor.
7. Connect first batch of modules: welcome, verify, auto reply.
8. Connect remaining modules incrementally.
9. Run local validation for auth, API, storage sync, and page rendering.

## Testing Strategy
- unit tests for storage mapping and permission checks
- API tests for auth/session and module save/load
- frontend smoke tests for login shell, group switch, save, and preview refresh
- local Telegram regression to confirm bot and web read the same values
