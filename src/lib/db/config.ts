// App namespace/schema for database tables
export const DB_SCHEMA = process.env.DB_SCHEMA || "app";

// Table names with schema prefix
export const tables = {
  users: `${DB_SCHEMA}.users`,
  magic_tokens: `${DB_SCHEMA}.magic_tokens`,
  allowed_domains: `${DB_SCHEMA}.allowed_domains`,
} as const;
