# UN Website Boilerplate (with Auth)

A Next.js template with UN branding and magic link authentication.

Based on: https://github.com/kleinlennart/un-website-boilerplate

## Branches

This template has two variants:

| Branch | Description |
|--------|-------------|
| `main` | Static site, no authentication |
| `template-with-auth` | Adds magic link auth, PostgreSQL integration, entity/document search |

When creating from this template, check "Include all branches" to get both. Then switch if needed:

```bash
git checkout template-with-auth
```

## Features

- UN branding (logo, colors, Roboto font)
- Magic link authentication (configurable email domains via DB)
- Rate limiting on magic link requests (2 min cooldown)
- 30-day session duration
- PostgreSQL session/user storage
- Configurable database schema per app
- Entity selection on first login (with "Other" option)
- Entity change dialog (click entity badge in header)
- Public landing page (`/about`) + protected dashboard (`/`)

## Setup

### 1. Install dependencies

```bash
pnpm install
```

### 2. Configure environment

```bash
cp .env.template .env.local
```

Edit `.env.local`:
- `DATABASE_URL` - PostgreSQL connection string
- `DB_SCHEMA` - Schema for auth tables (e.g. `myapp` → `myapp.users`, `myapp.magic_tokens`)
- `AUTH_SECRET` - Generate with `openssl rand -hex 32`
- `SMTP_*` - Mail server for magic links
- `BASE_URL` - Your app URL (for magic link emails)

### 3. Create database tables

Edit `sql/auth_tables.sql` and replace `myapp` with your schema name (must match `DB_SCHEMA`), then:

```bash
psql $DATABASE_URL -f sql/auth_tables.sql
```

The schema includes an `allowed_domains` table pre-populated with UN system domains (un.org, undp.org, unicef.org, who.int, etc.). Edit the SQL to remove domains you don't need, or add custom ones:

```sql
-- Add a custom domain
INSERT INTO myapp.allowed_domains (entity, domain) VALUES
  ('*', 'example.org'),        -- global: allow for all entities
  ('PARTNER', 'partner.org');  -- entity-specific
```

### 4. Run

```bash
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser.

## Auth Flow

1. User visits `/about` (public landing page)
2. User clicks "Sign In" → `/login`
3. User enters email, magic link sent (rate limited: 2 min cooldown)
4. User clicks link → `/verify?token=...`
5. First login: select entity (combobox with "Other" option); returning users: direct sign-in
6. Session cookie set (30 days)
7. Header shows user email, clickable entity badge (to change), and logout
8. Unauthenticated users accessing protected routes → redirect to `/about`

## Customization

- **Site title/subtitle**: Edit `SITE_TITLE` and `SITE_SUBTITLE` in `src/components/Header.tsx`
- **Allowed email domains**: Add to `allowed_domains` table in database
- **Entity list**: Query in `fetchEntities()` in `src/app/api/entities/route.ts`
- **Document search**: Query in `src/app/api/documents/search/route.ts`
- **Protected routes**: Edit `PUBLIC_PATHS` in `src/middleware.ts`
- **Auth schema**: Set `DB_SCHEMA` env var and update `sql/auth_tables.sql`

## File Structure

```
src/
├── app/
│   ├── about/                # Public landing page
│   ├── api/
│   │   ├── auth/             # Auth API routes (backup, actions preferred)
│   │   ├── documents/search/ # Document search
│   │   └── entities/         # Entity list + fetchEntities()
│   ├── login/                # Login page + layout
│   ├── verify/               # Token verification + entity selection
│   └── page.tsx              # Protected dashboard
├── components/
│   ├── DocumentSearch.tsx    # Document autocomplete
│   ├── EntityChangeDialog.tsx # Dialog to change entity
│   ├── EntityCombobox.tsx    # Entity dropdown with "Other" option
│   ├── EntitySearch.tsx      # Entity autocomplete (for search)
│   ├── Footer.tsx            # Site footer
│   ├── Header.tsx            # Site header with maxWidth, hideAbout props
│   ├── LoginForm.tsx         # Login form (uses server actions)
│   ├── UserMenu.tsx          # Email + entity badge + logout
│   └── VerifyForm.tsx        # Verify form with returning user detection
├── lib/
│   ├── actions.ts            # Server actions for auth
│   ├── auth.ts               # Auth logic (isAllowedDomain, sessions, etc.)
│   ├── config.ts             # DB_SCHEMA config + table names
│   ├── db.ts                 # PostgreSQL pool
│   ├── mail.ts               # Magic link emails
│   └── utils.ts              # Tailwind cn() helper
└── middleware.ts             # Route protection
sql/
└── auth_tables.sql           # Database schema (users, tokens, allowed_domains)
```

## Maintenance

### Check for issues
```bash
pnpm audit         # Security vulnerabilities
pnpm outdated      # Outdated packages
pnpm lint          # ESLint errors
pnpm tsc --noEmit  # TypeScript errors
```

### Update packages
```bash
pnpm update                                             # Safe patch/minor updates
pnpm add next@latest eslint-config-next@latest           # Update Next.js
```

### Clean install (if issues occur)
```bash
rm -rf node_modules .next && pnpm install
```

## Good to know

- use `npx shadcn@latest add <component-name>` when you need to add components.

- https://nextjs.org/docs/app/api-reference/file-conventions/src-folder
- https://nextjs.org/docs/app/getting-started/project-structure

- The `/public` directory should remain in the root of your project.
- Config files like `package.json`, `next.config.js` and `tsconfig.json` should remain in the root of your project.
- `.env.*` files should remain in the root of your project.

- [Next.js Documentation](https://nextjs.org/docs)
- [Learn Next.js](https://nextjs.org/learn)
